"""Append-only missed-deal repair: suffix preview, atomic commit, historical isolation."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL
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
            "repair_anchor_id": "anchor", "first_readable_at": 1.5, "occurred_at": 1.0,
        })
        with self.assertRaises(ValueError):
            Event(CARD_DEALT, {"seat": "玩家1", "rank": "A", "face_state": "shown", "nope": 1})

    def test_capability_matrix_is_experimental_not_full_insert_anywhere(self):
        status, note = CAPABILITY_MATRIX["回溯插入漏牌的原子修复"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("历史前缀", note)
        self.assertIn("纠错", note)


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
