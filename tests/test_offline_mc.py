"""Confirmed ledger remaining can feed offline MC without the 16-card exact cap."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
from blackjack_lab.analysis.offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA
from blackjack_lab.analysis.offline_research_sample import (
    EXPECTED_REMAINING, run_offline_research_sample, three_confirmed_rounds_draft,
)
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


class OfflineResearchSampleTest(unittest.TestCase):
    def test_three_confirmed_rounds_import_freeze_and_recompute_after_correction(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        report = run_offline_research_sample(Path(tmp.name) / "pack", n_samples=12, seed=3)
        self.assertTrue(report["ok"], report)
        sample_path = Path(report["sample_run"])
        self.assertTrue(sample_path.is_file())
        body = json.loads(sample_path.read_text(encoding="utf-8"))
        self.assertEqual(
            [{"n_decks": decks, "physical_remaining": remaining}
             for decks, remaining in EXPECTED_REMAINING.items()],
            body["expected_remaining"])
        self.assertTrue(body["not_a_reliable_window_claim"])
        self.assertFalse(body["accepted"])
        by_name = {step["step"]: step for step in body["steps"]}
        for decks, remaining in EXPECTED_REMAINING.items():
            step = by_name[f"{decks}-deck-three-round"]
            self.assertEqual(12, step["card_dealt"])
            self.assertEqual(remaining, step["physical_remaining"])
            self.assertTrue(step["offline_mc_ready"])
            self.assertTrue(Path(step["result_path"]).is_file())
        self.assertTrue(by_name["restart-recompute-correction"]["ok"])
        self.assertTrue(by_name["observation-gap-control"]["ok"])
        self.assertNotEqual(
            by_name["restart-recompute-correction"]["old_snapshot_id"],
            by_name["restart-recompute-correction"]["new_snapshot_id"])

    def test_fixture_coverage_is_not_a_live_video_attestation(self):
        draft = three_confirmed_rounds_draft(6)
        self.assertEqual(3, len(draft.get("round_coverage") or {}))
        for record in draft["round_coverage"].values():
            self.assertTrue(record.get("synthetic_fixture"))
            self.assertIn("not a real-video", record.get("notes", "").lower())

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

