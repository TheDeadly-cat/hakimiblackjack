import json
import unittest
from pathlib import Path
from unittest.mock import patch

from tests import test_simple_hole_entry as fixture
from blackjack_lab.ui.app import BlackjackLabApp


class TestWindowLayout(unittest.TestCase):
    setUp = fixture.TestSimpleHoleEntry.setUp
    close = fixture.TestSimpleHoleEntry.close
    prepare = fixture.TestSimpleHoleEntry.prepare
    initial = fixture.TestSimpleHoleEntry.initial

    def size(self):
        self.app.update()
        return self.app.winfo_width(), self.app.winfo_height()

    def test_user_sizes_restore_across_drawer_workbench_and_restart(self):
        app, view = self.app, self.app.compact_panel
        app.geometry('780x690')
        self.assertEqual(self.size(), (780, 690))
        view.toggle_recording()
        app.geometry('820x770')
        self.assertEqual(self.size(), (820, 770))
        view.toggle_recording()
        self.assertEqual(self.size(), (780, 690))
        view.toggle_recording()
        self.assertEqual(self.size(), (820, 770))
        app.show_workbench()
        app.geometry('1200x850')
        self.size()
        app.show_compact()
        self.assertEqual(self.size(), (820, 770))
        view.toggle_recording()
        view.toggle_cards()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.assertEqual(self.size(), (780, 690))
        self.assertFalse(self.app.window_layout.cards_visible)
        self.assertEqual(set(json.loads(Path(str(self.db) + '.ui-preferences.json').read_text(encoding='utf-8'))),
                         {'schema', 'sizes', 'cards_visible'})

    def test_card_strip_and_primary_positions_do_not_follow_phase_or_results(self):
        self.prepare()
        app, view = self.app, self.app.compact_panel
        self.assertEqual(view.model.state, '等待初始牌')
        self.size()
        point = lambda w: (w.winfo_rootx(), w.winfo_rooty())
        before = point(view.card_buttons[0]), point(view.flow_area)
        self.assertTrue(view.card_buttons[0].winfo_ismapped())
        self.assertFalse(view.recording_open)
        for rank in ('T', '6', '6'):
            app._key_rank(rank)
            self.size()
            self.assertEqual((point(view.card_buttons[0]), point(view.flow_area)), before)
        app._key_stand()
        app._key_rank('8')
        app._key_rank('3')
        self.size()
        self.assertEqual((point(view.card_buttons[0]), point(view.flow_area)), before)
        self.assertEqual(view.flow.stage, 'ready')
        self.assertGreater(view.flow_primary.winfo_rooty(), view.card_buttons[0].winfo_rooty() + view.card_buttons[0].winfo_height())

    def test_keyboard_mode_keeps_target_and_recent_and_layout_writes_no_events(self):
        self.initial()
        app, view = self.app, self.app.compact_panel
        before = app.ctrl.ledger.to_list()
        target, analysis = app.var_target.get(), app.var_analysis_target.get()
        view.toggle_cards()
        view.toggle_recording()
        view.toggle_recording()
        app.show_workbench()
        app.show_compact()
        self.size()
        self.assertFalse(view.card_strip.winfo_ismapped())
        self.assertIn('玩家1', view.heading.get())
        self.assertIn('6', view.recent_text.get())
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual((app.var_target.get(), app.var_analysis_target.get()), (target, analysis))

    def test_enter_on_primary_keeps_navigation_and_does_not_settle(self):
        self.initial()
        app, view = self.app, self.app.compact_panel
        app._key_stand()
        app._key_rank('8')
        app._key_rank('3')
        self.size()
        before = app.ctrl.ledger.to_list()
        view.flow_primary.focus_force()
        for key in ('Return', 'space'):
            view.flow_primary.event_generate('<KeyPress>', keysym=key)
            view.flow_primary.event_generate('<KeyRelease>', keysym=key)
            app.update()
        self.assertEqual(app.ctrl.ledger.to_list(), before)

    def test_screen_bounds_clamp_and_scrolling_keeps_controls_reachable(self):
        app = self.app
        with patch('blackjack_lab.ui.window_layout.work_area', return_value=(30, 40, 830, 640)):
            app.compact_panel.toggle_recording()
            width, height = self.size()
        self.assertLessEqual(width, 784)
        self.assertLessEqual(height, 552)
        self.assertGreaterEqual(app.winfo_x(), 30)
        self.assertGreaterEqual(app.winfo_y(), 40)
        self.assertGreater(float(app.compact_canvas.cget('scrollregion').split()[-1]), height)
        app.compact_canvas.yview_moveto(1)
        self.size()
        self.assertAlmostEqual(app.compact_canvas.yview()[1], 1)

    def test_invalid_preferences_are_preserved_and_never_restore_rule_facts(self):
        path = self.app.window_layout.path
        self.close()
        invalid = '{"schema":99,"peek_negative":true}'
        path.write_text(invalid, encoding='utf-8')
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.size()
        self.app.window_layout.save()
        self.assertEqual(path.read_text(encoding='utf-8'), invalid)
        self.assertFalse(self.app.var_simple_hole.get())
        self.assertIsNone(self.app.ctrl.state().current)
