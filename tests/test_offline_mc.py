"""Confirmed ledger remaining can feed offline MC without the 16-card exact cap."""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
from blackjack_lab.analysis.offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA
from blackjack_lab.analysis.research_windows import build_offline_mc_input, build_predeal_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
from blackjack_lab.ui.controller import SessionController


class LedgerOfflineMcTest(unittest.TestCase):
    def test_full_shoe_exact_stays_capped_offline_mc_runs(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "offline.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        with self.assertRaises(Exception) as caught:
            build_predeal_input(ctrl.ledger)
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", caught.exception.code)
        snapshot = build_offline_mc_input(
            ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=16, seed=7)
        self.assertEqual(312, snapshot.physical_remaining)
        self.assertEqual(312, len(snapshot.pack))
        self.assertEqual(sum(snapshot.counts), 312)
        self.assertEqual("ledger-prefix", __import__("json").loads(snapshot.information_json)["source"])
        result = calculate(snapshot, budget_seconds=15)
        self.assertEqual(OFFLINE_MC_RESULT_SCHEMA, result["schema"])
        self.assertEqual("available", result["status"])
        self.assertIsNotNone(result["ev"])
        self.assertEqual("hoeffding_fixed_n_finite_family_v1", result["ci_method"])
        self.assertFalse(result["timely"])
        self.assertTrue(result["not_exact_optimal"])
        self.assertTrue(result["not_a_reliable_window_claim"])
        self.assertEqual(snapshot.prefix_digest, result["ledger_prefix_digest"])
        self.assertEqual("ledger-prefix", result["source_mode"])
        store = AnalysisSnapshots(Path(tmp.name) / "snaps")
        saved = store.save(result)
        loaded = store.load(saved["snapshot_id"])
        self.assertEqual(result["input_digest"], loaded["result"]["input_digest"])
        again = ctrl.recompute_input(saved)
        self.assertEqual(snapshot.input_digest, again.input_digest)

    def test_unknown_cards_are_not_averaged_into_a_pack(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "unknown.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        ctrl.start_round(["玩家1"])
        ctrl.deal_unknown("庄家")
        with self.assertRaises(Exception) as exact:
            build_predeal_input(ctrl.ledger)
        with self.assertRaises(Exception) as offline:
            build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        self.assertIn(exact.exception.code, ("ROUND_ALREADY_DEALT", "COMPOSITION_UNKNOWN", "RECORD_GAP"))
        self.assertEqual(exact.exception.code, offline.exception.code)


def _three_confirmed_rounds(n_decks=6):
    from blackjack_lab.analysis.shoe_event_draft import add_event, confirm_events, empty_draft
    draft = empty_draft(
        role="development", filename=f"three-round-{n_decks}d-fixture.mp4")
    add_event(draft, "burn", status="confirmed", count=0)
    hands = (
        (("玩家1", "K"), ("庄家", "10"), ("玩家1", "6"), ("庄家", "9")),
        (("玩家1", "A"), ("庄家", "10"), ("玩家1", "8"), ("庄家", "7")),
        (("玩家1", "5"), ("庄家", "K"), ("玩家1", "10"), ("庄家", "8")),
    )
    ids = []
    for index, cards in enumerate(hands, start=1):
        for seat, rank in cards:
            event = add_event(
                draft, "deal", round_id=f"round-{index}", rank=rank, seat=seat, status="draft")
            ids.append(event["event_id"])
    confirm_events(draft, ids, confirmed_by="Shawn")
    return draft


class OfflineResearchSampleTest(unittest.TestCase):
    def test_three_confirmed_rounds_import_freeze_and_recompute_after_correction(self):
        from blackjack_lab.analysis.shoe_windows import full_pack
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for decks in (6, 7, 8):
            ctrl = SessionController(Path(tmp.name) / f"sample-{decks}.db")
            self.addCleanup(ctrl.close)
            ctrl.new_shoe(research_rules(decks, surrender=None))
            self.assertEqual(len(full_pack(decks)), ctrl.state().current.shoe.physical_remaining())
            result = apply_event_draft(ctrl, _three_confirmed_rounds(decks), seat="玩家1")
            self.assertFalse(result["accepted"])
            self.assertEqual(12, result["card_dealt"])
            remaining = ctrl.state().current.shoe.physical_remaining()
            self.assertEqual(len(full_pack(decks)) - 12, remaining)
            snapshot = build_offline_mc_input(
                ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=12, seed=3)
            self.assertEqual(remaining, snapshot.physical_remaining)
            self.assertTrue(result["offline_mc_ready"])

        ctrl = SessionController(Path(tmp.name) / "sample-flow.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        apply_event_draft(ctrl, _three_confirmed_rounds(6), seat="玩家1")
        snapshot = build_offline_mc_input(
            ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=12, seed=5)
        result = calculate(snapshot, budget_seconds=15)
        self.assertEqual(OFFLINE_MC_RESULT_SCHEMA, result["schema"])
        saved = ctrl.analysis_store.save(result)
        session_id = ctrl.session_id
        ctrl.close()
        restarted = SessionController.recover(Path(tmp.name) / "sample-flow.db", session_id)
        self.addCleanup(restarted.close)
        loaded = restarted.analysis_store.load(saved["snapshot_id"])
        self.assertEqual(result["input_digest"], loaded["result"]["input_digest"])
        again = restarted.recompute_input(saved)
        self.assertEqual(snapshot.input_digest, again.input_digest)
        dealt = [event for event in restarted.ledger.events if event.etype == CARD_DEALT]
        restarted.correct(dealt[0].event_id, {"rank": "A"}, "样板纠错：第一张改为A")
        current = build_offline_mc_input(
            restarted.ledger, policy=POLICY_ALWAYS_STAND, n_samples=12, seed=5)
        self.assertNotEqual(snapshot.prefix_digest, current.prefix_digest)
        newer = restarted.analysis_store.save(calculate(current, budget_seconds=15), saved["snapshot_id"])
        self.assertNotEqual(saved["snapshot_id"], newer["snapshot_id"])
        old = restarted.analysis_store.load(saved["snapshot_id"])
        self.assertEqual(result["input_digest"], old["result"]["input_digest"])

    def test_observation_gap_explains_unavailable_but_recording_continues(self):
        from blackjack_lab.analysis.shoe_event_draft import add_event, empty_draft
        from blackjack_lab.ledger.draft_import import apply_event_draft
        from blackjack_lab.ledger.events import CARD_DEALT
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "gap-continue.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6, surrender=None))
        draft = empty_draft(role="development", filename="gap-continue.mp4")
        add_event(draft, "deal", round_id="round-1", rank=None, status="unknown_kept", seat="玩家1")
        imported = apply_event_draft(ctrl, draft, seat="玩家1")
        self.assertFalse(imported["offline_mc_ready"])
        with self.assertRaises(Exception) as caught:
            build_offline_mc_input(ctrl.ledger, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1)
        self.assertIn(caught.exception.code, {
            "PRIOR_ROUND_OBSERVATION", "RECORD_GAP", "COMPOSITION_UNKNOWN", "BURN_COUNT_UNKNOWN",
        })
        ctrl.start_round(["玩家1"])
        ctrl.deal_shown("玩家1", "9")
        self.assertTrue(any(event.etype == CARD_DEALT for event in ctrl.ledger.events))

