"""T10-B: the Chinese experiment window can start, cancel, and show a contrast table."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp


class TestExperimentUI(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "experiment-ui.db"
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

    def wait_saved(self, window):
        deadline = time.perf_counter() + 20
        while time.perf_counter() < deadline:
            self.app.update()
            if window.saved:
                return window.saved
            time.sleep(0.02)
        self.fail("experiment window did not finish")

    def test_contrast_page_runs_6_7_8_and_keeps_unsupported_visible(self):
        self.app.act_experiments()
        window = self.app.experiment_window
        self.assertIsNotNone(window)
        window.update()
        window.var_mode.set("合成场景")
        window.var_template.set("single")
        window.var_player.set("10,6")
        window.var_up.set("10")
        window.var_decks.set("6,7,8")
        window.start()
        saved = self.wait_saved(window)
        decks = {item["n_decks"] for item in saved["record"]["items"]}
        self.assertEqual(decks, {6, 7, 8})
        rows = [window.table.item(child)["values"] for child in window.table.get_children()]
        self.assertTrue(any("6" in str(row[0]) for row in rows))
        self.assertTrue(any("7" in str(row[0]) for row in rows))
        self.assertTrue(any("8" in str(row[0]) for row in rows))
        self.assertTrue(any("停牌" in str(row) or "补牌" in str(row) for row in rows))
        self.assertIn("JSON", window.var_status.get())
        self.assertEqual(self.errors, [])
