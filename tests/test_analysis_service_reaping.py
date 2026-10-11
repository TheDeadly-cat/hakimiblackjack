"""Cancellation returns before waiting, without overlapping replacement workers."""
import threading
import multiprocessing as mp
from time import perf_counter, sleep
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from blackjack_lab.analysis.service import AnalysisService


class Connection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    def poll(self):
        return False


class ControlledProcess:
    def __init__(self, **kwargs):
        self.started = self.alive = self.closed = False
        self.terminated = False
        self.release = threading.Event()
        self.join_entered = threading.Event()
        self.join_thread = None

    def start(self):
        self.started = self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True

    def join(self, timeout=None):
        self.join_thread = threading.current_thread()
        self.join_entered.set()
        self.release.wait(2)
        self.alive = False

    def kill(self):
        self.alive = False

    def close(self):
        self.closed = True


def snapshot(identity='first'):
    return SimpleNamespace(validate=lambda: None, to_dict=lambda: {'identity': identity},
                           input_digest=identity, rules_digest='rules', engine_version='engine',
                           strategy_version='strategy')


class AnalysisServiceReapingTests(unittest.TestCase):
    def setUp(self):
        self.processes = []

        def process(**kwargs):
            result = ControlledProcess(**kwargs)
            self.processes.append(result)
            return result

        context = SimpleNamespace(Pipe=lambda **kwargs: (Connection(), Connection()), Process=process)
        self.patch = patch('blackjack_lab.analysis.service.mp.get_context', return_value=context)
        self.patch.start()
        self.service = AnalysisService()

    def tearDown(self):
        self.service.close()
        for process in self.processes:
            process.release.set()
        if self.service._retiring:
            self.service._retiring.join(2)
        self.patch.stop()

    def finish_retiring(self):
        self.processes[0].release.set()
        self.service._retiring.join(2)
        self.assertFalse(self.service._retiring.is_alive())

    def test_cancel_invalidates_immediately_and_waits_only_off_caller_thread(self):
        old_id = self.service.start(snapshot())
        process = self.processes[0]
        self.service.cancel()
        self.assertIsNone(self.service.active)
        self.assertEqual(self.service.result['status'], 'cancelled')
        self.assertFalse(self.service.accept_result({'request_id': old_id, 'input_digest': 'first'}))
        self.assertTrue(process.terminated)
        self.assertTrue(process.join_entered.wait(1))
        self.assertIsNot(process.join_thread, threading.current_thread())
        self.assertFalse(process.closed)
        self.assertFalse(self.service.stopped)
        self.finish_retiring()
        self.assertTrue(process.closed)
        self.assertTrue(self.service.stopped)

    def test_replacement_waits_until_previous_worker_is_reaped(self):
        self.service.start(snapshot())
        self.service.start(snapshot('second'))
        pending = self.processes[1]
        self.assertFalse(pending.started)
        self.assertIsNone(self.service.poll())
        self.assertEqual(sum(p.alive for p in self.processes), 1)
        self.finish_retiring()
        self.assertIsNone(self.service.poll())
        self.assertTrue(pending.started)
        self.assertEqual(sum(p.alive for p in self.processes), 1)

    def test_retirement_wait_consumes_original_request_budget(self):
        self.service.start(snapshot())
        request_id = self.service.start(snapshot('second'), budget_seconds=.001)
        self.service.active['start'] -= 1
        result = self.service.poll()
        self.assertEqual(result['request_id'], request_id)
        self.assertEqual(result['status'], 'timeout')
        self.assertEqual(result['reason_code'], 'WALL_TIME_BUDGET')
        self.assertIsNone(result['probabilities'])
        self.assertEqual(result['actions'], {})
        self.assertFalse(self.processes[1].started)
        self.assertTrue(self.processes[1].closed)
        self.assertIsNone(self.service.active)

    def test_close_retires_started_worker_and_discards_unstarted_replacement(self):
        self.service.start(snapshot())
        self.service.start(snapshot('second'))
        self.service.close()
        self.assertFalse(self.service.stopped)
        self.assertTrue(self.processes[1].closed)
        self.assertFalse(self.processes[1].started)
        self.finish_retiring()
        self.assertTrue(self.service.stopped)

    def test_signal_failure_retains_owned_worker_and_blocks_replacement_until_dead(self):
        self.service.start(snapshot())
        old = self.processes[0]
        with patch.object(old, 'terminate', side_effect=OSError('injected signal failure')):
            self.service.start(snapshot('second'))
        pending = self.processes[1]
        self.assertIs(self.service._retiring_job['process'], old)
        self.assertIn('injected signal failure', self.service.cleanup_error)
        self.assertTrue(old.join_entered.wait(1))
        self.assertFalse(pending.started)
        self.assertIsNone(self.service.poll())
        self.assertEqual(sum(process.alive for process in self.processes), 1)
        self.finish_retiring()
        self.assertFalse(old.alive)
        self.assertIsNone(self.service.poll())
        self.assertTrue(pending.started)
        self.assertEqual(sum(process.alive for process in self.processes), 1)
        self.assertIn('injected signal failure', self.service.cleanup_error)


