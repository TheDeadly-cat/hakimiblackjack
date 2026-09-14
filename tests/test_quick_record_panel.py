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

    def test_offline_review_banner_does_not_disable_valid_confirmation(self):
        from tests.test_realtime_review import fixture, np
        if np is None:
            self.skipTest("optional numpy")
        owner, row, intake = fixture()
        owner.finished = owner.source.finished = False
        owner.source.preview = intake.preview
        owner._latest = row
        self.panel.attach(owner)
        self.panel.feed.poll()
        owner.finished = owner.source.finished = True
        self.panel.feed.enter_replay()
        self.panel.refresh()
        self.assertIn("录像复盘", self.panel.var_current.get())
        self.assertFalse(self.panel.confirm_button.instate(["disabled"]))
        self.panel.confirm_button.invoke()
        self.assertEqual(1, len(self.panel.work.records()))

    def test_unranked_region_window_freezes_frame_and_only_adds_a_draft(self):
        from tests.test_realtime_review import fixture, np
        if np is None:
            self.skipTest("optional numpy")
        owner, row, intake = fixture()
        owner.finished = owner.source.finished = False
        owner.source.preview = intake.preview
        owner._latest = row
        row.recognition.observations[0].reject_reason = "不确定"
        self.panel.attach(owner)
        self.panel.feed.poll()
        window = self.panel.review_unranked()
        self.assertIsNotNone(window)
        self.assertEqual(1, len(window.record["regions"]))
        self.assertFalse(owner.stopped)
        window.add_draft()
        self.assertTrue(self.panel.work.selected.human_requested)
        self.assertEqual([], self.panel.work.records())

    def test_large_source_selected_region_is_visible_after_initial_layout(self):
        import tkinter as tk
        from types import SimpleNamespace
        from blackjack_lab.vision.image_io import write_png_rgb
        path = Path(self.tmp.name) / "wide-source.png"
        write_png_rgb(path, 1850, 520, b"\x90\x90\x90" * (1850*520))
        record = {"source_image":str(path), "regions":[{"bbox":{"x":1250,"y":128,"w":35,"h":25},"reason":"synthetic fixture"}]}
        self.panel.feed = SimpleNamespace(snapshot_unranked=lambda:record, poll=lambda:False,
            owner=SimpleNamespace(source=SimpleNamespace(preview=lambda:None)),
            unranked_count=1, enabled=False, replay_mode=False)
        from blackjack_lab.ui.unranked_review import UnrankedReviewWindow
        window = UnrankedReviewWindow(self.panel)
        self.app.update_idletasks()
        self.app.update()
        self.assertLessEqual(window.canvas.canvasx(0), 1250)
        self.assertGreaterEqual(window.canvas.canvasx(window.canvas.winfo_width()), 1285)
        window.destroy()
        self.panel.feed = None


if __name__ == "__main__":
    unittest.main()
