"""Background card receipts still drive actual automatic EV and saved output."""
import unittest
from tests import test_recording_background_ui as fixture


class BackgroundAutoAnalysisTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved

    def test_automatic_current_ev_runs_after_card_receipts_and_retains_saved_prefix(self):
        app=self.app; app.analysis_panel.auto.set(True)
        for rank in ('9','6','2'):
            app._key_rank(rank)
        self.wait_saved()
        self.pump(lambda: app.analysis_panel.last_result is not None, timeout=12)
        result=app.analysis_panel.last_result
        self.assertEqual(result['status'], 'available', result.get('reason'))
        self.assertIn('double', result['actions'])
        self.assertIsNotNone(app.analysis_panel.saved)
        self.assertEqual(result['input']['through_seq'], app.ctrl.ledger.events[-1].seq)
        self.assertEqual(result['input']['prefix_digest'], app.ctrl.read_prefix().prefix_digest)
        self.assertEqual(result['input']['session_id'], app.ctrl.session_id)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())


if __name__ == '__main__':
    unittest.main()
