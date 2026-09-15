"""Analysis live currency follows observation state without wiping as-of numbers."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.research_windows import knowledge_revision_token
from blackjack_lab.observation.currency import (
    MODE_LIVE, MODE_MANUAL, STATUS_FROZEN, STATUS_LIVE, STATUS_STOPPED,
)
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

    def go_live(self, generation="live-1"):
        self.app.observation.connect_source(generation)
        self.app.observation.note_source(STATUS_LIVE)
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

    def compute(self, expect_live=False):
        self.app.analysis_panel.calculate_current()
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        if expect_live:
            self.assertTrue(self.app.analysis_panel.live_applicable)
            self.assertIn("当前 · ", self.displayed())
        else:
            self.assertFalse(self.app.analysis_panel.live_applicable)
            self.assertNotIn("当前 · ", self.displayed().splitlines()[0])
        return result

    def test_manual_compute_is_asof_not_a_live_table(self):
        self.start_hand()
        self.compute(expect_live=False)
        self.assertIn("截至人工确认记录", self.displayed())
        self.assertEqual(MODE_MANUAL, self.app.observation.mode)
        self.assertEqual(self.errors, [])

    def test_unconfirmed_candidate_keeps_ledger_numbers_but_drops_live_label(self):
        self.start_hand()
        self.go_live()
        events = self.app.ctrl.ledger.to_list()
        self.compute(expect_live=True)
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
        self.go_live()
        self.compute(expect_live=True)
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

    def test_frame_refresh_without_knowledge_change_keeps_live_label(self):
        self.start_hand()
        self.go_live()
        self.compute(expect_live=True)
        before = self.app.analysis_panel.request_observation.knowledge_identity()
        self.app.observation.note_frame(time.perf_counter_ns())
        self.app.update()
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertIn("当前 · ", self.displayed())
        self.assertEqual(before, self.app.observation.revision().knowledge_identity())
        self.assertEqual(self.errors, [])

    def test_source_switch_does_not_restore_the_old_live_label(self):
        self.start_hand()
        self.go_live("live-1")
        self.compute(expect_live=True)
        self.app.observation.connect_source("live-2")
        self.app.observation.note_source(STATUS_LIVE)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertNotIn("当前 · ", self.displayed().splitlines()[0])
        self.assertEqual(self.errors, [])

    def test_overflow_cleared_queue_does_not_auto_restore_until_check(self):
        self.start_hand()
        self.go_live()
        self.compute(expect_live=True)
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
        self.compute(expect_live=False)
        self.app.observation.note_source(STATUS_FROZEN)
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertIn("截至人工确认记录", self.displayed())
        self.assertEqual(STATUS_FROZEN, self.app.observation.revision().source_status)
        self.assertEqual("manual", self.app.observation.mode)
        self.assertEqual(self.errors, [])

    def test_replay_is_not_labeled_as_the_current_live_table(self):
        self.start_hand()
        self.go_live()
        self.compute(expect_live=True)
        self.app.observation.enter_replay()
        self.app.update()
        self.assertFalse(self.app.analysis_panel.live_applicable)
        self.assertIn("录像回放", self.displayed())
        self.assertNotIn("当前 · ", self.displayed().splitlines()[0])
        self.assertEqual(self.errors, [])

    def test_late_async_result_is_not_republished_as_current(self):
        self.start_hand()
        self.go_live()
        panel = self.app.analysis_panel
        panel.calculate_current()
        self.assertIsNone(panel.last_result)
        self.app.observation.set_unconfirmed(1)
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        self.assertFalse(panel.live_applicable)
        self.assertTrue(panel._observation_moved_during_request)
        self.assertIn("截至已确认记录", self.displayed())
        self.assertFalse(panel.saved["timely_live_claim"])
        self.assertEqual(
            knowledge_revision_token(panel.request_observation), result["knowledge_revision"])
        self.assertNotEqual(
            knowledge_revision_token(self.app.observation.revision()), result["knowledge_revision"])
        self.app.observation.set_unconfirmed(0)
        self.app.update()
        self.assertFalse(panel.live_applicable)
        self.assertEqual(self.errors, [])
        self.assertNotIn("当前 · ", self.displayed().splitlines()[0])

    def test_live_current_snapshot_may_claim_timely_manual_cannot(self):
        self.start_hand()
        self.compute(expect_live=False)
        self.assertFalse(self.app.analysis_panel.saved["timely_live_claim"])
        self.go_live()
        self.app.analysis_panel.calculate_current()
        self.wait_result()
        self.assertTrue(self.app.analysis_panel.live_applicable)
        self.assertTrue(self.app.analysis_panel.saved["timely_live_claim"])
        self.assertEqual(
            knowledge_revision_token(self.app.analysis_panel.request_observation),
            self.app.analysis_panel.last_result["knowledge_revision"])
        self.assertIn("当前 · ", self.displayed())
        self.assertEqual(self.errors, [])
