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
        self.assertIn("合成研究", displayed)
        self.assertNotIn("当前 · ", displayed.splitlines()[0])
        self.assertNotIn("当前已发手牌条件优势", displayed)
        self.assertNotIn("爆牌概率", displayed)
        self.assertIsNotNone(panel.saved)
        self.assertEqual(self.errors, [])
        self.assertEqual("不支持", self.app.var_surrender.get())
        self.assertIsNone(panel._predeal_snapshot().surrender)
        self.assertAlmostEqual(0.0, result["ev"])
        self.assertNotIn("surrender", result["legal_actions"])
        self.assertIn("无投降", displayed)
        self.assertNotIn("晚投降", displayed)

    def test_late_combobox_six_card_matches_independent_oracle(self):
        self.app.var_surrender.set("late")
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        self.assertEqual("late", panel._predeal_snapshot().surrender)
        panel.predeal_button.invoke()
        result = self.wait_result()
        self.assertAlmostEqual(5 / 36, result["ev"], places=12)
        self.assertIn("surrender", result["legal_actions"])
        displayed = panel.text.get("1.0", "end")
        self.assertIn("晚投降", displayed)
        self.assertEqual(self.errors, [])

    def test_changing_surrender_revokes_synthetic_current_result(self):
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        first_digest = panel._predeal_snapshot().input_digest
        panel.predeal_button.invoke()
        result = self.wait_result()
        self.assertAlmostEqual(0.0, result["ev"])
        self.app.var_surrender.set("late")
        self.app.update()
        self.assertNotEqual(first_digest, panel._current_predeal_digest())
        self.assertTrue(panel._input_moved_during_request)
        self.assertFalse(panel.live_applicable)
        self.assertAlmostEqual(panel.last_result["ev"], 0.0)
        self.assertNotIn("当前 · ", panel.text.get("1.0", "end").splitlines()[0])
        self.assertEqual(self.errors, [])

    def test_locked_shoe_surrender_is_not_overridden_by_combobox(self):
        self.app.act_research_template()
        self.app.act_new_shoe()
        self.assertEqual("late", self.app.ctrl.current_rules().surrender)
        self.app.var_surrender.set("不支持")
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        self.assertEqual("late", panel._predeal_snapshot().surrender)
        self.assertEqual(self.errors, [])

    def test_early_surrender_disables_synthetic_predeal(self):
        self.app.var_surrender.set("early")
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        self.assertTrue(panel.predeal_button.instate(["disabled"]))
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

    def test_editing_composition_revokes_the_old_async_result(self):
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        first_digest = panel._predeal_snapshot().input_digest
        panel.predeal_button.invoke()
        panel.var_predeal_remaining.set("10,9,8,7,6,5")
        self.app.update()
        result = self.wait_result()
        self.assertEqual("available", result["status"])
        self.assertEqual(first_digest, result["input_digest"])
        self.assertNotEqual(first_digest, panel._current_predeal_digest())
        self.assertTrue(panel._input_moved_during_request)
        self.assertFalse(panel.live_applicable)
        displayed = panel.text.get("1.0", "end")
        self.assertNotIn("当前 · ", displayed.splitlines()[0])
        self.assertIn("发牌前开局优势", displayed)
        self.assertEqual(self.errors, [])

    def test_invalid_or_cleared_input_revokes_current_qualification(self):
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        panel.predeal_button.invoke()
        result = self.wait_result()
        saved = result["ev"]
        panel.var_predeal_remaining.set("not-a-pack")
        self.app.update()
        self.assertIs(result, panel.last_result)
        self.assertFalse(panel.live_applicable)
        self.assertTrue(panel._input_moved_during_request)
        self.assertAlmostEqual(panel.last_result["ev"], saved)
        self.assertIsNone(panel._current_predeal_digest())
        panel.var_predeal_remaining.set("")
        self.app.update()
        self.assertFalse(panel.live_applicable)
        self.assertNotIn("当前 · ", panel.text.get("1.0", "end").splitlines()[0])
        self.assertEqual(self.errors, [])

    def test_starting_predeal_replaces_an_in_flight_current_hand(self):
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
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        self.assertFalse(panel.predeal_button.instate(["disabled"]))
        panel.calculate_current()
        panel.predeal_button.invoke()
        deadline = time.perf_counter() + 7
        result = None
        while time.perf_counter() < deadline:
            self.app.update()
            result = panel.last_result
            if result and result.get("schema") == PREDEAL_RESULT_SCHEMA:
                break
            time.sleep(0.01)
        self.assertIsNotNone(result)
        self.assertEqual(PREDEAL_RESULT_SCHEMA, result["schema"])
        self.assertEqual("pre_deal", result["window"])
        displayed = panel.text.get("1.0", "end")
        self.assertIn("合成研究", displayed)
        self.assertIn("发牌前开局优势", displayed)
        self.assertNotIn("当前已发手牌条件优势", displayed)
        self.assertNotIn("当前 · ", displayed.splitlines()[0])
        self.assertFalse(panel.live_applicable)
        self.assertEqual(self.errors, [])

    def test_historical_predeal_recompute_stays_historical_when_composition_changes(self):
        panel = self.app.analysis_panel
        panel.var_predeal_remaining.set("10,10,9,9,8,7")
        self.app.update()
        panel.predeal_button.invoke()
        first = self.wait_result()
        saved = panel.saved
        self.assertIsNotNone(saved)
        panel.var_predeal_remaining.set("10,9,8,7,6,5")
        self.app.update()
        panel.start(self.app.ctrl.recompute_input(saved), saved["snapshot_id"])
        result = self.wait_result()
        self.assertEqual(first["input_digest"], result["input_digest"])
        displayed = panel.text.get("1.0", "end")
        self.assertIn("历史分析", displayed)
        self.assertNotIn("当前 · ", displayed.splitlines()[0])
        self.assertFalse(panel.live_applicable)
        self.assertFalse(panel.saved["timely_live_claim"])
        self.assertEqual(panel.saved["recomputed_from"], saved["snapshot_id"])
        self.assertEqual(self.errors, [])

