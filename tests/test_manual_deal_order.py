"""K3: keypad deal order, continuation stick, analysis vs recording target."""
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.information import build_input
from blackjack_lab.core.cards import TEN_BUCKET
from blackjack_lab.core.table import ACTION_SPLIT
from blackjack_lab.ui.deal_entry import MODE_CONTINUATION, MODE_INITIAL, MODE_PEEK_WAIT
from blackjack_lab.analysis.split_contracts import same_value_split_research_rules, split_research_rules
from tests.test_analysis_integration import example
from tests.test_ui_workflow import TestUIWorkflow


class TestManualDealOrder(TestUIWorkflow):
    def _join(self, *seats):
        for name, var in self.app.var_participants.items():
            var.set(name in seats)
        self.app.var_my_seat.set(seats[-1])

    def test_three_player_key_sequence(self):
        self.app.var_decks.set(6)
        self.app.act_new_shoe()
        self._join("玩家1", "玩家2", "玩家3")
        self.app.act_new_round()
        plan = self.app.ctrl.entry_plan
        self.assertEqual(len(plan.slots), 8)
        self.assertEqual(plan.mode, MODE_INITIAL)
        for rank in (TEN_BUCKET, "7", "A", "6", TEN_BUCKET):
            self.app._key_rank(rank)
        prompt = self.app.var_entry_prompt.get()
        self.assertIn("初始发牌 · 第二遍 · 进度5/8", prompt)
        self.assertIn("刚刚记入：玩家1，第2张，10点，已保存", prompt)
        self.assertIn("下一张给：玩家2，第2张", prompt)
        self.assertIn("录入目标：玩家2", prompt)
        self.assertIn("分析对象：我／玩家3", prompt)
        self.assertIn("初始槽未齐", self.app.var_topinfo.get())
        for rank in (TEN_BUCKET, "9"):
            self.app._key_rank(rank)
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks,
                         [TEN_BUCKET, TEN_BUCKET])
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家2"].hands[0].ranks,
                         ["7", TEN_BUCKET])
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家3"].hands[0].ranks,
                         ["A", "9"])
        self.assertEqual(self.app.ctrl.state().current.table.dealer.hands[0].ranks, ["6"])
        self.assertEqual(self.app.var_analysis_target.get(), "玩家3")
        self.assertIn("分析对象：我／玩家3", self.app.var_entry_prompt.get())
        before_events = len(self.app.ctrl.ledger.events)
        self.app._key_rank(TEN_BUCKET)
        self.assertEqual(len(self.app.ctrl.ledger.events), before_events)
        self.assertTrue(self.errors)
        self.errors.clear()
        self.app._key_hole()
        table = self.app.ctrl.state().current.table
        self.assertEqual(len(table.dealer.hands[0].cards), 2)
        self.assertTrue(table.dealer.hands[0].hidden_cards)
        shoe = self.app.ctrl.state().current.shoe
        self.assertEqual(shoe.physical_remaining(), 6 * 52 - 8)
        self.assertEqual(shoe.unrevealed_out, 1)
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_CONTINUATION)
        self.assertFalse(table.dealer_hole_checked_negative)
        self.assertEqual(self.errors, [])

    def test_continuation_stays_on_same_hand(self):
        self.start()
        self.app._key_rank("9")
        self.app._key_rank("8")
        self.app._key_rank("6")
        self.app._key_hole()
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_CONTINUATION)
        self.assertEqual(self.app.var_target.get(), "玩家1")
        self.app._key_rank("3")
        self.app._key_rank("2")
        ranks = self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks
        self.assertEqual(ranks, ["9", "6", "3", "2"])
        self.assertEqual(self.app.var_target.get(), "玩家1")
        before = len(self.app.ctrl.ledger.events)
        self.app._key_navigate(1)
        self.assertEqual(len(self.app.ctrl.ledger.events), before)
        self.assertEqual(self.errors, [])

    def test_peek_wait_does_not_write_negative(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app._key_rank("9")
        self.app._key_rank("A")
        self.app._key_rank("8")
        self.app._key_hole()
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.assertFalse(self.app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertIn("等待检查结果", self.app.var_entry_prompt.get())
        self.app.act_peek_negative()
        self.assertTrue(self.app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_CONTINUATION)

    def test_failed_persist_does_not_advance(self):
        self.start()
        cursor = self.app.ctrl.entry_plan.cursor_slot_id
        with patch.object(self.app.ctrl.store, "save_event", side_effect=OSError("disk")):
            self.app._key_rank(TEN_BUCKET)
        self.assertEqual(self.app.ctrl.entry_plan.cursor_slot_id, cursor)
        self.assertEqual(self.app.ctrl.entry_plan.filled_slots, {})
        self.assertEqual(self.app.var_target.get(), "玩家1")
        self.assertTrue(self.errors)

    def test_multi_player_record_keeps_single_player_analysis_gate(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self._join("玩家1", "玩家2")
        self.app.act_new_round()
        from blackjack_lab.analysis.contracts import InputUnavailable
        with self.assertRaises(InputUnavailable) as error:
            build_input(self.app.ctrl.ledger, "玩家1")
        self.assertEqual(error.exception.code, "SINGLE_PLAYER_ONLY")

    def test_empty_seats_keep_display_numbers(self):
        self.app.var_decks.set(6)
        self.app.act_new_shoe()
        self._join("玩家1", "玩家3", "玩家5")
        self.app.act_new_round()
        plan = self.app.ctrl.entry_plan
        self.assertEqual(plan.participating_seats, ("玩家1", "玩家3", "玩家5"))
        self.assertEqual([slot.seat for slot in plan.slots], [
            "玩家1", "玩家3", "玩家5", "庄家",
            "玩家1", "玩家3", "玩家5", "庄家",
        ])
        for rank in ("2", "3", "5", "6", "7", "8", "9"):
            self.app._key_rank(rank)
        table = self.app.ctrl.state().current.table
        self.assertEqual(table.players["玩家1"].hands[0].ranks, ["2", "7"])
        self.assertEqual(table.players["玩家3"].hands[0].ranks, ["3", "8"])
        self.assertEqual(table.players["玩家5"].hands[0].ranks, ["5", "9"])
        self.assertFalse(table.players["玩家2"].hands)
        self.assertFalse(table.players["玩家4"].hands)

    def test_changing_next_round_seats_does_not_rewrite_current_plan(self):
        self.app.var_decks.set(6)
        self.app.act_new_shoe()
        self._join("玩家1", "玩家2", "玩家3")
        self.app.act_new_round()
        old = self.app.ctrl.entry_plan.to_dict()
        self.app.var_participants["玩家2"].set(False)
        self.app.refresh_all()
        self.assertEqual(self.app.ctrl.entry_plan.to_dict(), old)

    def test_twenty_does_not_auto_stand_and_enter_does_not_stand(self):
        self.start()
        self.app._key_rank(TEN_BUCKET)
        self.app._key_rank("6")
        self.app._key_rank(TEN_BUCKET)
        self.app._key_hole()
        hand = self.app.ctrl.state().current.table.players["玩家1"].hands[0]
        self.assertEqual(hand.ranks, [TEN_BUCKET, TEN_BUCKET])
        self.assertFalse(hand.stood)
        self.assertFalse(hand.is_closed)
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_CONTINUATION)
        self.assertEqual(self.app.var_target.get(), "玩家1")
        before = [ev.etype for ev in self.app.ctrl.ledger.events]
        self.app._key_navigate(1)
        self.assertEqual([ev.etype for ev in self.app.ctrl.ledger.events], before)
        self.assertFalse(self.app.ctrl.state().current.table.players["玩家1"].hands[0].stood)

    def test_ctrl_jump_pauses_and_keeps_skipped_slot(self):
        self.start()
        skipped = self.app.ctrl.entry_plan.cursor_slot_id
        self.app._key_jump(3)
        plan = self.app.ctrl.entry_plan
        self.assertTrue(plan.paused)
        self.assertIn(skipped, plan.unresolved_slots)
        self.assertNotIn(skipped, plan.filled_slots)
        self.assertEqual(self.app.var_target.get(), "玩家3")
        self.assertIn("自动轮转已暂停", self.app.var_entry_prompt.get())

    def test_undo_restores_recording_slot(self):
        self.start()
        self.app._key_rank("9")
        self.assertEqual(self.app.var_target.get(), "庄家")
        self.app._key_rank("8")
        self.assertEqual(self.app.var_target.get(), "玩家1")
        self.app.act_undo()
        self.assertEqual(self.app.var_target.get(), "庄家")
        self.assertEqual(self.app.ctrl.entry_plan.cursor_slot_id,
                         self.app.ctrl.entry_plan.slots[1].slot_id)
        self.app.act_undo()
        self.assertEqual(self.app.var_target.get(), "玩家1")
        self.assertEqual(self.app.ctrl.entry_plan.filled_slots, {})

    def test_redraw_failure_does_not_resubmit(self):
        self.start()
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("redraw")):
            self.app._key_rank(TEN_BUCKET)
        self.assertEqual(len(self.app.ctrl.entry_plan.filled_slots), 1)
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks,
                         [TEN_BUCKET])
        self.assertIn("不要重复录牌", self.app.var_status.get())
        self.errors.clear()
        self.app.refresh_all()
        self.app._key_rank("6")
        table = self.app.ctrl.state().current.table
        self.assertEqual(table.players["玩家1"].hands[0].ranks, [TEN_BUCKET])
        self.assertEqual(table.dealer.hands[0].ranks, ["6"])

    def test_same_value_keypad_split_stays_on_first_hand(self):
        self.app.var_decks.set(6)  # Keep the historical six-deck split fixture explicit.
        self.app.act_research_template(split=True, same_value=True)
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app._key_rank(TEN_BUCKET)
        self.app._key_rank("6")
        self.app._key_rank(TEN_BUCKET)
        self.app._key_hole()
        self.assertFalse(self.app.btn_split.instate(["disabled"]))
        self.app.act_action(ACTION_SPLIT)
        hands = self.app.ctrl.state().current.table.players["玩家1"].hands
        self.assertEqual(len(hands), 2)
        self.assertEqual(len(hands[0].cards), 1)
        self.assertEqual(len(hands[1].cards), 1)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 6 * 52 - 4)
        self.assertEqual(self.app.ctrl.entry_plan.continuation_seat, "玩家1")
        self.assertEqual(self.app.ctrl.entry_plan.continuation_hand_ordinal, 1)
        self.app._key_rank("3")
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks,
                         [TEN_BUCKET, "3"])
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[1].ranks,
                         [TEN_BUCKET])
        self.assertEqual(self.app.var_target.get(), "玩家1")

    def test_hole_reveal_does_not_remove_another_physical_card(self):
        self.start()
        self.app._key_rank("9")
        self.app._key_rank("8")
        self.app._key_rank("6")
        self.app._key_hole()
        remaining = self.app.ctrl.state().current.shoe.physical_remaining()
        unrevealed = self.app.ctrl.state().current.shoe.unrevealed_out
        self.app.var_mode.set("揭示")
        self.app.var_target.set("庄家")
        self.app.var_hand.set("（最新一手）")
        self.app.refresh_all()
        self.app.act_card("8")
        shoe = self.app.ctrl.state().current.shoe
        self.assertEqual(shoe.physical_remaining(), remaining)
        self.assertEqual(shoe.unrevealed_out, unrevealed - 1)
        self.assertEqual(self.errors, [])

    def test_analysis_target_widget_is_independent(self):
        self.start()
        self.assertEqual(self.app.analysis_panel.cmb_analysis_target.get(), "玩家1")
        self.app.var_target.set("玩家2")
        self.app.refresh_all()
        self.assertEqual(self.app.var_analysis_target.get(), "玩家1")
        self.assertIn("录入目标：玩家2", self.app.var_entry_prompt.get())
        self.assertIn("分析对象：我／玩家1", self.app.var_entry_prompt.get())

    def test_tk_zero_and_enter_bindings(self):
        self.start()
        binder = self.app._key_binder

        class Event:
            def __init__(self, keysym, state=0):
                self.keysym = keysym
                self.state = state
                self.widget = self_app

        self_app = self.app
        binder.on_press(Event("0"))
        binder.on_release(Event("0"))
        self.app.update()
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks,
                         [TEN_BUCKET])
        self.assertEqual(self.app.var_target.get(), "庄家")
        before = len(self.app.ctrl.ledger.events)
        binder.on_press(Event("Return"))
        binder.on_release(Event("Return"))
        self.app.update()
        self.assertEqual(len(self.app.ctrl.ledger.events), before)
        self.assertTrue(self.app.ctrl.entry_plan.paused)
        self.assertNotIn(self.app.ctrl.entry_plan.slots[1].slot_id,
                         self.app.ctrl.entry_plan.filled_slots)
        self.assertEqual(self.errors, [])

    def test_reopen_restores_plan_without_guessing(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        self.start()
        self.app._key_rank("9")
        session = self.app.ctrl.session_id
        db = self.db
        filled = dict(self.app.ctrl.entry_plan.filled_slots)
        self.app.on_close()
        self.app = None
        self.app = BlackjackLabApp(db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app.ctrl.session_id, session)
        self.assertEqual(self.app.ctrl.entry_plan.filled_slots, filled)
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_INITIAL)
        self.assertEqual(self.app.var_target.get(), "庄家")
        self.assertEqual(self.app.var_analysis_target.get(), "玩家1")


