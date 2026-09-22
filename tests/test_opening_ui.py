import time
import unittest
from unittest.mock import patch

from tests import test_analysis_ui as fixture
from tests.test_opening_ev import synthetic_result
from blackjack_lab.analysis.opening_service import OpeningService, validate_opening_result
from blackjack_lab.ui.app import BlackjackLabApp


class FakeService:
    def __init__(self):
        self.active = self.pending = None

    def start(self, snapshot):
        self.active = {'snapshot': snapshot}

    def poll(self):
        result, self.pending = self.pending, None
        if result is not None:
            self.active = None
        return result

    def cancel(self):
        self.active = self.pending = None

    close = cancel


class TestOpeningTitle(unittest.TestCase):
    close = fixture.TestAnalysisUI.close

    def setUp(self):
        fake = patch('blackjack_lab.ui.opening_estimate.OpeningService', FakeService)
        fake.start()
        self.addCleanup(fake.stop)
        fixture.TestAnalysisUI.setUp(self)
        self.app.act_common_settings()
        self.app.act_new_shoe()
        self.view = self.app.opening_estimate

    def tick(self):
        self.app.after_cancel(self.view._poll_id)
        self.view.poll()

    def publish(self, net):
        self.view.refresh(force=True)
        self.view.service.pending = synthetic_result(self.view.snapshot, net)
        self.tick()

    def test_positive_uncertain_and_negative_title_use_per_original_unit(self):
        self.publish(1)
        self.assertIn('+1.0000/1', self.app.var_opening_ev.get())
        self.assertIn('优势率 +100.00%', self.app.var_opening_ev.get())
        self.assertIn('正EV估算', self.app.var_opening_ev.get())
        self.publish(0)
        self.assertIn('正负待定', self.app.var_opening_ev.get())
        self.publish(-1)
        self.assertIn('负EV估算', self.app.var_opening_ev.get())
        self.assertEqual(self.errors, [])

    def test_record_commit_invalidates_before_redraw_and_late_result_is_rejected(self):
        app = self.app
        app.act_new_round()
        self.publish(1)
        stale = self.view.result
        before = len(app.ctrl.ledger.events)
        with patch.object(app, 'refresh_all', side_effect=RuntimeError('injected redraw failure')):
            app._key_rank('T')
        self.assertEqual(len(app.ctrl.ledger.events), before + 1)
        self.assertNotIn('+1.0000', app.var_opening_ev.get())
        self.assertIsNone(self.view.result)
        self.view.service.pending = stale
        self.tick()
        self.assertNotIn('+1.0000', app.var_opening_ev.get())
        self.assertIsNone(self.view.result)
        app.refresh_all()
        app.act_undo()
        self.view.refresh(force=True)
        self.assertEqual(self.view.snapshot.seed, stale['seed'])
        self.assertNotEqual(self.view.snapshot.input_digest, stale['input_digest'])

    def test_seat_change_and_auto_off_clear_result_and_preserve_ledger(self):
        self.publish(1)
        before = self.app.ctrl.ledger.to_list()
        self.app.var_participants['玩家2'].set(True)
        self.assertIsNone(self.view.result)
        self.assertNotIn('+1.0000', self.app.var_opening_ev.get())
        self.publish(1)
        self.assertEqual(len(self.view.snapshot.participants), 2)
        self.app.analysis_panel.auto.set(True)
        self.app.analysis_panel.auto.set(False)
        self.tick()
        self.assertIsNone(self.view.result)
        self.assertIn('自动计算已关闭', self.app.var_opening_ev.get())
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_title_has_fixed_height_and_recovery_does_not_restore_fake_result(self):
        self.app.update()
        card_y = self.app.compact_panel.card_buttons[0].winfo_rooty()
        self.publish(1)
        self.app.update()
        self.assertEqual(self.app.compact_panel.card_buttons[0].winfo_rooty(), card_y)
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.assertNotIn('+1.0000', self.app.var_opening_ev.get())
        self.assertIsNone(self.app.opening_estimate.result)

    def test_cancel_stays_cancelled_until_new_input_or_explicit_retry(self):
        self.app.analysis_panel.auto.set(True)
        self.publish(1)
        self.app.analysis_panel.cancel()
        self.tick()
        self.assertIsNone(self.view.service.active)
        self.assertIsNone(self.view.result)
        self.assertIn('已取消', self.app.var_opening_ev.get())
        self.publish(1)
        self.assertIn('+1.0000', self.app.var_opening_ev.get())


class TestOpeningWorker(unittest.TestCase):
    setUp = fixture.TestAnalysisUI.setUp
    close = fixture.TestAnalysisUI.close

    def test_real_worker_returns_bound_opening_result_and_cancels(self):
        self.app.act_common_settings()
        self.app.act_new_shoe()
        snapshot = self.app.opening_estimate.current_input()
        service = OpeningService()
        self.addCleanup(service.close)
        service.start(snapshot, budget_seconds=12)
        deadline = time.perf_counter() + 13
        result = None
        while time.perf_counter() < deadline and result is None:
            result = service.poll()
            time.sleep(.01)
        self.assertIsNotNone(result)
        self.assertEqual(result['status'], 'available', result)
        validate_opening_result(result, snapshot)
        service.start(snapshot)
        process = service.active['process']
        service.cancel()
        self.assertIsNone(service.active)
        self.assertTrue(process._closed or not process.is_alive())
