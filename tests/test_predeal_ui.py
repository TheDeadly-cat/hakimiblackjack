"""Analysis panel exposes a separate small-shoe pre-deal button."""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.predeal_contracts import PREDEAL_RESULT_SCHEMA
from blackjack_lab.ui.app import BlackjackLabApp


class PreDealUITest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "predeal-ui.db"
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

    def wait_result(self):
        deadline = time.perf_counter() + 7
        while time.perf_counter() < deadline:
            self.app.update()
            if self.app.analysis_panel.last_result:
                return self.app.analysis_panel.last_result
            time.sleep(0.01)
        self.fail("Tk pre-deal calculation did not complete")

    def test_synthetic_remaining_is_not_current_hand(self):
        panel = self.app.analysis_panel
        self.assertTrue(panel.predeal_button.instate(["disabled"]))
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        self.assertFalse(panel.predeal_button.instate(["disabled"]))
        panel.predeal_button.invoke()
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        self.assertEqual(PREDEAL_RESULT_SCHEMA, result["schema"])
        displayed = panel.text.get("1.0", "end")
        self.assertIn("发牌前开局优势", displayed)
        self.assertIn("合成剩余组成", displayed)
        self.assertNotIn("当前已发手牌条件优势", displayed)
        self.assertNotIn("爆牌概率", displayed)
        self.assertIsNotNone(panel.saved)
        self.assertEqual(self.errors, [])

    def test_dealt_round_disables_ledger_predeal_but_keeps_current_hand(self):
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
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("")
        self.app.update()
        self.assertTrue(panel.predeal_button.instate(["disabled"]))
        self.assertFalse(panel.compute_button.instate(["disabled"]))
        self.assertEqual(self.errors, [])
