"""Actual Tk controls, focused input, immutable selection and analysis invalidation."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.quick_record_panel import QuickRecordPanel


class QuickPanelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.errors = []
        for name, effect in [("showerror", lambda *a, **kw: self.errors.append(a)),
                             ("showinfo", lambda *a, **kw: None), ("askyesno", lambda *a, **kw: True)]:
            context = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=effect)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(Path(self.tmp.name) / "ui.db")
        self.addCleanup(self.app.on_close)
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.panel = QuickRecordPanel(self.app, register_hotkey=False)
        self.app._quick_panel = self.panel
        self.panel.toggle()
        self.app.update()
        self.panel.focus_force()
        self.app.update()

    def key(self, name, release=True):
        self.panel.event_generate("<KeyPress>", keysym=name)
        self.app.update()
        if release:
            self.panel.event_generate("<KeyRelease>", keysym=name)
            self.app.update()

    def test_rank_key_changes_draft_enter_commits_once_even_held(self):
        self.key("k")
        self.assertEqual("K", self.panel.work.selected.rank)
        self.assertEqual(0, len(self.panel.work.records()))
        self.key("Return", release=False)
        self.key("Return", release=False)
        self.assertEqual(1, len(self.panel.work.records()))
        self.assertEqual("K", self.panel.work.records()[0]["rank"])
        self.assertFalse(self.errors)

    def test_correct_previous_card_updates_ledger_and_clears_current_analysis(self):
        self.key("6")
        self.key("Return")
        original = self.panel.work.records()[0]["event_id"]
        self.panel.previous()
        self.key("k")
        self.panel.var_reason.set("原图为 K")
        analysis = self.app.analysis_panel
        analysis.last_result = {"sentinel": "old current result"}
        self.panel.last_commit_click = 0
        self.key("Return")
        self.assertEqual(original, self.panel.work.records()[0]["event_id"])
        self.assertEqual("K", self.panel.work.records()[0]["rank"])
        self.assertIsNone(analysis.last_result)
        self.assertEqual(24, self.app.ctrl.state().current.shoe.remaining["6"])

    def test_entry_and_collapsed_strip_do_not_receive_rank_commands(self):
        self.panel.reason_entry.focus_force()
        self.app.update()
        self.panel.reason_entry.event_generate("<KeyPress>", keysym="k")
        self.app.update()
        self.assertEqual("k", self.panel.var_reason.get())
        self.assertIsNone(self.panel.work.selected)
        self.panel.toggle()
        self.key("a")
        self.key("Return")
        self.assertEqual(0, len(self.panel.work.records()))

    def test_two_candidates_stay_pinned_and_enter_repeat_cannot_consume_next(self):
        from blackjack_lab.vision.image_io import write_png_rgb
        path = Path(self.tmp.name) / "source.png"
        write_png_rgb(path, 8, 8, b"\xff\xff\xff" * 64)
        self.panel.work.connect("demo", lambda: "")
        first = self.panel.work.enqueue(key="a", rank="3", original={}, source_image=path, crop_image=path)
        self.panel.refresh()
        self.panel.work.enqueue(key="b", rank="3", original={}, source_image=path, crop_image=path)
        self.panel.refresh()
        self.assertEqual(first.draft_id, self.panel.displayed_id)
        self.key("Return", release=False)
        self.panel.last_commit_click = 0
        self.key("Return", release=False)
        self.assertEqual(1, len(self.panel.work.records()))
        self.assertEqual(1, len(self.panel.work.pending))

    def test_owned_window_styles_do_not_allow_click_through(self):
        if not self.panel.native_status.get("supported"):
            self.skipTest("Windows only")
        self.assertFalse(self.panel.native_status["ex_style"] & 0x20)
        self.assertFalse(self.panel.native_status["ex_style"] & 0x08000000)
        self.panel.toggle()
        self.assertTrue(self.panel.native_status["ex_style"] & 0x08000000)
        self.assertFalse(self.panel.native_status["ex_style"] & 0x20)

    def test_invalid_history_selection_does_not_claim_an_earlier_commit_as_new(self):
        self.app.lst_timeline.selection_clear(0, "end")
        before = self.app.ctrl.commit_revision
        self.app.act_detailed_card_correction()
        self.assertEqual(before, self.app.ctrl.commit_revision)
        self.assertIn("未提交新的牌面事件", self.errors[-1][1])


if __name__ == "__main__":
    unittest.main()