class RealAnalysisServiceReapingTests(unittest.TestCase):
    def test_repeated_real_replacement_keeps_one_owned_worker_and_latest_identity(self):
        from blackjack_lab.analysis.information import build_input
        from tests.test_analysis_integration import example
        original = mp.get_context('spawn')
        processes = []

        def process(**kwargs):
            result = original.Process(**kwargs)
            processes.append(result)
            return result

        context = SimpleNamespace(Pipe=original.Pipe, Process=process)
        service = AnalysisService()
        self.addCleanup(service.close)
        current = build_input(example(cards=('10', '9'), up='6'), '玩家1')
        cancellation_ms = []
        maximum_alive = 0
        try:
            with patch('blackjack_lab.analysis.service.mp.get_context', return_value=context):
                for _ in range(8):
                    started = perf_counter()
                    service.cancel()
                    cancellation_ms.append((perf_counter() - started) * 1000)
                    latest_id = service.start(current)
                    alive = 0
                    for owned in processes:
                        try:
                            alive += int(owned.is_alive())
                        except ValueError:  # Successfully closed by the background reaper.
                            pass
                    maximum_alive = max(maximum_alive, alive)
                    self.assertLessEqual(alive, 1)
                end = perf_counter() + 7
                result = None
                while result is None and perf_counter() < end:
                    result = service.poll()
                    sleep(.005)
                self.assertIsNotNone(result)
                self.assertEqual(result['status'], 'available')
                self.assertEqual(result['request_id'], latest_id)
                self.assertEqual(result['input_digest'], current.input_digest)
                print(f'Actual repeated cancellation: max {max(cancellation_ms):.3f} ms; '
                      f'max owned live workers {maximum_alive}')
        finally:
            service.close()
            end = perf_counter() + 5
            while not service.stopped and perf_counter() < end:
                sleep(.005)
            self.assertTrue(service.stopped)


class AnalysisCancellationExitTests(unittest.TestCase):
    from tests.test_analysis_ui import TestAnalysisUI as _fixture
    setUp = _fixture.setUp
    close = _fixture.close
    start = _fixture.start

    def test_cancel_callback_stays_responsive_and_exit_waits_for_detached_worker(self):
        from tests.test_safe_exit import pump_closed
        self.start(cards=('10', '9'), up='6')
        app = self.app
        app.analysis_panel.calculate_current()
        service = app.analysis_panel.service
        process = service.active['process']
        entered, release = threading.Event(), threading.Event()
        original_join = process.join

        def held_join(timeout=None):
            entered.set()
            release.wait(5)
            return original_join(timeout)

        try:
            with patch.object(process, 'join', side_effect=held_join):
                started = perf_counter()
                app.analysis_panel.cancel()
                elapsed = perf_counter() - started
                self.assertLess(elapsed, .15)
                self.assertTrue(entered.wait(2))
                app.on_close()
                app.update()
                self.assertTrue(app.winfo_exists())
                self.assertFalse(service.stopped)
                release.set()
                pump_closed(app)
                self.app = None
                self.assertTrue(service.stopped)
                print(f'Tk cancellation callback: {elapsed * 1000:.3f} ms; '
                      'exit waited for detached worker')
        finally:
            release.set()

    def test_dead_worker_cleanup_error_allows_return_and_explicit_diagnostic_exit(self):
        from tests.test_safe_exit import pump_closed
        self.start(cards=('10', '9'), up='6')
        app = self.app
        panel = app.analysis_panel
        panel.calculate_current()
        service = panel.service
        pipe = service.active['pipe']
        close = pipe.close
        def cleanup_error():
            close()
            raise OSError('injected dead worker pipe cleanup failure')
        with patch.object(pipe, 'close', side_effect=cleanup_error):
            app.on_close()
            deadline = perf_counter() + 8
            while app.exit_flow.phase != 'needs_save' and perf_counter() < deadline:
                app.update(); sleep(.005)
        self.assertEqual(app.exit_flow.phase, 'needs_save')
        self.assertTrue(service.stopped)
        self.assertIn('injected dead worker pipe cleanup failure', service.cleanup_error)
        self.assertTrue(any('injected dead worker' in error for error in app.exit_flow.errors))
        self.assertFalse(app.exit_flow.discard_button.instate(['disabled']))
        app.exit_flow.return_to_app()
        self.assertFalse(app._closing)
        self.assertIsNone(app.exit_flow)
        self.assertTrue(app.winfo_exists())
        app.on_close()
        deadline = perf_counter() + 8
        while app.exit_flow.phase != 'needs_save' and perf_counter() < deadline:
            app.update(); sleep(.005)
        self.assertEqual(app.exit_flow.phase, 'needs_save')
        app.exit_flow.discard()
        pump_closed(app)
        self.app = None
        self.assertTrue(service.stopped)
        self.assertIn('injected dead worker', service.cleanup_error)


if __name__ == '__main__':
    unittest.main()
