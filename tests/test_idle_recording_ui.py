"""Idle key/button input must not turn setup guidance into a save-failure lock."""
from pathlib import Path
import tempfile
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

from blackjack_lab.ledger.events import CARD_DEALT, SOURCE_SIMULATOR
from blackjack_lab.ui.app import BlackjackLabApp
from scripts.tk_lifecycle import close_app


class IdleRecordingUITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'idle.db'
        self.errors = []
        for name, effect in [('askyesno', lambda *a, **k: True),
                             ('showerror', lambda *a, **k: self.errors.append(a))]:
            context = patch('blackjack_lab.ui.app.messagebox.' + name, side_effect=effect)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db, recording_source=SOURCE_SIMULATOR,
                                   auto_analysis=False, background_recording=True,
                                   recording_process=False)
        self.app.withdraw()
        self.addCleanup(lambda: close_app(self.app, discard_fixture_results=True))

    def wait_saved(self):
        deadline = perf_counter() + 8
        while self.app.recording_busy and perf_counter() < deadline:
            self.app.update()
            sleep(.005)
        self.assertFalse(self.app.recording_busy)
        self.assertFalse(self.app._recording_faults, self.app.recording_fault_message())

    def assert_idle_input_rejected(self, prompt):
        app = self.app
        before = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app._key_rank('A')
        app.act_card('2')
        app._key_hole()
        app.compact_panel.card_buttons[0].invoke()
        self.assertIn(prompt, app.var_recording_save.get())
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), before)
        self.assertIsNone(app._recording_owner)
        self.assertFalse(app._recording_faults)
        self.assertFalse(app.recording_busy)
        self.assertFalse(app.compact_panel.recording_review.winfo_manager())
        self.assertFalse(list(Path(str(self.db) + '.recording-failures').glob('*.json')))
        self.assertFalse(app.compact_panel.flow_primary.instate(['disabled']))
        self.assertTrue(all(b.instate(['disabled']) for b in app.compact_panel.card_buttons))
        self.assertEqual(self.errors, [])

    def test_setup_input_does_not_lock_start_and_first_real_card_saves(self):
        app = self.app
        self.assert_idle_input_rejected('新建牌靴')
        app.select_table_mode('bclc')
        app.act_new_shoe()
        self.assert_idle_input_rejected('开始本轮')
        app.compact_panel.flow_primary.invoke()
        self.assertFalse(app.compact_panel.card_buttons[0].instate(['disabled']))
        app._key_rank('8')
        self.wait_saved()
        dealt = [e.payload['rank'] for e in app.ctrl.store.load_ledger(app.ctrl.session_id).events
                 if e.etype == CARD_DEALT]
        self.assertEqual(dealt, ['8'])
        self.assertFalse(app.compact_panel.recording_review.winfo_manager())
        self.assertEqual(self.errors, [])

    def test_user_pause_does_not_become_a_persistence_fault(self):
        app = self.app
        app.act_new_shoe()
        app.act_new_round()
        app._key_pause()
        before = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app._key_rank('8')
        self.assertFalse(app._recording_faults)
        self.assertFalse(app.recording_busy)
        self.assertIsNone(app._recording_owner)
        self.assertIn('恢复录入', app.var_recording_save.get())
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), before)
        app._key_pause()
        app._key_rank('8')
        self.wait_saved()


if __name__ == '__main__':
    unittest.main()
