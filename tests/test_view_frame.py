import unittest
from unittest.mock import patch

from tests import test_simple_hole_entry as fixture


class TestViewFrame(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def test_paint_reuses_snapshot_without_changing_records(self):
        self.initial()
        app = self.app
        before = app.ctrl.ledger.to_list()
        with patch.object(app.ctrl, 'state', wraps=app.ctrl.state) as state:
            app.compact_panel.render()
        self.assertLessEqual(state.call_count, 2)
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertIsNone(app._view_snapshot)

    def test_commit_inside_frame_cannot_leave_old_cards_or_analysis(self):
        self.initial()
        app = self.app
        with app._view_frame():
            self.assertEqual(app._current_seg().table.players['玩家1'].hands[0].ranks, ['T','6'])
            app.ctrl.deal_shown('玩家1', '2')
            self.assertEqual(app._current_seg().table.players['玩家1'].hands[0].ranks, ['T','6','2'])
            self.assertEqual(app.ctrl.analysis_input('玩家1').player_ranks, ('T','6','2'))
            self.assertFalse(app.compact_panel.model.choices)
        self.assertIsNone(app._view_snapshot)

    def test_exception_drops_snapshot_and_next_frame_replays_authority(self):
        self.initial()
        app = self.app
        with self.assertRaises(RuntimeError):
            with app._view_frame():
                app._current_seg()
                raise RuntimeError('view failure')
        self.assertIsNone(app._view_snapshot)
        app.ctrl.deal_shown('玩家1','2')
        app.refresh_all()
        self.assertIn('T 6 2', app.compact_panel.identity.get())
        self.assertIsNone(app._view_snapshot)

    def test_undo_and_correction_are_visible_in_nested_frames(self):
        self.initial()
        app = self.app
        event = app.ctrl.ledger.events[-2]
        with app._view_frame():
            app.ctrl.correct(event.event_id, {'rank':'5'}, 'synthetic correction')
            with app._view_frame():
                self.assertEqual(app._current_seg().table.players['玩家1'].hands[0].ranks,['T','5'])
                app.ctrl.undo_last()
                self.assertEqual(app._current_seg().table.players['玩家1'].hands[0].ranks,['T','6'])
            self.assertEqual(app._current_seg().table.players['玩家1'].hands[0].ranks,['T','6'])
