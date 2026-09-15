"""Append-only missed-deal repair: suffix preview, atomic commit, historical isolation."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL
from blackjack_lab.analysis.split_contracts import split_research_rules
from blackjack_lab.core.table import ACTION_STAND, DEALER
from blackjack_lab.ledger.events import CARD_DEALT, Event, SOURCE_REPAIR
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ledger.repair import (
    PLAN_NOTE, RepairError, apply_repair, inserted_visible_at, plan_missed_deal,
)
from blackjack_lab.storage.database import SCHEMA_VERSION, LocalStore
from blackjack_lab.ui.controller import SessionController


def _research_ledger():
    ledger = EventLedger("repair-session")
    ledger.start_session("synthetic repair fixture")
    ledger.create_shoe(research_rules(6))
    ledger.start_round(["玩家1"])
    return ledger


class LedgerRepairTest(unittest.TestCase):
    def test_insert_dealer_up_after_first_player_card(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        second = ledger.deal("玩家1", "6")
        hole = ledger.deal(DEALER, None, hidden=True)
        original = ledger.to_list()
        original_end = hole.seq
        plan = plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9",
                                reason="漏记庄家明牌", confirmed_at=1_800_000_000.0)
        apply_repair(ledger, plan)
        self.assertEqual(original, ledger.to_list()[:len(original)])
        self.assertTrue(any(e.event_id == first.event_id for e in ledger.events))
        self.assertTrue(any(e.event_id == second.event_id for e in ledger.events))
        self.assertTrue(any(e.event_id == hole.event_id for e in ledger.events))

        historical = ledger.replay(through_seq=original_end)
        hist_player = historical.current.table.players["玩家1"].hands[0].ranks
        hist_dealer = [c.rank for c in historical.current.table.dealer.hands[0].cards]
        self.assertEqual(hist_player, ["10", "6"])
        self.assertNotIn("9", hist_dealer)
        self.assertFalse(inserted_visible_at(ledger, original_end, plan.batch_id))

        current = ledger.replay().current
        self.assertEqual(current.table.players["玩家1"].hands[0].ranks, ["10", "6"])
        self.assertEqual(current.table.dealer.hands[0].cards[0].rank, "9")
        self.assertTrue(inserted_visible_at(ledger, ledger.events[-1].seq, plan.batch_id))

        inserted = [e for e in ledger.events if e.payload.get("repair_role") == "inserted_missed"][0]
        self.assertEqual(inserted.source, SOURCE_REPAIR)
        self.assertEqual(inserted.observed_at, 1_800_000_000.0)
        self.assertGreater(inserted.seq, original_end)
        self.assertIn("当时已经抓到窗口", plan.note)
        self.assertEqual(PLAN_NOTE, plan.note)

    def test_as_of_analysis_cannot_use_late_card(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        hole = ledger.deal(DEALER, None, hidden=True)
        original_end = hole.seq
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9"))
        current_input = build_input(ledger, "玩家1")
        self.assertEqual(current_input.dealer_up, 9)
        self.assertEqual(current_input.player_ranks, ("10", "6"))
        with self.assertRaises(Exception) as caught:
            build_input(ledger, "玩家1", through_seq=original_end)
        self.assertEqual(caught.exception.code, "DEALER_INFORMATION")

    def test_later_action_suffix_is_replayed(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        stand = ledger.player_action(
            "玩家1", ledger.replay().current.table.players["玩家1"].hands[0].hand_id, ACTION_STAND)
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9"))
        current = ledger.replay().current
        self.assertTrue(current.table.players["玩家1"].hands[0].stood)
        self.assertEqual(current.table.dealer.hands[0].cards[0].rank, "9")
        historical = ledger.replay(through_seq=stand.seq)
        self.assertTrue(historical.current.table.players["玩家1"].hands[0].stood)
        self.assertNotEqual(historical.current.table.dealer.hands[0].cards[0].rank, "9")

    def test_control_suffix_is_refused(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        second = ledger.deal("玩家1", "6")
        ledger.correct(second.event_id, {"rank": "7"}, reason="看错牌面")
        with self.assertRaises(RepairError) as caught:
            plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        self.assertEqual(caught.exception.code, "REPAIR_CONTROL_SUFFIX")
        self.assertEqual(len(ledger.events), 6)

    def test_illegal_suffix_leaves_ledger_unchanged(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        before = ledger.to_list()
        with self.assertRaises(RepairError) as caught:
            plan_missed_deal(ledger, first.event_id, seat="玩家2", rank="5")
        self.assertEqual(caught.exception.code, "SUFFIX_REPLAY_FAILED")
        self.assertEqual(before, ledger.to_list())

    def test_payload_extension_is_optional_not_schema_change(self):
        self.assertEqual(SCHEMA_VERSION, 2)
        Event(CARD_DEALT, {
            "seat": "玩家1", "rank": "A", "face_state": "shown",
            "repair_batch_id": "batch", "repair_role": "inserted_missed",
            "repair_anchor_id": "anchor", "repair_original_event_id": "orig",
            "first_readable_at": 1.5, "occurred_at": 1.0,
        })
        with self.assertRaises(ValueError):
            Event(CARD_DEALT, {"seat": "玩家1", "rank": "A", "face_state": "shown", "nope": 1})

    def test_capability_matrix_is_experimental_not_full_insert_anywhere(self):
        status, note = CAPABILITY_MATRIX["回溯插入漏牌的原子修复"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("历史前缀", note)
        self.assertIn("纠错", note)
        self.assertIn("跨轮", note)
        self.assertIn("确认时间不回溯", note)


class RepairPersistenceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "repair.db"

    def test_atomic_commit_does_not_keep_partial_undos(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        store = LocalStore(self.path)
        self.addCleanup(store.close)
        store.save_ledger(ledger)
        plan = plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        candidate = apply_repair(copy.deepcopy(ledger), plan)
        original_insert = store._insert
        new_writes = {"n": 0}

        def flaky(event):
            inserted = original_insert(event)
            if inserted:
                new_writes["n"] += 1
                if new_writes["n"] >= 2:
                    raise RuntimeError("disk full")
            return inserted

        with patch.object(store, "_insert", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                store.save_ledger(candidate)
        loaded = store.load_ledger(ledger.session_id)
        self.assertEqual(loaded.to_list(), ledger.to_list())
        self.assertFalse(any(e.etype == "UNDO" for e in loaded.events))
        self.assertEqual(store.get_meta("schema_version"), "2")

    def test_controller_commit_publishes_repaired_state(self):
        ctrl = SessionController(self.path)
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        ctrl.start_round(["玩家1"])
        first = ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.deal_hidden(DEALER)
        original_count = len(ctrl.ledger.events)
        revision = ctrl.commit_revision
        ctrl.repair_missed_deal(first.event_id, DEALER, "9", reason="漏记庄家明牌")
        self.assertGreater(ctrl.commit_revision, revision)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), ctrl.ledger.to_list())
        self.assertGreater(len(ctrl.ledger.events), original_count)
        self.assertEqual(ctrl.ledger.replay().current.table.dealer.hands[0].cards[0].rank, "9")
        with self.assertRaises(LedgerError):
            ctrl.commit_repair(ctrl.preview_missed_deal(first.event_id, DEALER, "8"))

    def test_commit_save_failure_does_not_replace_live_ledger(self):
        ctrl = SessionController(self.path)
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        ctrl.start_round(["玩家1"])
        first = ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.deal_hidden(DEALER)
        before = ctrl.ledger.to_list()
        revision = ctrl.commit_revision
        with patch.object(ctrl.store, "save_ledger", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                ctrl.repair_missed_deal(first.event_id, DEALER, "9", reason="漏记庄家明牌")
        self.assertEqual(before, ctrl.ledger.to_list())
        self.assertEqual(revision, ctrl.commit_revision)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertFalse(any(event.etype == "UNDO" for event in ctrl.ledger.events))
        self.assertEqual(ctrl.store.get_meta("schema_version"), "2")


class RepairBoundaryTest(unittest.TestCase):
    def test_ledger_change_invalidates_an_unapplied_plan(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        plan = plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        ledger.deal("玩家1", "5")
        with self.assertRaises(RepairError) as caught:
            apply_repair(ledger, plan)
        self.assertEqual("STALE_PLAN", caught.exception.code)

    def test_same_plan_cannot_apply_twice(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        plan = plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        apply_repair(ledger, plan)
        with self.assertRaises(RepairError) as caught:
            apply_repair(ledger, plan)
        self.assertEqual("STALE_PLAN", caught.exception.code)

    def test_two_tens_keep_distinct_identities_after_insert(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        second = ledger.deal("玩家1", "10")
        hole = ledger.deal(DEALER, None, hidden=True)
        self.assertNotEqual(first.event_id, second.event_id)
        original_end = hole.seq
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9"))
        historical = ledger.replay(through_seq=original_end)
        hist_player = historical.current.table.players["玩家1"].hands[0].ranks
        self.assertEqual(hist_player, ["10", "10"])
        current = ledger.replay().current
        self.assertEqual(current.table.players["玩家1"].hands[0].ranks, ["10", "10"])
        self.assertEqual(current.table.dealer.hands[0].cards[0].rank, "9")

    def test_reveal_in_suffix_does_not_rewrite_historical_prefix(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        up = ledger.deal(DEALER, "9")
        hole = ledger.deal(DEALER, None, hidden=True)
        ledger.reveal(hole.event_id, "8")
        original_end = up.seq
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat="玩家1", rank="5"))
        historical = ledger.replay(through_seq=original_end)
        self.assertEqual(historical.current.table.players["玩家1"].hands[0].ranks, ["10", "6"])
        current = ledger.replay().current
        self.assertEqual(current.table.players["玩家1"].hands[0].ranks, ["10", "5", "6"])
        self.assertEqual(current.table.dealer.hands[0].cards[1].rank, "8")

    def test_new_shoe_suffix_is_refused_without_deleting_the_guard(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        ledger.end_round()
        ledger.create_shoe(research_rules(6))
        with self.assertRaises(RepairError) as caught:
            plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        self.assertEqual("REPAIR_CROSSES_SHOE", caught.exception.code)

    def test_split_suffix_keeps_the_unrepaired_prefix(self):
        from blackjack_lab.core.table import ACTION_SPLIT
        ledger = EventLedger("repair-split-session")
        ledger.start_session("synthetic split repair fixture")
        ledger.create_shoe(split_research_rules(6))
        ledger.start_round(["玩家1"])
        first = ledger.deal("玩家1", "8")
        second = ledger.deal("玩家1", "8")
        hole = ledger.deal(DEALER, None, hidden=True)
        hand_id = ledger.replay().current.table.players["玩家1"].hands[0].hand_id
        split_event = ledger.player_action("玩家1", hand_id, ACTION_SPLIT)
        original = ledger.to_list()
        original_end = split_event.seq
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="6"))
        self.assertEqual(original, ledger.to_list()[:len(original)])
        historical = ledger.replay(through_seq=original_end)
        self.assertEqual(
            [hand.ranks for hand in historical.current.table.players["玩家1"].hands],
            [["8"], ["8"]])
        hist_dealer = [card.rank for card in historical.current.table.dealer.hands[0].cards]
        self.assertNotIn("6", hist_dealer)
        current = ledger.replay().current
        self.assertEqual([hand.ranks for hand in current.table.players["玩家1"].hands], [["8"], ["8"]])
        self.assertEqual(current.table.dealer.hands[0].cards[0].rank, "6")
        self.assertTrue(any(event.event_id == second.event_id for event in ledger.events))
        self.assertTrue(any(event.event_id == hole.event_id for event in ledger.events))
        self.assertTrue(any(event.payload.get("repair_role") == "replay_suffix"
                            and event.payload.get("action") == ACTION_SPLIT
                            for event in ledger.events))

    def test_replayed_events_point_to_the_original_event_ids(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, "9")
        hole = ledger.deal(DEALER, None, hidden=True)
        reveal = ledger.reveal(hole.event_id, "8")
        suffix_ids = {event.event_id for event in ledger.events if event.seq > first.seq}
        apply_repair(ledger, plan_missed_deal(ledger, first.event_id, seat="玩家1", rank="5"))
        replayed = [event for event in ledger.events
                    if event.payload.get("repair_role") == "replay_suffix"]
        pointed = {event.payload.get("repair_original_event_id") for event in replayed}
        self.assertEqual(suffix_ids, pointed)
        replayed_reveal = next(event for event in replayed if event.etype == "CARD_REVEALED")
        self.assertEqual(reveal.event_id, replayed_reveal.payload["repair_original_event_id"])
        replayed_hole = next(
            event for event in replayed
            if event.payload.get("repair_original_event_id") == hole.event_id)
        self.assertEqual(replayed_hole.event_id, replayed_reveal.payload["target_event_id"])
        self.assertNotEqual(hole.event_id, replayed_reveal.payload["target_event_id"])

    def test_saved_analysis_does_not_become_a_repair_window_hit(self):
        from blackjack_lab.analysis.service import calculate
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ctrl = SessionController(Path(tmp.name) / "repair-analysis.db")
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(research_rules(6))
        ctrl.start_round(["玩家1"])
        first = ctrl.deal_shown("玩家1", "10")
        ctrl.deal_shown("玩家1", "6")
        ctrl.deal_shown(DEALER, "9")
        ctrl.deal_hidden(DEALER)
        result = calculate(ctrl.analysis_input("玩家1"))
        self.assertEqual("available", result["status"])
        stand_before = result["actions"]["stand"]["ev"]
        saved = ctrl.analysis_store.save(result)
        digest_before = saved["result"]["input_digest"]
        through = saved["result"]["input"]["through_seq"]
        ctrl.repair_missed_deal(first.event_id, "玩家1", "5")
        loaded = ctrl.analysis_store.load(saved["snapshot_id"])
        self.assertEqual(digest_before, loaded["result"]["input_digest"])
        self.assertAlmostEqual(stand_before, loaded["result"]["actions"]["stand"]["ev"])
        self.assertEqual(through, loaded["result"]["input"]["through_seq"])
        rebuilt = calculate(ctrl.recompute_input(loaded))
        self.assertEqual(digest_before, rebuilt["input_digest"])
        self.assertAlmostEqual(stand_before, rebuilt["actions"]["stand"]["ev"])
        current = calculate(ctrl.analysis_input("玩家1"))
        self.assertNotEqual(digest_before, current["input_digest"])
        self.assertEqual(("10", "5", "6"), current["input"]["player_ranks"])

    def test_occurred_at_is_not_treated_as_then_confirmed(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        plan = plan_missed_deal(
            ledger, first.event_id, seat=DEALER, rank="9",
            occurred_at=1.0, first_readable_at=1.5, confirmed_at=1_800_000_000.0)
        apply_repair(ledger, plan)
        inserted = next(event for event in ledger.events
                        if event.payload.get("repair_role") == "inserted_missed")
        self.assertEqual(1_800_000_000.0, inserted.observed_at)
        self.assertEqual(1.0, inserted.payload.get("occurred_at"))
        self.assertEqual(1.5, inserted.payload.get("first_readable_at"))
        self.assertGreater(inserted.observed_at, inserted.payload["occurred_at"])
        self.assertFalse(inserted_visible_at(ledger, first.seq, plan.batch_id))
        historical = ledger.replay(through_seq=first.seq)
        self.assertEqual(["10"], historical.current.table.players["玩家1"].hands[0].ranks)

    def test_cross_round_suffix_keeps_round_one_prefix(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, "9")
        ledger.deal(DEALER, None, hidden=True)
        hand_id = ledger.replay().current.table.players["玩家1"].hands[0].hand_id
        stand = ledger.player_action("玩家1", hand_id, ACTION_STAND)
        ended = ledger.end_round()
        original = ledger.to_list()
        original_end = ended.seq
        round_two = ledger.start_round(["玩家1"])
        later = ledger.deal("玩家1", "8")
        fives_before = ledger.replay().current.shoe.remaining.get("5")
        plan = plan_missed_deal(ledger, first.event_id, seat="玩家1", rank="5")
        apply_repair(ledger, plan)
        self.assertEqual(original, ledger.to_list()[:len(original)])
        historical = ledger.replay(through_seq=original_end)
        self.assertEqual(1, historical.current.table.round_no)
        self.assertEqual(["10", "6"], historical.current.table.players["玩家1"].hands[0].ranks)
        self.assertFalse(inserted_visible_at(ledger, original_end, plan.batch_id))
        replayed_end = next(
            event for event in ledger.events
            if event.payload.get("repair_original_event_id") == ended.event_id)
        repaired_round_one = ledger.replay(through_seq=replayed_end.seq)
        self.assertEqual(["10", "5", "6"], repaired_round_one.current.table.players["玩家1"].hands[0].ranks)
        current = ledger.replay().current
        self.assertEqual(2, current.table.round_no)
        self.assertEqual(["8"], current.table.players["玩家1"].hands[0].ranks)
        self.assertEqual(fives_before - 1, current.shoe.remaining.get("5"))
        self.assertTrue(any(event.event_id == later.event_id for event in ledger.events))
        self.assertTrue(any(event.payload.get("repair_original_event_id") == stand.event_id
                            for event in ledger.events))
        self.assertTrue(any(event.payload.get("repair_original_event_id") == round_two.event_id
                            and event.etype == "ROUND_STARTED"
                            for event in ledger.events))

    def test_failed_apply_does_not_keep_partial_undos(self):
        ledger = _research_ledger()
        first = ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.deal(DEALER, None, hidden=True)
        plan = plan_missed_deal(ledger, first.event_id, seat=DEALER, rank="9")
        before = ledger.to_list()
        with patch("blackjack_lab.ledger.repair._replay_one",
                   side_effect=RepairError("SUFFIX_REPLAY_FAILED", "injected replay failure")):
            with self.assertRaises(RepairError) as caught:
                apply_repair(ledger, plan)
        self.assertEqual("SUFFIX_REPLAY_FAILED", caught.exception.code)
        self.assertEqual(before, ledger.to_list())
        self.assertFalse(any(event.etype == "UNDO" for event in ledger.events))


class RepairRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "repair-recover.db"

    def test_process_recovery_keeps_repaired_current_and_unrepaired_prefix(self):
        ctrl = SessionController(self.path)
        try:
            ctrl.new_shoe(research_rules(6))
            ctrl.start_round(["玩家1"])
            first = ctrl.deal_shown("玩家1", "10")
            ctrl.deal_shown("玩家1", "6")
            ctrl.deal_shown(DEALER, "9")
            ctrl.deal_hidden(DEALER)
            hand_id = ctrl.ledger.replay().current.table.players["玩家1"].hands[0].hand_id
            ctrl.player_action("玩家1", hand_id, ACTION_STAND)
            ended = ctrl.end_round_unsettled("研究夹具：跨轮修复不要求结算")
            original_end = ended.seq
            ctrl.start_round(["玩家1"])
            ctrl.deal_shown("玩家1", "8")
            session_id = ctrl.session_id
            ctrl.repair_missed_deal(first.event_id, "玩家1", "5")
        finally:
            ctrl.close()
        restored = SessionController.recover(self.path, session_id)
        self.addCleanup(restored.close)
        historical = restored.ledger.replay(through_seq=original_end)
        self.assertEqual(["10", "6"], historical.current.table.players["玩家1"].hands[0].ranks)
        self.assertEqual(1, historical.current.table.round_no)
        current = restored.ledger.replay().current
        self.assertEqual(2, current.table.round_no)
        self.assertEqual(["8"], current.table.players["玩家1"].hands[0].ranks)
        replayed_end = next(
            event for event in restored.ledger.events
            if event.payload.get("repair_original_event_id") == ended.event_id)
        repaired_round_one = restored.ledger.replay(through_seq=replayed_end.seq)
        self.assertEqual(["10", "5", "6"], repaired_round_one.current.table.players["玩家1"].hands[0].ranks)
