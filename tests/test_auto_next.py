import copy
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests import test_common_settings as fixture
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_DEALER, MODE_INITIAL
from blackjack_lab.ui.recent_entry import undo_label


class TestAutoNext(unittest.TestCase):
    setUp = fixture.TestCommonSettings.setUp
    close = fixture.TestCommonSettings.close

    def prepare(self, up='6', players=1, rule='S17'):
        app = self.app
        app.var_s17.set(rule)
        app.act_new_shoe()
        for i, var in enumerate(app.var_participants.values(), 1):
            var.set(i <= players)
        app.act_new_round()
        for rank in ['9'] * players + [up] + ['7'] * players:
            app._key_rank(rank)
        for _ in range(players):
            app._key_stand()
        self.assertEqual(self.errors, [])
        self.assertEqual(app.var_target.get(), '庄家')

    def test_16_continues_17_advances_and_one_backspace_restores_card_and_plan(self):
        self.prepare()
        app, ctrl = self.app, self.app.ctrl
        app._key_rank('T')
        self.assertEqual(ctrl.state().current.table.round_no, 1)
        before = copy.deepcopy(ctrl.entry_plan.to_dict())
        callbacks = []
        ctrl.add_context_listener(lambda: callbacks.append(ctrl.state().current.table.round_no))
        app._key_rank('A')
        self.assertEqual(callbacks, [2])
        self.assertEqual([e.etype for e in ctrl.ledger.events[-3:]], ['CARD_DEALT', 'ROUND_ENDED', 'ROUND_STARTED'])
        self.assertEqual(len(ctrl.state().current.settlements), 1)
        self.assertEqual(ctrl.entry_plan.mode, MODE_INITIAL)
        self.assertIn('自动下一局', undo_label(ctrl))
        app.act_undo()
        self.assertEqual(callbacks, [2, 1])
        seg = ctrl.state().current
        self.assertEqual(seg.table.dealer.hands[0].ranks, ['6', 'T'])
        self.assertEqual(seg.shoe.physical_remaining(), 412)
        self.assertEqual(seg.settlements, [])
        self.assertEqual(ctrl.entry_plan.mode, MODE_DEALER)
        self.assertEqual(ctrl.entry_plan.filled_slots, before['filled_slots'])
        app._key_rank('2')
        self.assertEqual(ctrl.state().current.table.round_no, 2)
        self.assertEqual(len(ctrl.state().current.settlements), 1)
        self.assertEqual(self.errors, [])

    def test_reveal_advances_without_extra_physical_card_and_recovers_group_undo(self):
        self.prepare(up='T', players=3)
        app, ctrl = self.app, self.app.ctrl
        physical = ctrl.state().current.shoe.physical_remaining()
        app._key_rank('7')
        self.assertEqual(ctrl.state().current.table.round_no, 2)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), physical)
        self.assertEqual(ctrl.state().current.table.participants, ['玩家1', '玩家2', '玩家3'])
        self.assertEqual(len(ctrl.state().current.settlements), 3)
        recovered = SessionController.recover(self.db, ctrl.session_id)
        self.addCleanup(recovered.close)
        self.assertFalse(recovered.entry_plan.paused)
        recovered.undo_last()
        self.assertEqual(recovered.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(recovered.state().current.table.round_no, 1)
        self.assertEqual(recovered.entry_plan.mode, MODE_DEALER)
        again = SessionController.recover(self.db, ctrl.session_id)
        self.addCleanup(again.close)
        self.assertEqual(again.entry_plan.to_dict(), recovered.entry_plan.to_dict())

    def test_failed_transaction_saves_neither_reveal_settlement_nor_new_round(self):
        self.prepare()
        ctrl = self.app.ctrl
        ledger, plan = ctrl.ledger.to_list(), copy.deepcopy(ctrl.entry_plan.to_dict())
        with patch.object(ctrl.store, 'save_ledger', side_effect=sqlite3.OperationalError('injected disk error')):
            self.app._key_rank('A')
        self.assertEqual(ctrl.ledger.to_list(), ledger)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), ledger)
        self.assertEqual(len(self.errors), 1)
        self.errors.clear()
        self.app._key_rank('A')
        self.assertEqual(ctrl.state().current.table.round_no, 2)

    def test_h17_soft17_waits_and_dealer_bust_advances(self):
        self.prepare(rule='H17')
        self.app._key_rank('A')
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 1)
        self.app._key_rank('T')
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 2)

    def test_dealer_bust_advances(self):
        self.prepare()
        self.app._key_rank('9')
        self.app._key_rank('T')
        self.assertEqual(self.app.ctrl.state().current.table.round_no, 2)
        self.assertEqual(self.app.ctrl.state().current.settlements[-1]['net_units'], 1)

    def test_new_round_card_undo_then_automatic_transition_undo(self):
        self.prepare()
        app = self.app
        app._key_rank('A')
        app._key_rank('8')
        app.act_undo()
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)
        app.act_undo()
        self.assertEqual(app.ctrl.state().current.table.round_no, 1)
        self.assertEqual(app.ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(app.var_target.get(), '庄家')

    def test_held_rank_does_not_spill_into_next_round(self):
        self.prepare()
        app = self.app
        event = SimpleNamespace(keysym='1', state=0, widget=app)
        app._key_binder.on_press(event)
        count = len(app.ctrl.ledger.events)
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)
        for _ in range(4):
            app._key_binder.on_press(event)
        self.assertEqual(len(app.ctrl.ledger.events), count)
        app._key_binder.on_release(event)
        app._key_binder.on_press(event)
        self.assertEqual(len(app.ctrl.ledger.events), count + 1)
        app._key_binder.on_release(event)

    def test_pause_survives_group_undo_and_blocks_further_card(self):
        self.prepare()
        app = self.app
        app._key_rank('T')
        app._key_rank('A')
        app._key_pause()
        self.assertTrue(app.ctrl.entry_plan.input_paused)
        app.act_undo()
        self.assertEqual(app.ctrl.state().current.table.round_no, 1)
        self.assertTrue(app.ctrl.entry_plan.input_paused)
        count = len(app.ctrl.ledger.events)
        app._key_rank('2')
        self.assertEqual(len(app.ctrl.ledger.events), count)
        app._key_pause()
        app._key_rank('2')
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)

    def test_seven_players_reverse_order_persist_and_sidecar_failure_pauses_undo(self):
        self.app.var_deal_direction.set('reverse')
        self.prepare(players=7)
        app, ctrl = self.app, self.app.ctrl
        app._key_rank('A')
        self.assertEqual(ctrl.state().current.table.participants, [f'玩家{i}' for i in range(7, 0, -1)])
        self.assertEqual(len(ctrl.state().current.settlements), 7)
        self.assertEqual(app.var_target.get(), '玩家7')
        with patch.object(ctrl, '_write_entry_plan', side_effect=OSError('sidecar unavailable')):
            app.act_undo()
        self.assertEqual(ctrl.state().current.table.round_no, 1)
        self.assertEqual(ctrl.state().current.settlements, [])
        self.assertTrue(ctrl.entry_plan.paused)
        self.assertIn('保存', ctrl.entry_warning)

    def test_initial_deal_prepares_no_hand_analysis_or_workers(self):
        app = self.app
        app.act_new_shoe()
        app.var_participants['玩家2'].set(True)
        app.act_new_round()
        with patch('blackjack_lab.analysis.information.prepare_prefix') as prepare, \
                patch.object(app.analysis_panel.service, 'start') as selected, \
                patch.object(app.analysis_panel.overview.service, 'start') as others:
            for rank in ['T', '9', '6', '2']:
                app._key_rank(rank)
            app.analysis_panel.auto.set(True)
            app.analysis_panel._cancel_auto()
            app.analysis_panel._run_auto()
            app.analysis_panel.overview.poll()
            prepare.assert_not_called()
            selected.assert_not_called()
            others.assert_not_called()
        app._key_rank('7')
        self.assertIsNotNone(app.analysis_panel.current_input)
        self.assertTrue(all(r['snapshot'] for r in app.analysis_panel.overview.rows.values()))
        self.assertEqual(self.errors, [])

    def test_shared_prefix_revalidates_after_card_undo_and_in_place_damage(self):
        self.app.act_new_shoe()
        self.app.var_participants['玩家2'].set(True)
        self.app.act_new_round()
        for rank in ['T', '9', '6', '2', '7']:
            self.app._key_rank(rank)
        ctrl = self.app.ctrl
        first = ctrl.current_decision_input('玩家1')
        self.assertIs(first, ctrl.current_decision_input('玩家1'))
        self.app._key_rank('2')
        second = ctrl.current_decision_input('玩家1')
        self.assertNotEqual(first.prefix_digest, second.prefix_digest)
        self.app.act_undo()
        undone = ctrl.current_decision_input('玩家1')
        self.assertEqual(undone.counts, first.counts)
        self.assertNotEqual(undone.prefix_digest, first.prefix_digest)
        # Cache identity includes content, not only the number of appended events.
        original = ctrl.ledger.events[3].payload['rank']
        ctrl.ledger.events[3].payload['rank'] = 'INVALID'
        try:
            with self.assertRaises(ValueError):
                ctrl.current_decision_input('玩家1')
        finally:
            ctrl.ledger.events[3].payload['rank'] = original

    def test_read_frame_is_callback_scoped_and_commit_invalidates_it(self):
        ctrl = self.app.ctrl
        with ctrl.read_frame():
            first = ctrl.state()
            self.assertIs(ctrl.state(), first)
            self.app.act_new_shoe()
            second = ctrl.state()
            self.assertIsNot(first, second)
            self.assertIsNotNone(second.current)
        self.assertIsNot(ctrl.state(), second)


if __name__ == '__main__':
    unittest.main()
