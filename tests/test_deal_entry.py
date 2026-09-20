"""Fixed initial-deal queue: slot counts, skip empty seats, no guessing."""
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.core.table import DEALER
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import (
    MODE_CONTINUATION, MODE_INITIAL, MODE_MANUAL, MODE_UNALIGNED,
    RoundEntryPlan, build_initial_slots, participating_in_order,
)


class TestDealEntryPlan(unittest.TestCase):
    def test_slot_counts_for_one_to_seven_players(self):
        for n in range(1, 8):
            seats = [f"玩家{i}" for i in range(1, n + 1)]
            slots = build_initial_slots(seats)
            self.assertEqual(len(slots), 2 * n + 2)
            self.assertEqual(sum(1 for s in slots if s.expected_face == "shown"), 2 * n + 1)
            self.assertEqual(sum(1 for s in slots if s.role == "dealer_hole"), 1)
            first = [s.seat for s in slots if s.role == "player_first"]
            second = [s.seat for s in slots if s.role == "player_second"]
            self.assertEqual(first, second)
            self.assertEqual(first, seats)

    def test_three_player_order(self):
        slots = build_initial_slots(["玩家1", "玩家2", "玩家3"])
        self.assertEqual([s.seat for s in slots], [
            "玩家1", "玩家2", "玩家3", DEALER,
            "玩家1", "玩家2", "玩家3", DEALER,
        ])
        self.assertEqual(slots[-1].expected_face, "hidden")

    def test_empty_seats_keep_fixed_numbers(self):
        ordered = participating_in_order(["玩家5", "玩家1", "玩家3"])
        self.assertEqual(ordered, ("玩家1", "玩家3", "玩家5"))
        slots = build_initial_slots(ordered)
        self.assertEqual([s.seat for s in slots if s.role == "player_first"],
                         ["玩家1", "玩家3", "玩家5"])

    def test_reverse_deal_direction(self):
        ordered = participating_in_order(["玩家1", "玩家3", "玩家5"], "reverse")
        self.assertEqual(ordered, ("玩家5", "玩家3", "玩家1"))

    def test_freeze_and_auto_advance(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1", "玩家2", "玩家3"], my_seat="玩家3")
        self.assertEqual(plan.mode, MODE_INITIAL)
        self.assertEqual(plan.slot().seat, "玩家1")
        plan.mark_filled(plan.cursor_slot_id, "e1", "T")
        plan.advance_after_initial_success()
        self.assertEqual(plan.slot().seat, "玩家2")
        self.assertEqual(plan.last_saved.seat, "玩家1")

    def test_enter_skips_unfilled_and_pauses(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1", "玩家2"], my_seat="玩家1")
        skipped = plan.slot().slot_id
        nxt = plan.navigate(1)
        self.assertTrue(plan.paused)
        self.assertEqual(plan.mode, MODE_MANUAL)
        self.assertIn(skipped, plan.unresolved_slots)
        self.assertEqual(nxt.seat, "玩家2")
        self.assertNotIn(skipped, plan.filled_slots)

    def test_hole_slot_rejects_shown_rank(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1"], my_seat="玩家1")
        plan.cursor_slot_id = plan.hole_slot().slot_id
        with self.assertRaises(ValueError):
            plan.accept_shown_on_cursor()
        self.assertEqual(plan.accept_hole_on_cursor().role, "dealer_hole")

    def test_continuation_stays_on_current_hand(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1", "玩家2"], my_seat="玩家1")
        plan.enter_continuation("玩家1", "h1", 1)
        plan.record_continuation_card("玩家1", "e3", "3")
        plan.record_continuation_card("玩家1", "e2", "2")
        self.assertEqual(plan.mode, MODE_CONTINUATION)
        self.assertEqual(plan.continuation_seat, "玩家1")
        plan.jump_seat("玩家2")
        self.assertEqual(plan.continuation_seat, "玩家1")

    def test_undo_restores_slot(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1", "玩家2"], my_seat="玩家1")
        first = plan.cursor_slot_id
        plan.mark_filled(first, "e1", "T")
        plan.advance_after_initial_success()
        restored = plan.undo_event("e1")
        self.assertEqual(restored.slot_id, first)
        self.assertEqual(plan.cursor_slot_id, first)
        self.assertEqual(plan.mode, MODE_INITIAL)
        self.assertNotIn(first, plan.filled_slots)

    def test_missing_plan_does_not_guess(self):
        plan = RoundEntryPlan.unaligned(session_id="s")
        self.assertEqual(plan.mode, MODE_UNALIGNED)
        self.assertEqual(plan.slots, ())
        self.assertIsNone(plan.current_recording_seat())

    def test_reconcile_drops_voided_events_only(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1"], my_seat="玩家1")
        first = plan.cursor_slot_id
        plan.mark_filled(first, "gone", "T")
        plan.reconcile(["still-there"])
        self.assertNotIn(first, plan.filled_slots)
        self.assertIn(first, plan.unresolved_slots)

    def test_prompt_keeps_analysis_identity(self):
        plan = RoundEntryPlan.freeze(
            session_id="s", shoe_id="shoe", round_id="r",
            selected_seats=["玩家1", "玩家2", "玩家3"], my_seat="玩家3")
        text = plan.prompt("玩家1", "玩家3")
        self.assertIn("录入目标：玩家1", text)
        self.assertIn("分析对象：我／玩家3", text)
        self.assertIn("初始发牌 · 第一遍", text)


class TestEntryPlanPersistence(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "plan.db"

    def test_next_round_keeps_old_plan_file_and_events(self):
        ctrl = SessionController(self.db)
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(RuleProfile(n_decks=6))
        ctrl.start_round(["玩家1", "玩家2", "玩家3"], my_seat="玩家3")
        first = ctrl.deal_shown("玩家1", "T")
        ctrl.entry_plan.mark_filled(ctrl.entry_plan.cursor_slot_id, first.event_id, "T")
        ctrl.entry_plan.advance_after_initial_success()
        ctrl.save_entry_plan()
        old_path = ctrl._entry_plan_path()
        old_round = ctrl.entry_plan.round_id
        old_seats = ctrl.entry_plan.participating_seats
        old_filled = dict(ctrl.entry_plan.filled_slots)
        ctrl.end_round_unsettled("下一轮换座位，不改写旧轮", observation_status="unknown")
        ctrl.start_round(["玩家1", "玩家3"], my_seat="玩家1")
        self.assertEqual(ctrl.entry_plan.participating_seats, ("玩家1", "玩家3"))
        self.assertEqual(len(ctrl.entry_plan.slots), 6)
        self.assertNotEqual(ctrl.entry_plan.round_id, old_round)
        retained = json.loads(old_path.read_text(encoding="utf-8"))
        self.assertEqual(tuple(retained["participating_seats"]), old_seats)
        self.assertEqual(retained["filled_slots"], old_filled)
        self.assertEqual(retained["round_id"], old_round)
        live = ctrl.ledger.replay()
        first_round = next(ev for ev in ctrl.ledger.events if ev.event_id == first.event_id)
        self.assertEqual(first_round.payload["seat"], "玩家1")
        self.assertEqual(first_round.round_id, old_round)

    def test_recover_missing_plan_does_not_guess(self):
        ctrl = SessionController(self.db)
        ctrl.new_shoe(RuleProfile(n_decks=6))
        ctrl.start_round(["玩家1", "玩家3"], my_seat="玩家1")
        event = ctrl.deal_shown("玩家1", "7")
        ctrl.entry_plan.mark_filled(ctrl.entry_plan.slots[0].slot_id, event.event_id, "7")
        ctrl.entry_plan.advance_after_initial_success()
        ctrl.save_entry_plan()
        session = ctrl.session_id
        path = ctrl._entry_plan_path()
        ctrl.close()
        recovered = SessionController.recover(self.db, session)
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.entry_plan.mode, MODE_INITIAL)
        self.assertEqual(recovered.entry_plan.participating_seats, ("玩家1", "玩家3"))
        self.assertEqual(len(recovered.entry_plan.filled_slots), 1)
        path.unlink()
        recovered.close()
        missing = SessionController.recover(self.db, session)
        self.addCleanup(missing.close)
        self.assertEqual(missing.entry_plan.mode, MODE_UNALIGNED)
        self.assertEqual(missing.entry_plan.slots, ())
        self.assertIsNone(missing.entry_plan.current_recording_seat())


if __name__ == "__main__":
    unittest.main()
