import tempfile
import time
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp


class TestAnalysisUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "analysis-ui.db"
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

    def start(self, n=6, cards=("10", "6"), up="10"):
        self.app.var_decks.set(n)
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app.var_target.set("庄家")
        self.app.refresh_all()
        self.app.act_card(up)
        self.app.act_hidden_card()
        self.app.var_target.set("玩家1")
        self.app.refresh_all()
        for card in cards:
            self.app.act_card(card)
        if up in ("A", "10"):
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

    def test_real_6_7_8_analysis_before_hole_reveal_and_saved_history(self):
        values = []
        for n in (6, 7, 8):
            self.start(n)
            panel = self.app.analysis_panel
            self.assertFalse(panel.compute_button.instate(["disabled"]))
            panel.compute_button.invoke()
            result = self.wait_result()
            self.assertEqual(result["status"], "available")
            if os.environ.get("HAKIMI_OFFLINE_REQUIRED") == "1":
                self.assertTrue(result["worker_network_guard_active"])
            self.assertEqual(self.app.ctrl.state().current.shoe.unrevealed_out, 1)
            self.assertIn("爆牌概率", panel.text.get("1.0", "end"))
            self.assertIn("均为负", panel.text.get("1.0", "end"))
            self.assertIsNotNone(panel.saved)
            values.append(result["actions"]["hit"]["ev"])
            with patch("blackjack_lab.ui.app.simpledialog.askstring", return_value="自建验收轮结束"), \
                    patch.object(self.app, "ask_observation_status", return_value="unknown"):
                self.app.act_end_unsettled()
            self.app.act_end_shoe()
        self.assertEqual(len(set(values)), 3)
        self.assertEqual(len(self.app.ctrl.analysis_store.list()[0]), 3)
        self.assertEqual(self.errors, [])

    def test_pair_shows_partial_comparison_without_recommendation(self):
        self.start(cards=("8", "8"), up="6")
        self.app.analysis_panel.compute_button.invoke()
        result = self.wait_result()
        self.assertTrue(result["partial_comparison"])
        self.assertIsNone(result["highest_ev_action"])
        displayed = self.app.analysis_panel.text.get("1.0", "end")
        self.assertIn("部分动作比较", displayed)
        self.assertNotIn("EV最高", displayed)
        self.assertEqual(self.errors, [])

    def test_change_undo_and_target_cancel_current_worker(self):
        self.start(cards=("2", "3"), up="2")
        panel = self.app.analysis_panel
        panel.calculate_current()
        old_id = panel.service.active["id"]
        self.app.act_card("2")
        self.assertIsNone(panel.service.active)
        self.assertIsNone(panel.last_result)
        panel.calculate_current()
        self.assertNotEqual(panel.service.active["id"], old_id)
        self.app.act_undo()
        self.assertIsNone(panel.service.active)
        panel.calculate_current()
        self.app.var_target.set("玩家2")
        self.app.refresh_all()
        self.assertIsNone(panel.service.active)
        self.assertIsNone(panel.last_result)
        self.assertEqual(self.errors, [])

    def test_timeout_keeps_recording_responsive_without_old_values(self):
        self.start(cards=("2", "3"), up="2")
        panel = self.app.analysis_panel
        panel.start(self.app.ctrl.analysis_input("玩家1"), budget_seconds=0.001)
        result = self.wait_result()
        self.assertEqual(result["status"], "timeout")
        self.assertIsNone(result["probabilities"])
        self.app.act_card("2")
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks, ["2", "3", "2"])
        self.assertEqual(self.errors, [])

    def test_snapshot_write_failure_is_separate_from_event_commit(self):
        self.start()
        panel = self.app.analysis_panel
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app.ctrl.analysis_store, "save", side_effect=OSError("disk full")):
            panel.calculate_current()
            result = self.wait_result()
        self.assertEqual(result["status"], "available")
        self.assertIn("快照未保存", panel.persistence.get())
        self.assertEqual(self.app.ctrl.store.event_count(), before)
        panel.retry_save()
        self.assertIsNotNone(panel.saved)
        self.assertEqual(self.errors, [])

    def test_refresh_failure_says_committed_and_io_failure_says_not_committed(self):
        self.start(cards=("5", "6"), up="2")
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app, "refresh_all", side_effect=RuntimeError("render failure")):
            self.app.act_card("2")
        self.assertIn("数据已成功提交", self.app.var_status.get())
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.app.act_refresh()
        before = self.app.ctrl.store.event_count()
        with patch.object(self.app.ctrl.store, "save_event", side_effect=OSError("write failure")):
            self.app.act_card("3")
        self.assertIn("未提交新的牌面事件", self.app.var_status.get())
        self.assertEqual(self.app.ctrl.store.event_count(), before)
        self.assertEqual(len(self.errors), 2)

    def test_restart_read_original_and_recompute_to_new_snapshot(self):
        self.start()
        panel = self.app.analysis_panel
        panel.calculate_current()
        self.wait_result()
        saved = panel.saved
        original_path = self.app.ctrl.analysis_store.directory / (saved["snapshot_id"] + ".json")
        original_bytes = original_path.read_bytes()
        self.close()
        self.app = BlackjackLabApp(self.db)
        self.app.update()
        entries, damaged = self.app.ctrl.analysis_store.list()
        self.assertEqual(len(entries), 1)
        self.assertEqual(damaged, [])
        self.app.analysis_panel.show_history()
        # Invoke the real history-window recompute command.
        windows = [w for w in self.app.winfo_children() if w.winfo_class() == "Toplevel"]
        self.assertEqual(len(windows), 1)
        buttons = [w for w in windows[0].winfo_children() if w.winfo_class() == "TButton"]
        buttons[0].invoke()
        result = self.wait_result()
        self.assertEqual(result["status"], "available")
        self.assertIn("历史分析", self.app.analysis_panel.text.get("1.0", "end"))
        self.assertEqual(self.app.analysis_panel.saved["recomputed_from"], saved["snapshot_id"])
        self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(len(self.app.ctrl.analysis_store.list()[0]), 2)
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