class TestSameValueSplitAnalysis(unittest.TestCase):
    def test_same_value_template_allows_ten_bucket_pair(self):
        snapshot = build_input(example(cards=("T", "T"), up="6",
                                       rules=same_value_split_research_rules()), "玩家1")
        self.assertIn("split", snapshot.legal_actions)
        snapshot.validate()

    def test_same_value_accepts_mixed_ten_ranks(self):
        for cards in (("10", "J"), ("J", "Q"), ("Q", "K"), ("K", "10")):
            snapshot = build_input(example(cards=cards, up="6",
                                           rules=same_value_split_research_rules()), "玩家1")
            self.assertIn("split", snapshot.legal_actions, cards)
            snapshot.validate()

    def test_old_same_rank_template_still_rejects_forged_t_pair(self):
        from dataclasses import replace
        from blackjack_lab.analysis.contracts import canonical
        from blackjack_lab.analysis.service import calculate
        import json
        snapshot = build_input(example(cards=("T", "T"), up="6", rules=split_research_rules()), "玩家1")
        information = json.loads(snapshot.information_json)
        information["action_states"]["分牌"]["allowed"] = True
        forged = replace(snapshot, legal_actions=(*snapshot.legal_actions, "split"),
                         uncertain_actions=(), information_json=canonical(information))
        with self.assertRaises(ValueError):
            forged.validate()
        result = calculate(forged)
        self.assertEqual(result["status"], "failed")


if __name__ == "__main__":
    unittest.main()
