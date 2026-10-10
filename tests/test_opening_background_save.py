"""An invalidated opening result saves off Tk and survives failed exit saves."""
import threading
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

from tests import test_recording_background_ui as fixture
from blackjack_lab.analysis.opening import build_opening_input
from blackjack_lab.analysis.opening_service import terminal_opening_result
from blackjack_lab.storage.opening_snapshots import algorithm_manifest
from blackjack_lab.ui.read_snapshot import PrefixSnapshot


class OpeningBackgroundSaveTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump

    def request(self):
        app = self.app
        snapshot = build_opening_input(app.ctrl.ledger, ('玩家1',), '玩家1', 'forward')
        result = terminal_opening_result(snapshot, 'f'*32, 'stale', 'controlled invalidated opening')
        attempt = dict(prefix=PrefixSnapshot.capture(app.ctrl.ledger), sources=algorithm_manifest())
        return result, attempt

    def test_slow_save_keeps_window_responsive_and_is_drained_before_close(self):
        app = self.app; owner = app.opening_estimate
        result, attempt = self.request()
        entered, release = threading.Event(), threading.Event()
        original = app.ctrl.opening_store.save
        def held(**data):
            entered.set(); release.wait(5)
            return original(**data)
        try:
            with patch.object(app.ctrl.opening_store, 'save', side_effect=held):
                started = perf_counter(); self.assertIsNone(owner.persist(result, attempt))
                self.assertLess(perf_counter()-started, .15)
                self.assertTrue(entered.wait(2))
                beats=[]
                for _ in range(15):
                    app.after(0, lambda: beats.append(perf_counter())); app.update(); sleep(.01)
                self.assertEqual(len(beats), 15)
                started=perf_counter(); app.on_close()
                self.assertLess(perf_counter()-started, .15)
                app.update(); self.assertTrue(app.winfo_exists())
                self.assertFalse(owner.writer.stopped)
                release.set(); self.pump(lambda: app.exit_flow.phase == 'finished')
        finally:
            release.set()
        self.app=None
        self.assertTrue(owner.writer.stopped)
        saved, damaged=app.ctrl.opening_store.list()
        self.assertFalse(damaged); self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['event_prefix'], attempt['prefix'].to_list())
        self.assertEqual(saved[0]['result']['status'], 'stale')

    def test_failed_save_keeps_original_for_explicit_exit_retry(self):
        app=self.app; owner=app.opening_estimate
        result, attempt=self.request()
        with patch.object(app.ctrl.opening_store, 'save', side_effect=OSError('controlled disk full')):
            self.assertIsNone(owner.persist(result, attempt))
            self.pump(lambda: bool(owner.pending_saves[0].get('save_error')))
            app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_save')
            self.assertEqual(app.exit_flow.pending()['opening'], 1)
            self.assertEqual(owner.pending_saves[0]['event_prefix'].content, attempt['prefix'].content)
        app.exit_flow.retry(); self.pump(lambda: app.exit_flow.phase == 'finished')
        self.app=None
        saved, damaged=app.ctrl.opening_store.list()
        self.assertFalse(damaged); self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['event_prefix'], attempt['prefix'].to_list())

    def test_old_opening_process_join_is_off_tk_and_exit_waits_for_release(self):
        app=self.app; owner=app.opening_estimate
        snapshot=build_opening_input(app.ctrl.ledger, ('玩家1',), '玩家1', 'forward')
        request_id=owner.service.start(snapshot)
        job=owner.service.active; process=job['process']; pid=process.pid
        owner.attempt=dict(snapshot=snapshot, request_id=request_id, started=perf_counter(),
                           prefix=PrefixSnapshot.capture(app.ctrl.ledger), sources=algorithm_manifest())
        entered, release=threading.Event(), threading.Event()
        original=process.join
        def held(timeout=None):
            entered.set(); release.wait(5); return original(timeout)
        try:
            with patch.object(process, 'join', side_effect=held):
                started=perf_counter(); owner.stop_request('stale')
                self.assertLess(perf_counter()-started, .15)
                self.assertIsNone(owner.service.active)
                self.assertTrue(entered.wait(2))
                app.on_close(); app.update()
                self.assertTrue(app.winfo_exists()); self.assertFalse(owner.reaper.stopped)
                release.set(); self.pump(lambda: app.exit_flow.phase == 'finished')
        finally:
            release.set()
        self.app=None
        self.assertTrue(owner.reaper.stopped)
        from scripts.process_metrics import ProcessMetrics
        self.assertFalse(ProcessMetrics().running(pid))
        saved, damaged=app.ctrl.opening_store.list()
        self.assertFalse(damaged); self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['result']['status'], 'stale')


if __name__ == '__main__':
    unittest.main()
