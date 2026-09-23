import copy
import sqlite3
import unittest
from unittest.mock import patch

from tests import test_common_settings as fixture
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED
from blackjack_lab.ui.recent_entry import undo_label
from blackjack_lab.core.table import TableError


class TestNewShoeAnytime(unittest.TestCase):
    setUp = fixture.TestCommonSettings.setUp
    close = fixture.TestCommonSettings.close

    def prepare(self, cards=('9', '6', '7')):
        self.app.act_new_shoe()
        self.app.act_new_round()
        for rank in cards:
            self.app._key_rank(rank)
        self.assertEqual(self.errors, [])

    def test_top_button_creates_first_shoe_without_redundant_confirmation(self):
        button = self.app.compact_panel.new_shoe_button
        self.assertTrue(button.winfo_ismapped())
        with patch('blackjack_lab.ui.app.messagebox.askyesno') as confirmation:
            button.invoke()
        confirmation.assert_not_called()
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 416)
        self.assertEqual(self.app.compact_panel.flow_primary.cget('text'), '开始本轮')

    def test_switch_with_unrevealed_hole_keeps_old_cards_and_starts_full_new_shoe(self):
        self.prepare()
        app, c = self.app, self.app.ctrl
        old = c.state().current
        before = c.ledger.to_list()
        callback_rounds = []
        c.add_context_listener(lambda: callback_rounds.append(c.state().current.shoe.physical_remaining()))
        app.var_decks.set(6)
        app.compact_panel.new_shoe_button.invoke()
        state = c.state()
        self.assertEqual(len(state.segments), 2)
        self.assertEqual(callback_rounds, [312])
        self.assertEqual(c.ledger.to_list()[:len(before)], before)
        self.assertEqual([e.etype for e in c.ledger.events[-3:]], ['ROUND_ENDED', 'SHOE_ENDED', 'SHOE_CREATED'])
        prior = state.segments[0]
        self.assertTrue(prior.closed)
        self.assertEqual(prior.unresolved, old.unresolved)
        self.assertEqual(prior.shoe.remaining, old.shoe.remaining)
        self.assertEqual(prior.shoe.exact_out, old.shoe.exact_out)
        self.assertEqual(prior.shoe.t_bucket_out, old.shoe.t_bucket_out)
        self.assertEqual(prior.shoe.unrevealed_out, old.shoe.unrevealed_out)
        self.assertIsNone(prior.shoe.physical_remaining())  # Interrupted observation is not certified complete.
        self.assertEqual(prior.settlements, [])
        self.assertEqual(prior.unsettled_rounds, [1])
        self.assertEqual(prior.round_observations[-1]['declared'], 'unknown')
        self.assertFalse(state.current.shoe.gap)
        self.assertEqual(state.current.shoe.unrevealed_out, 0)
        self.assertEqual(state.current.shoe.physical_remaining(), 312)
        self.assertIsNone(c.entry_plan)
        self.assertIsNone(app.analysis_panel.last_result)
        self.assertIn('撤销换靴', undo_label(c))
        app.opening_estimate.current_input().validate()
        self.assertEqual(self.errors, [])

    def test_switch_works_during_initial_dealing_and_input_pause(self):
        self.prepare(('T',))
        self.app._key_pause()
        self.assertTrue(self.app.ctrl.entry_plan.input_paused)
        self.app.act_new_shoe()
        self.assertEqual(len(self.app.ctrl.state().segments), 2)
        self.app.act_new_round()
        self.app._key_rank('8')
        self.assertEqual(self.app.ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['8'])
        self.assertEqual(self.errors, [])

    def test_auto_started_empty_next_round_does_not_trap_new_shoe(self):
        self.prepare()
        app = self.app
        app._key_stand()
        app._key_rank('A')
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)
        app.act_new_shoe()
        state = app.ctrl.state()
        self.assertEqual(len(state.segments), 2)
        self.assertEqual(len(state.segments[0].settlements), 1)
        self.assertEqual(state.segments[0].unsettled_rounds, [2])
        self.assertEqual(state.current.shoe.physical_remaining(), 416)
        self.assertEqual(self.errors, [])

    def test_cancel_and_stale_confirmation_preserve_current_shoe(self):
        self.prepare()
        c = self.app.ctrl
        before, plan = c.ledger.to_list(), copy.deepcopy(c.entry_plan.to_dict())
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=False):
            self.app.act_new_shoe()
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.entry_plan.to_dict(), plan)
        expected = c.context_token
        c.mark_gap('simulated intervening update')
        changed = c.ledger.to_list()
        with self.assertRaisesRegex(TableError, '记录已变化'):
            c.replace_shoe(self.app._build_rules(), expected)
        self.assertEqual(c.ledger.to_list(), changed)

    def test_failed_save_or_unsupported_new_rules_never_close_old_shoe(self):
        self.prepare()
        c = self.app.ctrl
        before, plan = c.ledger.to_list(), copy.deepcopy(c.entry_plan.to_dict())
        insert = c.store._insert
        def fail_on_new_shoe(event):
            if event.seq > before[-1]['seq'] and event.etype == 'SHOE_CREATED':
                raise sqlite3.OperationalError('injected disk failure after prior inserts')
            return insert(event)
        with patch.object(c.store, '_insert', side_effect=fail_on_new_shoe):
            self.app.act_new_shoe()
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.entry_plan.to_dict(), plan)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)
        self.app.rule_details['shoe_model'] = 'per_round_reset'
        self.app.act_new_shoe()
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(len(self.errors), 2)

    def test_one_undo_restores_old_round_cards_position_and_pause(self):
        self.prepare(('T',))
        app, c = self.app, self.app.ctrl
        app._key_pause()
        before = copy.deepcopy(c.entry_plan.to_dict())
        app.act_new_shoe()
        app.act_undo()
        self.assertEqual(len(c.state().segments), 1)
        self.assertFalse(c.state().current.closed)
        self.assertEqual(c.state().current.shoe.physical_remaining(), 415)
        self.assertEqual(c.state().current.round_observations, [])
        self.assertEqual(c.entry_plan.filled_slots, before['filled_slots'])
        self.assertEqual(c.entry_plan.cursor_slot_id, before['cursor_slot_id'])
        self.assertTrue(c.entry_plan.input_paused)
        self.assertEqual(self.errors, [])

    def test_recovery_can_undo_switch_and_recovers_the_restored_plan_again(self):
        self.prepare()
        c = self.app.ctrl
        old_shoe = c.state().current.shoe_id
        old_slots = dict(c.entry_plan.filled_slots)
        self.app.act_new_shoe()
        recovered = SessionController.recover(self.db, c.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.state().current.shoe.physical_remaining(), 416)
        recovered.undo_last()
        self.assertEqual(recovered.state().current.shoe_id, old_shoe)
        self.assertEqual(recovered.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(recovered.entry_plan.filled_slots, old_slots)
        again = SessionController.recover(self.db, c.session_id)
        self.addCleanup(again.close)
        self.assertEqual(again.entry_plan.to_dict(), recovered.entry_plan.to_dict())

    def test_untrusted_old_plan_does_not_prevent_switch_or_get_guessed_on_undo(self):
        self.prepare()
        c = self.app.ctrl
        c._pause_entry_recovery('simulated damaged plan')
        self.app.act_new_shoe()
        self.assertEqual(c.state().current.shoe.physical_remaining(), 416)
        self.app.act_undo()
        self.assertEqual(c.entry_plan.mode, MODE_UNALIGNED)
        self.assertTrue(c.entry_plan.input_paused)
        self.assertIn('旧轮录入位置需核对', c.entry_warning)
        self.assertEqual(self.errors, [])

    def test_no_round_and_already_closed_shoe_also_allow_new_shoe(self):
        self.app.act_new_shoe()
        self.app.act_new_shoe()
        self.assertEqual([e.etype for e in self.app.ctrl.ledger.events[-2:]], ['SHOE_ENDED', 'SHOE_CREATED'])
        self.app.act_undo()
        self.assertEqual(len(self.app.ctrl.state().segments), 1)
        self.app.act_end_shoe()
        self.app.act_new_shoe()
        self.assertEqual(len(self.app.ctrl.state().segments), 2)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
