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


if __name__ == "__main__":
    unittest.main()
