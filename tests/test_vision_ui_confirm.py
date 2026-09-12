# -*- coding: utf-8 -*-
"""识牌核对入口接入中文工作台；未打开图片时不加载 OpenCV。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.vision_bridge import (
    OP_NEW, ConfirmDecision, VisionReviewSession,
)
from tests.test_vision_bridge import _obs, _result


class TestVisionWorkbench(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "ui.db"
        self.errors = []
        for name, replacement in (
            ("showerror", lambda title, message, **kw: self.errors.append(message)),
            ("showinfo", lambda *a, **kw: None),
            ("askyesno", lambda *a, **kw: False),
        ):
            context = patch("blackjack_lab.ui.app.messagebox." + name, side_effect=replacement)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db)
        self.addCleanup(self.close_app)
        self.app.update()

    def close_app(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def test_open_window_without_cv2_import_requirement(self):
        self.app.act_open_vision()
        self.assertIsNotNone(self.app._vision_win)
        self.app.update()
        self.app._vision_win.destroy()
        self.assertEqual(self.errors, [])

    def test_pending_banner_and_confirm_refreshes_table(self):
        self.app.var_decks.set(6)
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.app.act_new_round()
        sess = VisionReviewSession(self.app.ctrl, _result(_obs("f" * 32, "A")))
        self.app.vision_session = sess
        self.app.refresh_vision_banner()
        self.assertIn("图像待核对", self.app.var_vision.get())
        sess.confirm(ConfirmDecision("f" * 32, OP_NEW, seat="玩家1", confirmed_rank="A"))
        self.app.refresh_all()
        self.assertEqual(self.app.ctrl.state().current.table.players["玩家1"].hands[0].ranks, ["A"])
        last = self.app.lst_timeline.get(self.app.lst_timeline.size() - 1)
        self.assertIn("识牌人工确认", last)
        self.assertEqual(self.errors, [])
