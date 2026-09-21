import copy
import unittest
from unittest.mock import patch

from tests import test_simple_hole_entry as fixture
from blackjack_lab.ui.recent_entry import recent_visible, undo_label
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED, MODE_PEEK_WAIT
from blackjack_lab.core.table import TableError, ACTION_SPLIT, ACTION_STAND
from blackjack_lab.core.shoe import ConsistencyError
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate


class TestRecentCorrection(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def change(self, rank):
        ctrl = self.app.ctrl
        recent = recent_visible(ctrl)
        return ctrl.correct_recent_visible(recent.event_id, rank, '误按牌面', ctrl.context_token)

    def test_inline_edits_visible_trigger_not_auto_hole_and_keeps_history(self):
        self.initial()
        ctrl, view = self.app.ctrl, self.app.compact_panel
        before = ctrl.ledger.to_list()
        snapshot = ctrl.analysis_input('玩家1')
        self.assertEqual(recent_visible(ctrl).event_id, ctrl.ledger.events[-2].event_id)
        self.assertEqual(undo_label(ctrl), '撤销自动暗牌')
        view.edit_button.invoke()
        view.edit_rank.set('5')
        with patch('blackjack_lab.ui.app.simpledialog.askstring') as dialog:
            view.save_correction.invoke()
        dialog.assert_not_called()
        self.assertFalse(view.editor_open)
        self.assertEqual(ctrl.ledger.to_list()[:-1], before)
        self.assertEqual(ctrl.ledger.events[-1].etype, 'CORRECTION')
        self.assertEqual(recent_visible(ctrl).rank, '5')
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(ctrl.analysis_input('玩家1', through_seq=snapshot.through_seq).to_dict(), snapshot.to_dict())
        self.assertNotEqual(ctrl.entry_plan.mode, MODE_UNALIGNED)
        recovered = SessionController.recover(self.db, ctrl.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(recent_visible(recovered).rank, '5')
        self.assertNotEqual(recovered.entry_plan.mode, MODE_UNALIGNED)

    def test_revealed_card_edits_reveal_and_undo_restores_same_hole(self):
        self.initial()
        self.app._key_stand()
        self.app._key_rank('8')
        ctrl = self.app.ctrl
        reveal, hole = ctrl.ledger.events[-1], ctrl.ledger.events[-1].payload['target_event_id']
        self.assertEqual(undo_label(ctrl), '撤销底牌揭示')
        self.change('9')
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], reveal.event_id)
        self.assertEqual(undo_label(ctrl), '撤销本次纠错')
        ctrl.undo_last()
        ctrl.undo_last()
        self.assertIn(hole, ctrl.state().current.unresolved)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)

    def test_editing_initial_upcard_changes_peek_gate_without_fake_fact(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        self.change('A')
        self.app._key_rank('6')
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.assertFalse(self.app.ctrl.state().current.table.dealer_hole_checked_negative)

    def test_undo_upcard_correction_restores_original_peek_gate(self):
        self.prepare()
        self.app._key_rank('T')
        self.app._key_rank('6')
        self.change('A')
        self.app.act_undo()
        self.assertEqual(self.app.ctrl.entry_plan.dealer_up_rank, '6')
        self.app._key_rank('6')
        self.assertNotEqual(self.app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.assertFalse(self.app.ctrl.state().current.table.dealer_hole_checked_negative)

    def test_split_conflict_is_rejected_without_altering_later_events(self):
        app = self.app
        app.var_simple_hole.set(True)
        app.act_research_template(split=True)
        app.act_new_shoe()
        app.act_new_round()
        for rank in ('8', '6', '8'):
            app._key_rank(rank)
        app.act_action(ACTION_SPLIT)
        before = app.ctrl.ledger.to_list()
        with self.assertRaises(TableError):
            self.change('7')
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), before)

    def test_conflicting_negative_peek_is_shown_inline_without_saving(self):
        self.initial(up='T')
        self.app.act_peek_negative()
        self.app._key_stand()
        self.app._key_rank('8')
        ctrl, view = self.app.ctrl, self.app.compact_panel
        before = ctrl.ledger.to_list()
        view.open_correction()
        view.edit_rank.set('A')
        view.apply_correction()
        self.assertIn('非BJ', view.edit_error.get())
        self.assertTrue(view.editor_open)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(self.errors, [])

    def test_stale_editor_and_keyboard_focus_do_not_record_or_change_target(self):
        self.initial()
        ctrl, view = self.app.ctrl, self.app.compact_panel
        view.open_correction()
        before = ctrl.ledger.to_list()
        self.app.update()
        view.rank_select.focus_force()
        for key in ('3', 'Return', 'space'):
            view.rank_select.event_generate('<KeyPress>', keysym=key)
            view.rank_select.event_generate('<KeyRelease>', keysym=key)
            self.app.update()
        self.assertEqual(ctrl.ledger.to_list(), before)
        ctrl.player_action('玩家1', ctrl.state().current.table.players['玩家1'].hands[0].hand_id, ACTION_STAND)
        after = ctrl.ledger.to_list()
        view.apply_correction()
        self.assertIn('记录已变化', view.edit_error.get())
        self.assertEqual(ctrl.ledger.to_list(), after)

    def test_store_failure_preserves_card_and_allows_explicit_retry(self):
        self.initial()
        ctrl = self.app.ctrl
        before = ctrl.ledger.to_list()
        with patch.object(ctrl.store, 'save_event', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.change('5')
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.change('5')
        self.assertEqual(recent_visible(ctrl).rank, '5')

    def test_recent_card_and_undo_labels_follow_voids_and_new_round(self):
        self.initial()
        self.app.act_undo()
        self.assertEqual(recent_visible(self.app.ctrl).rank, '6')
        self.assertIn('玩家1', undo_label(self.app.ctrl))
        self.app.act_undo()
        self.assertEqual(recent_visible(self.app.ctrl).seat, '庄家')
        self.assertEqual(recent_visible(self.app.ctrl).ordinal, 1)

    def test_inline_and_timeline_correction_have_same_probability_and_ev(self):
        self.initial()
        ctrl = self.app.ctrl
        timeline = copy.deepcopy(ctrl.ledger)
        target = recent_visible(ctrl).event_id
        timeline.correct(target, {'rank': '5'}, '误按牌面')
        self.change('5')
        old_input, new_input = build_input(timeline, '玩家1'), ctrl.analysis_input('玩家1')
        self.assertEqual(old_input.counts, new_input.counts)
        self.assertEqual(old_input.physical_remaining, new_input.physical_remaining)
        old, new = calculate(old_input), calculate(new_input)
        self.assertEqual(new['status'], 'available')
        self.assertEqual(old['actions'], new['actions'])
        self.assertEqual(old['probabilities'], new['probabilities'])

    def test_shoe_capacity_is_checked_by_full_replay(self):
        app = self.app
        app.var_decks.set(6)
        app.act_research_template()
        app.act_new_shoe()
        ctrl = app.ctrl
        for count in (7, 4):
            seats = [f'玩家{i}' for i in range(1, count + 1)]
            ctrl.start_round(seats)
            for seat in seats:
                ctrl.deal_shown(seat, 'A')
                ctrl.deal_shown(seat, 'A')
            ctrl.deal_shown('庄家', 'A')
            ctrl.deal_shown('庄家', '6')
            ctrl.end_round()
        ctrl.start_round(['玩家1'])
        ctrl.deal_shown('玩家1', 'T')
        ctrl.deal_shown('庄家', '6')
        ctrl.deal_shown('玩家1', '5')
        before = ctrl.ledger.to_list()
        with self.assertRaises(ConsistencyError):
            self.change('A')
        self.assertEqual(ctrl.ledger.to_list(), before)
