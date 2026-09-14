"""Analysis live currency follows observation state without wiping as-of numbers."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.observation.currency import MODE_LIVE, STATUS_FROZEN, STATUS_LIVE, STATUS_STOPPED
from blackjack_lab.ui.app import BlackjackLabApp


class AnalysisObservationUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "analysis-obs.db"
        self.errors = []
        for name, effect in [("showerror", lambda title, text, **kw: self.errors.append(text)),
                             ("showinfo", lambda *a, **kw: None), ("askyesno", lambda *a, **kw: True)]:
            context = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=effect)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db)
        self.addCleanup(self.close)
        self.app.update()

    def close(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def start_hand(self):
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

    def wait_result(self):
        deadline = time.perf_counter() + 7
        while time.perf_counter() < deadline:
            self.app.update()
            if self.app.analysis_panel.last_result:
                return self.app.analysis_panel.last_result
            time.sleep(0.01)
        self.fail("Tk calculation did not complete")

    def displayed(self):
        return self.app.analysis_panel.text.get("1.0", "end")

    def compute(self):
        self.app.analysis_panel.calculate_current()
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertIn("当前 · ", self.displayed())
        return result

    def test_unconfirmed_candidate_keeps_ledger_numbers_but_drops_live_label(self):
        self.start_hand()
        events = self.app.ctrl.ledger.to_list()
        self.compute()
        saved = self.app.analysis_panel.last_result
        self.app.observation.set_unconfirmed(1)
        self.app.update()
        self.assertEqual(events, self.app.ctrl.ledger.to_list())
        self.assertIs(saved, self.app.analysis_panel.last_result)
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertIn("截至已确认记录", self.displayed())
        self.assertNotIn("当前 · ", self.displayed().splitlines()[0])
        self.app.observation.set_unconfirmed(0)
        self.app.update()
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertIn("当前 · ", self.displayed())
        self.assertEqual(self.errors, [])

    def test_live_source_freeze_or_stop_invalidates_live_keeps_history(self):
        self.start_hand()
        self.compute()
        self.app.observation.connect_source("live-1")
        self.app.observation.note_source(STATUS_LIVE)
        self.app.observation.note_source(STATUS_FROZEN)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertIn("截至已确认记录", self.displayed())
        self.assertIsNotNone(self.app.analysis_panel.last_result)
        self.app.analysis_panel.show_history()
        windows = [w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel"]
        self.assertTrue(windows)
        windows[0].destroy()
        self.app.observation.note_source(STATUS_STOPPED)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertEqual(MODE_LIVE, self.app.observation.mode)
        self.assertEqual(self.errors, [])

    def test_overflow_cleared_queue_does_not_auto_restore_until_check(self):
        self.start_hand()
        self.compute()
        self.app.observation.mark_overflow()
        self.app.observation.set_unconfirmed(0)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.app.observation.reconcile(acknowledge_overflow=False)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.app.observation.reconcile(acknowledge_overflow=True)
        self.app.update()
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertIn("当前 · ", self.displayed())
        self.assertEqual(self.errors, [])

    def test_manual_mode_is_not_trapped_by_live_source_guards(self):
        self.start_hand()
        self.compute()
        self.app.observation.note_source(STATUS_FROZEN)
        self.app.update()
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertIn("当前 · ", self.displayed())
        self.assertEqual(STATUS_FROZEN, self.app.observation.revision().source_status)
        self.assertEqual("manual", self.app.observation.mode)
        self.assertEqual(self.errors, [])

    def test_late_async_result_is_not_republished_as_current(self):
        self.start_hand()
        panel = self.app.analysis_panel
        panel.calculate_current()
        self.assertIsNone(panel.last_result)
        self.app.observation.set_unconfirmed(1)
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        self.assertFalse(panel.live_applicable)
        self.assertTrue(panel._observation_moved_during_request)
        self.assertIn("截至已确认记录", self.displayed())
        self.app.observation.set_unconfirmed(0)
        self.app.update()
        self.assertFalse(panel.live_applicable)
        self.assertNotIn("当前 · ", self.displayed().splitlines()[0])
        self.assertEqual(self.errors, [])
