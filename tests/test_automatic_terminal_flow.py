import unittest
from dataclasses import replace

from tests import test_simple_hole_entry as fixture
from blackjack_lab.core.table import ACTION_SPLIT, TableError, TableState
from blackjack_lab.core.rules import CONFIRM_VERIFIED
from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.automatic_flow import dealer_finish_message


class TestAutomaticTerminalFlow(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def dealer_turn(self, rule='S17'):
        app = self.app
        app.var_simple_hole.set(True)
        app.act_research_template()
        app.var_s17.set(rule)
        app.act_new_shoe()
        app.act_new_round()
        for rank in ['T', '6', '6']:
            app._key_rank(rank)
        app._key_stand()
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(self.errors, [])

    def test_default_rule_selection_uses_the_user_requested_s17_preset(self):
        self.assertEqual(self.app.var_s17.get(), 'S17')
        self.assertEqual(self.app.var_confirm.get(), CONFIRM_VERIFIED)

    def test_player_bust_marks_rows_and_moves_to_next_player_then_dealer(self):
        self.initial(players=3)
        app, ctrl = self.app, self.app.ctrl
        for player, following in [(1, '玩家2'), (2, '玩家3'), (3, '庄家')]:
            before = len(ctrl.ledger.events)
            app._key_rank('T')
            hand = ctrl.state().current.table.players[f'玩家{player}'].hands[0]
            self.assertTrue(hand.is_bust)
            self.assertTrue(hand.is_closed)
            self.assertEqual(app.var_target.get(), following)
            self.assertIn('已爆牌', hand.display())
            self.assertIn(f'已自动转到{following}', app.compact_panel.flow_message.get())
            self.assertEqual(app.compact_panel.seat_table.item(f'玩家{player}', 'values')[-1], '已爆牌')
            self.assertEqual([e.etype for e in ctrl.ledger.events[before:]], ['CARD_DEALT'])
        self.assertEqual(app.compact_panel.state.get(), '已爆牌')
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 405)
        app._key_rank('A')
        app.act_end_round()
        self.assertNotEqual(app.compact_panel.state.get(), '已爆牌')
        self.assertEqual(app.compact_panel.flow.stage, 'start')
        self.assertEqual(self.errors, [])

    def test_split_bust_finishes_first_hand_then_moves_to_second_before_other_player(self):
        app = self.app
        app.var_simple_hole.set(True)
        app.act_research_template(split=True)
        app.act_new_shoe()
        app.var_participants['玩家2'].set(True)
        app.act_new_round()
        for rank in ['8', 'T', '6', '8', '6']:
            app._key_rank(rank)
        app.act_action(ACTION_SPLIT)
        app._key_rank('9')
        app._key_rank('T')
        seg = app.ctrl.state().current
        first, second = seg.table.players['玩家1'].hands
        self.assertTrue(first.is_bust)
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertEqual(app._selected_hand_id(seg), second.hand_id)
        self.assertIn('已自动转到玩家1／第2手', app.compact_panel.flow_message.get())
        app._key_rank('T')
        app._key_stand()
        self.assertEqual(app.var_target.get(), '玩家2')
        self.assertNotIn('已自动转到', app.compact_panel.flow_message.get())
        self.assertEqual(self.errors, [])

    def test_ace_reduces_to_one_and_exact_21_is_not_bust(self):
        self.prepare(players=2)
        app = self.app
        for rank in ['A', 'T', '6', '9', '6', '5', '6']:
            app._key_rank(rank)
        hand = app.ctrl.state().current.table.players['玩家1'].hands[0]
        self.assertEqual(hand.total()[0], 21)
        self.assertFalse(hand.is_bust)
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertNotIn('已爆牌', app.compact_panel.flow_message.get())
        self.assertEqual(self.errors, [])

    def test_hard_17_stops_all_rank_entry_without_extra_event_or_settlement(self):
        self.dealer_turn()
        app, ctrl = self.app, self.app.ctrl
        app._key_rank('9')
        self.assertFalse(app.compact_panel.card_buttons[0].instate(['disabled']))
        app._key_rank('2')
        self.assertIn('17 点，已自动停牌', app.compact_panel.flow_message.get())
        self.assertIn('已自动停牌', app.compact_panel.identity.get())
        self.assertIn('无需再录入', app.var_entry_prompt.get())
        self.assertTrue(all(b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        self.assertTrue(all(b.instate(['disabled']) for b in app.workbench_card_buttons))
        before = ctrl.ledger.to_list()
        for _ in range(3):
            app._key_rank('8')
            app.act_card('T')
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.state().current.table.dealer.hands[0].ranks, ['6', '9', '2'])
        self.assertEqual(ctrl.state().current.settlements, [])
        with self.assertRaises(TableError):
            ctrl.deal_shown('庄家', '2')
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(self.errors, [])

    def test_s17_stops_at_soft_17_but_does_not_infer_hidden_hole(self):
        self.dealer_turn()
        app = self.app
        self.assertFalse(app.dealer_recording_finished())
        app._key_rank('A')
        self.assertIn('软17 点，已自动停牌', app.dealer_recording_finished())
        self.assertTrue(all(b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        self.assertEqual(self.errors, [])

    def test_locked_h17_continues_soft_17_and_restores_that_rule_after_restart(self):
        self.dealer_turn('H17')
        self.app._key_rank('A')
        self.assertFalse(self.app.dealer_recording_finished())
        before = self.app.ctrl.ledger.to_list()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(self.app.ctrl.state().current.rules.dealer_soft17, 'H17')
        self.app.var_auto_next.set(False)  # This legacy case inspects the terminal before manual settlement.
        self.assertFalse(self.app.dealer_recording_finished())
        self.app._key_rank('2')
        self.assertIn('19 点，已自动停牌', self.app.dealer_recording_finished())
        self.assertEqual(self.errors, [])

    def test_undo_and_correction_reopen_dealer_entry_when_total_falls_below_17(self):
        self.dealer_turn()
        app = self.app
        app._key_rank('9')
        app._key_rank('2')
        app.act_undo()
        self.assertFalse(app.dealer_recording_finished())
        self.assertTrue(all(not b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        app._key_rank('2')
        event = app.ctrl.ledger.events[-1]
        app.ctrl.correct_recent_visible(event.event_id, 'A', 'synthetic correction', app.ctrl.context_token)
        app._sync_from_plan()
        app.refresh_all()
        self.assertFalse(app.dealer_recording_finished())
        self.assertTrue(all(not b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), 411)
        self.assertEqual(self.errors, [])

    def test_recovery_keeps_bust_target_and_no_extra_automatic_actions(self):
        self.initial(players=2)
        self.app._key_rank('T')
        before, plan = self.app.ctrl.ledger.to_list(), self.app.ctrl.entry_plan.to_dict()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(self.app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(self.app.var_target.get(), '玩家2')
        self.assertIn('已自动转到玩家2', self.app.compact_panel.flow_message.get())
        self.assertEqual(self.errors, [])

    def test_undo_bust_returns_to_the_reopened_player(self):
        self.initial(players=2)
        self.app._key_rank('T')
        self.assertEqual(self.app.var_target.get(), '玩家2')
        self.app.act_undo()
        self.assertEqual(self.app.var_target.get(), '玩家1')
        self.assertFalse(self.app.ctrl.state().current.table.players['玩家1'].hands[0].is_bust)
        self.assertNotIn('已爆牌', self.app.compact_panel.flow_message.get())
        self.assertEqual(self.errors, [])

    def test_stopped_dealer_recovery_and_next_round_reset_input(self):
        self.dealer_turn()
        app = self.app
        app._key_rank('A')
        before = app.ctrl.ledger.to_list()
        self.close()
        self.app = app = BlackjackLabApp(self.db, auto_analysis=False)
        app.update()
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertTrue(all(b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        app.act_complete_and_next(app.ctrl.state().current.round_id)
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertFalse(app.dealer_recording_finished())
        self.assertTrue(all(not b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        self.assertEqual(self.errors, [])


class TestDealerFinishRules(unittest.TestCase):
    def test_visible_terminals_unknown_rule_and_incomplete_information(self):
        for rule, cards, expected in [('S17', ['A', '6'], '停牌'), ('H17', ['A', '6'], ''),
                (None, ['A', '6'], ''), (None, ['T', '7'], '停牌'),
                ('S17', ['T', '8'], '停牌'), ('S17', ['T', '9'], '停牌'),
                ('S17', ['T', 'T'], '停牌'), ('S17', ['A', 'T'], '停牌'),
                ('S17', ['T', '6', '8'], '爆牌'), ('S17', ['6'], '')]:
            with self.subTest(rule=rule, cards=cards):
                table = TableState(replace(research_rules(8), dealer_soft17=rule))
                table.start_round(['玩家1'])
                for rank in cards:
                    table.add_card('庄家', rank)
                message = dealer_finish_message(table)
                self.assertIn(expected, message) if expected else self.assertEqual(message, '')
