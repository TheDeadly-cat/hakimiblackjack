import unittest
from unittest.mock import patch

from tests import test_simple_hole_entry as fixture
from blackjack_lab.core.table import TableError
from blackjack_lab.ledger.events import CARD_DEALT, FACE_HIDDEN
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.round_players import preview_players, apply_players


class TestRoundPlayerAdjustment(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare

    def deal(self, players, ranks):
        self.prepare(players=players)
        for rank in ranks:
            self.app._key_rank(rank)
        self.assertEqual(self.errors, [])

    def recover(self):
        current = self.app.ctrl
        recovered = SessionController.recover(self.db, current.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.ledger.to_list(), current.ledger.to_list())
        self.assertEqual(recovered.entry_plan.to_dict(), current.entry_plan.to_dict())
        return recovered

    def test_seven_to_six_after_six_cards_needs_no_prompt_and_dealer_is_next(self):
        self.deal(7, ['2', '3', '4', '5', '6', '7'])
        app, ctrl = self.app, self.app.ctrl
        before = ctrl.ledger.to_list()
        with patch('blackjack_lab.ui.app.messagebox.askyesno') as ask:
            app.compact_panel.remove_player.invoke()
        ask.assert_not_called()
        self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
        seg = ctrl.state().current
        self.assertEqual(seg.table.participants, [f'玩家{i}' for i in range(1, 7)])
        self.assertEqual([seg.table.players[f'玩家{i}'].hands[0].ranks for i in range(1, 7)], [[str(i+1)] for i in range(1, 7)])
        self.assertEqual(seg.shoe.physical_remaining(), 410)
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(app.compact_panel.player_count.get(), '本轮 6 人')
        self.assertEqual(seg.table.round_no, 1)
        self.assertFalse(self.recover().entry_plan.paused)
        # Earlier analysis/review prefixes still reproduce the original seven seats.
        self.assertEqual(len(ctrl.ledger.replay(through_seq=before[-1]['seq']).current.table.participants), 7)
        self.assertEqual(self.errors, [])

    def test_seventh_card_moves_from_phantom_player_to_dealer_then_completes_normally(self):
        self.deal(7, ['2', '3', '4', '5', '6', '7', '9'])
        app, ctrl = self.app, self.app.ctrl
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=True) as ask:
            app.change_player_count(-1)
        self.assertIn('玩家7 → 庄家', ask.call_args.args[1])
        self.assertEqual(ctrl.state().current.table.dealer.hands[0].ranks, ['9'])
        self.assertFalse(ctrl.state().current.table.players['玩家7'].hands)
        self.assertEqual(app.var_target.get(), '玩家1')
        for _ in range(6):
            app._key_rank('2')
        seg = ctrl.state().current
        self.assertEqual(seg.shoe.physical_remaining(), 402)
        hidden = [e for e in ctrl.ledger.events if e.etype == CARD_DEALT
                  and e.payload['face_state'] == FACE_HIDDEN and not ctrl.ledger.is_voided(e.event_id)]
        self.assertEqual(len(hidden), 1)
        for _ in range(6):
            app._key_stand()
        app._key_rank('8')
        app.act_end_round()
        self.assertEqual(ctrl.state().current.table.dealer.hands[0].ranks, ['9', '8'])
        self.assertEqual(len(ctrl.state().current.settlements), 6)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 402)
        self.assertEqual(self.errors, [])

    def test_add_missing_seventh_player_reassigns_mistaken_dealer_and_keeps_physical_count(self):
        self.deal(6, ['2', '3', '4', '5', '6', '7', '8'])
        self.app.change_player_count(1)
        seg = self.app.ctrl.state().current
        self.assertEqual(seg.table.players['玩家7'].hands[0].ranks, ['8'])
        self.assertFalse(seg.table.dealer.hands)
        self.assertEqual(seg.shoe.physical_remaining(), 409)
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.recover()
        self.assertEqual(self.errors, [])

    def test_cancel_reassignment_preview_does_not_change_any_records_or_plan(self):
        self.deal(7, ['2'] * 7)
        ctrl = self.app.ctrl
        before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=False):
            self.app.change_player_count(-1)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(sum(v.get() for v in self.app.var_participants.values()), 7)

    def test_write_failure_rolls_back_whole_adjustment(self):
        self.deal(7, ['2', '3', '4', '5', '6', '7'])
        ctrl = self.app.ctrl
        before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
        insert = ctrl.store._insert
        def fail(event):
            if event.seq == len(before) + 3:
                raise OSError('synthetic transaction failure')
            return insert(event)
        with patch.object(ctrl.store, '_insert', side_effect=fail):
            self.app.change_player_count(-1)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(sum(v.get() for v in self.app.var_participants.values()), 7)
        self.assertEqual(len(self.errors), 1)

    def test_sidecar_failure_keeps_committed_adjustment_and_pauses(self):
        self.deal(7, ['2', '3', '4', '5', '6', '7'])
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        with patch.object(ctrl, '_write_entry_plan', side_effect=OSError('synthetic plan failure')):
            self.app.change_player_count(-1)
        self.assertEqual(len(ctrl.state().current.table.participants), 6)
        self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), ctrl.ledger.to_list())
        self.assertTrue(ctrl.entry_plan.paused)
        self.assertIn('不要重复录入', self.app.var_status.get())
        count = len(ctrl.ledger.events)
        self.app._key_rank('9')
        self.assertEqual(len(ctrl.ledger.events), count)

    def test_stale_preview_and_pause_change_cannot_apply(self):
        self.deal(7, ['2'])
        ctrl = self.app.ctrl
        seats = [f'玩家{i}' for i in range(1, 7)]
        preview = preview_players(ctrl, seats)
        ctrl.entry_plan.toggle_input_pause()
        with self.assertRaises(TableError):
            apply_players(ctrl, preview)
        ctrl.entry_plan.toggle_input_pause()
        self.app._key_rank('3')
        with self.assertRaises(TableError):
            apply_players(ctrl, preview)
        self.assertEqual(len(ctrl.state().current.table.participants), 7)

    def test_repeated_add_remove_keeps_corrected_ranks_and_input_pause(self):
        self.deal(7, ['2', '3', '4'])
        ctrl = self.app.ctrl
        ctrl.correct_recent_visible(ctrl.ledger.events[-1].event_id, '8', 'synthetic rank correction', ctrl.context_token)
        self.app._key_pause()
        for delta in (-1, 1, -1):
            self.app.change_player_count(delta)
            self.assertTrue(ctrl.entry_plan.input_paused)
            self.assertEqual(ctrl.state().current.table.players['玩家3'].hands[0].ranks, ['8'])
            self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 413)
        self.recover()
        self.assertEqual(self.errors, [])

    def test_shrink_two_to_one_at_first_pass_end_adds_exactly_one_confirmed_hole(self):
        self.deal(2, ['T', '6', '6'])
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=True) as ask:
            self.app.change_player_count(-1)
        self.assertIn('未知底牌', ask.call_args.args[1])
        seg = self.app.ctrl.state().current
        self.assertEqual(seg.table.players['玩家1'].hands[0].ranks, ['T', '6'])
        self.assertEqual(seg.table.dealer.hands[0].ranks[0], '6')
        self.assertEqual(seg.shoe.physical_remaining(), 412)
        self.assertEqual(seg.shoe.unrevealed_out, 1)
        self.assertEqual(self.app.var_target.get(), '玩家1')
        self.app.ctrl.analysis_input('玩家1').validate()
        self.recover()
        self.assertEqual(self.errors, [])

    def test_zero_cards_can_change_without_new_round_number(self):
        self.prepare(players=7)
        self.app.change_player_count(-1)
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 1)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 416)
        self.assertEqual(self.errors, [])

    def test_reverse_order_keeps_recorded_order_and_recovers(self):
        app = self.app
        app.var_simple_hole.set(True)
        app.act_research_template()
        app.act_new_shoe()
        for variable in app.var_participants.values():
            variable.set(True)
        app.var_deal_direction.set('reverse')
        app.act_new_round()
        for rank in ['2', '3', '4', '5', '6', '7']:
            app._key_rank(rank)
        app.change_player_count(-1)
        seg = app.ctrl.state().current
        self.assertEqual(seg.table.participants, [f'玩家{i}' for i in range(6, 0, -1)])
        self.assertEqual(seg.table.players['玩家6'].hands[0].ranks, ['2'])
        self.assertEqual(seg.table.players['玩家1'].hands[0].ranks, ['7'])
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(seg.shoe.physical_remaining(), 410)
        self.recover()
        self.assertEqual(self.errors, [])

    def test_actual_gui_restart_restores_six_people_and_next_dealer(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        self.deal(7, ['2', '3', '4', '5', '6', '7'])
        self.app.change_player_count(-1)
        before = self.app.ctrl.ledger.to_list()
        plan = self.app.ctrl.entry_plan.to_dict()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(self.app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.assertEqual(self.app.compact_panel.player_count.get(), '本轮 6 人')
        self.app._key_rank('9')
        self.assertEqual(self.app.ctrl.state().current.table.dealer.hands[0].ranks, ['9'])
        self.assertEqual(self.errors, [])
