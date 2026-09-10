"""Proposed PR #1 regression tests for ee485dd.

These are review handoff tests, NOT part of the reported 154-test CI run.
This review only syntax-checked this file; it did not run these tests against
an entire checkout. Run from a full repository root with Tk available:
    python -m unittest discover -s review_tests -p 'test_v02a_review_regressions.py' -v
Use Windows, or an appropriately configured display/Xvfb on Linux.
All records and databases are synthetic and temporary.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.contracts import research_rules, InputUnavailable
from blackjack_lab.analysis.information import build_input
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.ui.app import BlackjackLabApp


class TestRoundIntegrityReview(unittest.TestCase):
    def test_missing_initial_card_in_unsettled_round_blocks_next_round_analysis(self):
        ledger = EventLedger("review-missing-initial-card")
        ledger.start_session("synthetic review fixture")
        ledger.create_shoe(research_rules(6))
        ledger.start_round(["玩家1"])
        ledger.deal("庄家", "9")
        ledger.deal("庄家", "8")
        ledger.deal("玩家1", "10")
        # One mandatory initial player card was not observed/recorded.
        # Allow continued recording; do NOT declare the shoe precisely known.
        ledger.end_round(settle=False, reason="玩家第二张初始牌漏录，无法补齐")
        ledger.start_round(["玩家1"])
        ledger.deal("庄家", "10")
        ledger.deal("庄家", hidden=True)
        ledger.deal("玩家1", "10")
        ledger.deal("玩家1", "6")
        ledger.peek_negative()
        with self.assertRaises(InputUnavailable):
            build_input(ledger, "玩家1")


class TestAnalysisLifecycleReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.errors = []
        for name, effect in (
            ("showerror", lambda title, message, **kw: self.errors.append(message)),
            ("showinfo", lambda *args, **kwargs: None),
            ("askyesno", lambda *args, **kwargs: True),
        ):
            mocked = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=effect)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.app = BlackjackLabApp(Path(self.tmp.name) / "review.db")
        self.addCleanup(self.close_app)
        self.app.update()
        self.app.var_decks.set(6)
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app.var_target.set("庄家")
        self.app.refresh_all()
        self.app.act_card("10")
        self.app.act_hidden_card()
        self.app.var_target.set("玩家1")
        self.app.refresh_all()
        self.app.act_card("10")
        self.app.act_card("6")
        self.app.act_peek_negative()
        self.app.update()
        self.assertEqual(self.errors, [])

    def close_app(self):
        if self.app is not None:
            self.app.on_close()
            self.app = None

    def wait_result(self, timeout=7.0):
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            self.app.update()
            result = self.app.analysis_panel.last_result
            if result is not None:
                return result
            time.sleep(0.01)
        self.fail("analysis did not return within test deadline")

    def commit_with_render_failure(self):
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("review-render-failure")):
            self.app.act_card("2")
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.assertIn("数据已成功提交", self.app.var_status.get())

    def test_committed_input_invalidates_inflight_request_even_if_render_fails(self):
        panel = self.app.analysis_panel
        panel.calculate_current()
        self.assertIsNotNone(panel.service.active)
        self.commit_with_render_failure()
        # Cancellation may alternatively be implemented by a publication-time
        # fresh-revision check. This assertion specifies the preferred immediate
        # invalidation contract. Do not merely change this to accept stale output.
        self.assertIsNone(panel.service.active)
        self.assertIsNone(panel.last_result)

    def test_committed_input_removes_displayed_result_even_if_render_fails(self):
        panel = self.app.analysis_panel
        panel.calculate_current()
        result = self.wait_result()
        self.assertEqual(result["status"], "available")
        old_seq = result["input"]["through_seq"]
        self.commit_with_render_failure()
        self.assertGreater(self.app.ctrl.ledger.events[-1].seq, old_seq)
        self.assertIsNone(panel.last_result)
        # An alternative implementation may retain a historical object, but
        # it must be explicitly stale/historical, never displayed as current.

    def test_cancel_clears_scheduled_auto_request(self):
        panel = self.app.analysis_panel
        panel.auto.set(True)
        with patch.object(panel, "calculate_current") as automatic_call:
            self.app.act_card("2")
            self.assertIsNotNone(panel._auto_id)
            panel.cancel()
            deadline = time.perf_counter() + 0.5
            while time.perf_counter() < deadline:
                self.app.update()
                time.sleep(0.01)
            automatic_call.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
