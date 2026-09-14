"""Synthetic observation-error window contrast. Not an unused-video result."""
import unittest

from blackjack_lab.analysis.observation_error import run_observation_error_study
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL
from blackjack_lab.analysis.shoe_windows import CONSUMPTION_BASIC, CONSUMPTION_STAND, play_round


class ObservationErrorStudyTest(unittest.TestCase):
    def test_delay_cannot_claim_the_first_round_window(self):
        pack = (10, 10, 9, 9, 8, 7, 6, 5)
        report = run_observation_error_study(pack=pack, seed=3, lag_rounds=1, max_rounds=3)
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertFalse(report["independent_video"])
        first = report["rounds"][0]
        self.assertEqual("OBSERVER_LAG", first["delay"]["reason_code"])
        self.assertIsNone(first["delay"].get("ev"))
        if first["truth"].get("status") == "available" and first["truth"].get("ev", 0) > 0:
            self.assertGreaterEqual(report["summary"]["delay"]["false_negative"], 1)
        self.assertIn("delay_missed_positive_rate", report["summary"])
        if report["summary"]["truth_positive"]:
            self.assertGreaterEqual(report["summary"]["delay_missed_positive"], 0)
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])

    def test_rank_error_is_counted_not_used_as_a_window_claim(self):
        pack = (10, 10, 9, 9, 8, 7)
        report = run_observation_error_study(pack=pack, seed=8, max_rounds=2)
        self.assertIn("false_positive", report["summary"]["rank_error"])
        self.assertIn("false_negative", report["summary"]["rank_error"])
        self.assertIn("zero_window_truth", report["summary"])
        self.assertTrue(report["not_a_reliable_window_claim"])

    def test_basic_and_composition_policies_can_consume_different_cards(self):
        pack = [5, 10, 6, 9, 10, 10, 10, 2]
        stood = play_round(pack, policy=CONSUMPTION_STAND, budget_seconds=2.0)
        basic = play_round(pack, policy=CONSUMPTION_BASIC, budget_seconds=2.0)
        self.assertLess(len(basic["remaining"]), len(stood["remaining"]))
