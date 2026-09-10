"""Local reproductions of review R1-R3; supplied handoff assertions are separate."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import InputUnavailable, research_rules
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.ledger.ledger import EventLedger
from tests import test_analysis_ui as ui_fixture


def next_round(ledger):
    ledger.start_round(["玩家1"])
    ledger.deal("庄家", "10")
    ledger.deal("庄家", hidden=True)
    ledger.deal("玩家1", "10")
    ledger.deal("玩家1", "6")
    ledger.peek_negative()


def first_round(complete=False):
    ledger = EventLedger("review-synthetic")
    ledger.start_session("自建审查回归，非平台记录")
    ledger.create_shoe(research_rules())
    ledger.start_round(["玩家1"])
    ledger.deal("庄家", "9")
    ledger.deal("庄家", "8")
    ledger.deal("玩家1", "10")
    if complete:
        ledger.deal("玩家1", "6")
    return ledger


class TestCrossRoundObservation(unittest.TestCase):
    def test_missing_initial_card_survives_unsettled_end_and_next_round(self):
        ledger = first_round()
        ledger.end_round(settle=False, reason="任意文案不能决定资格")
        next_round(ledger)
        with self.assertRaises(InputUnavailable):
            build_input(ledger, "玩家1")


class TestAnalysisLifecycleReview(unittest.TestCase):
    setUp = ui_fixture.TestAnalysisUI.setUp
    close = ui_fixture.TestAnalysisUI.close
    start = ui_fixture.TestAnalysisUI.start
    wait_result = ui_fixture.TestAnalysisUI.wait_result

    def test_displayed_result_invalidates_before_failed_refresh(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        self.wait_result()
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("render failure")):
            self.app.act_card("2")
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.assertIn("数据已成功提交", self.app.var_status.get())
        self.assertIsNone(panel.last_result)
        self.assertNotIn("EV单位", panel.text.get("1.0", "end"))

    def test_inflight_result_cannot_publish_after_commit_and_failed_refresh(self):
        self.start(cards=("2", "3"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        old = calculate(self.app.ctrl.analysis_input("玩家1"))
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("render failure")):
            self.app.act_card("2")
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.assertIn("数据已成功提交", self.app.var_status.get())
        self.assertIsNone(panel.service.active)
        with patch.object(panel.service, "poll", return_value=old):
            panel._poll()
        self.assertIsNone(panel.last_result)
        self.assertEqual(self.app.ctrl.analysis_store.list()[0], [])

    def test_cancel_removes_queued_automatic_calculation(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.auto.set(True)
        self.app.act_card("2")
        self.assertIsNotNone(panel._auto_id)
        with patch.object(panel.service, "start", wraps=panel.service.start) as start:
            panel.cancel()
            deadline = time.perf_counter() + .4
            while time.perf_counter() < deadline:
                self.app.update()
                time.sleep(.01)
            start.assert_not_called()
        self.assertIsNone(panel._auto_id)

    def pump(self, seconds=.4):
        deadline = time.perf_counter() + seconds
        while time.perf_counter() < deadline:
            self.app.update()
            time.sleep(.01)

    def test_turning_off_auto_removes_queued_callback_and_manual_still_works(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.auto_button.invoke()
        self.app.act_card("2")
        self.assertIsNotNone(panel._auto_id)
        with patch.object(panel.service, "start", wraps=panel.service.start) as start:
            panel.auto_button.invoke()
            self.pump()
            start.assert_not_called()
        self.assertIsNone(panel._auto_id)
        panel.compute_button.invoke()
        self.assertEqual(self.wait_result()["input"]["player"], (5, 6, 2))

    def test_cancel_after_failed_redraw_does_not_restart_on_next_poll(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.auto.set(True)
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("render failure")):
            self.app.act_card("2")
        with patch.object(panel.service, "start", wraps=panel.service.start) as start:
            panel.cancel_button.invoke()
            self.pump()
            start.assert_not_called()
        self.app.act_card("3")  # A new input can schedule a new request.
        self.assertEqual(self.wait_result()["input"]["player"], (5, 6, 2, 3))

    def test_publish_guard_reads_real_token_when_notifications_are_missing(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        old = calculate(self.app.ctrl.analysis_input("玩家1"), request_id=panel.request_id)
        cached = panel.request_key
        with patch.object(self.app.ctrl, "_context_listeners", []):
            self.app.ctrl.deal_shown("玩家1", "2")
        self.assertEqual(panel.request_key, cached)
        self.assertEqual(panel.context_key, cached)
        with patch.object(panel.service, "poll", return_value=old):
            panel._poll()
        self.assertIsNone(panel.last_result)
        self.assertIsNone(panel.service.active)
        self.assertEqual(self.app.ctrl.analysis_store.list()[0], [])

    def test_each_early_redraw_failure_clears_result_then_new_prefix_can_compute(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        for method in ("refresh_hands", "refresh_table"):
            panel.calculate_current()
            old = self.wait_result()
            before = self.app.ctrl.store.event_count()
            with patch.object(self.app, method, side_effect=RuntimeError(method)):
                self.app.act_card("2")
            self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
            self.assertIsNone(panel.last_result)
            self.assertNotIn("EV单位", panel.text.get("1.0", "end"))
            self.assertIn("数据已成功提交", self.app.var_status.get())
            self.app.act_refresh()
            panel.calculate_current()
            new = self.wait_result()
            self.assertGreater(new["input"]["through_seq"], old["input"]["through_seq"])

    def test_target_and_session_changes_invalidate_without_redraw(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        for change in (lambda: self.app.var_target.set("玩家2"),
                       lambda: self.app.ctrl.load_session(self.app.ctrl.session_id)):
            self.app.var_target.set("玩家1")
            panel.calculate_current()
            self.wait_result()
            change()
            self.assertIsNone(panel.last_result)
            self.assertNotIn("EV单位", panel.text.get("1.0", "end"))

    def test_import_invalidates_current_request_without_redraw(self):
        from blackjack_lab.storage.export import export_json
        self.start(cards=("2", "3"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        path = export_json(self.app.ctrl.ledger, Path(self.tmp.name)/"same-session.json")
        self.app.ctrl.import_file(path)
        self.assertIsNone(panel.service.active)
        self.assertIsNone(panel.last_result)

    def test_history_recompute_remains_historical_when_current_input_changes(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        original = self.wait_result()
        saved = panel.saved
        original_path = self.app.ctrl.analysis_store.directory/(saved["snapshot_id"]+".json")
        original_bytes = original_path.read_bytes()
        panel.start(self.app.ctrl.recompute_input(saved), saved["snapshot_id"])
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("render failure")):
            self.app.act_card("2")
        self.app.var_target.set("玩家2")
        result = self.wait_result()
        self.assertEqual(result["input_digest"], original["input_digest"])
        self.assertIn("历史分析", panel.text.get("1.0", "end"))
        self.assertIn("不代表当前输入", panel.text.get("1.0", "end"))
        self.assertEqual(panel.saved["recomputed_from"], saved["snapshot_id"])
        self.assertNotEqual(panel.saved["snapshot_id"], saved["snapshot_id"])
        self.assertEqual(original_path.read_bytes(), original_bytes)

    def test_failed_database_write_keeps_still_current_result(self):
        self.start(cards=("5", "6"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        result = self.wait_result()
        before = self.app.ctrl.context_token
        with patch.object(self.app.ctrl.store, "save_event", side_effect=OSError("disk full")):
            self.app.act_card("2")
        self.assertEqual(self.app.ctrl.context_token, before)
        self.assertEqual(panel.last_result, result)
        self.assertIn("未提交新的牌面事件", self.app.var_status.get())


if __name__ == "__main__":
    unittest.main()
