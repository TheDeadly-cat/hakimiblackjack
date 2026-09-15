"""Frozen-policy Monte Carlo is not exact small-shoe optimal EV."""
import unittest

from blackjack_lab.analysis.fixed_policy_mc import (
    POLICY_ALWAYS_STAND, POLICY_TOY_HARD, FixedPolicyError, evaluate_fixed_policy,
)
from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL, counts_from_values
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.research_windows import build_predeal_input
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL
from tests.independent_small_shoe import FOUR_TENS, ACE_THREE_TENS, four_tens
from tests.independent_six_card_oracle import PACK as SIX_CARD_PACK, compute as six_card_oracle


class FixedPolicyMonteCarloTest(unittest.TestCase):
    def test_four_tens_frozen_stand_matches_independent_push(self):
        oracle = four_tens()
        report = evaluate_fixed_policy(
            pack=FOUR_TENS, policy=POLICY_ALWAYS_STAND, n_samples=48, seed=3,
            surrender=None)
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertEqual("fixed_policy_monte_carlo", report["method"])
        self.assertEqual("synthetic-composition", report["source_mode"])
        self.assertFalse(report["timely"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual("indeterminate", report["window_state"])
        self.assertEqual(64, len(report["rules_digest"]))
        self.assertIsNotNone(report["result_ready_at"])
        self.assertTrue(report["not_exact_optimal"])
        self.assertFalse(report["window_claim_allowed"])
        self.assertTrue(report["complete_pre_registered_sample"])
        self.assertEqual(0, report["n_failed"])
        self.assertAlmostEqual(report["ev"], 0.0, delta=1e-12)
        self.assertAlmostEqual(report["ev"], oracle["ev"], delta=1e-12)
        self.assertEqual(16, report["exact_small_max_remaining"])
        self.assertEqual(16, PREDEAL_MAX_REMAINING)
        self.assertEqual(5.0, report["interactive_exact_budget_seconds"])

    def test_positive_wald_interval_is_still_not_a_proven_window(self):
        report = evaluate_fixed_policy(
            pack=ACE_THREE_TENS, policy=POLICY_ALWAYS_STAND, n_samples=200, seed=3,
            surrender=None)
        self.assertGreater(report["ev"], 0)
        self.assertFalse(report["window_claim_allowed"])
        self.assertEqual("indeterminate", report["window_state"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual(64, len(report["rules_digest"]))
        if report["sign_status"] == "point_ci_excludes_zero_positive":
            self.assertGreater(report["ci_low"], 0)

    def test_frozen_stand_is_not_the_six_card_late_surrender_optimum(self):
        mc = evaluate_fixed_policy(
            pack=SIX_CARD_PACK, policy=POLICY_ALWAYS_STAND, n_samples=400, seed=11,
            surrender=None)
        exact = calculate(build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK),
            rules=research_rules(6, surrender="late")))
        oracle_stand = six_card_oracle(False)
        self.assertAlmostEqual(mc["ev"], oracle_stand["ev"], delta=0.08)
        self.assertAlmostEqual(exact["ev"], 5 / 36, delta=1e-9)
        self.assertGreater(abs(exact["ev"] - mc["ev"]), 0.05)
        self.assertTrue(mc["not_exact_optimal"])

    def test_full_six_deck_opening_is_not_all_unsupported(self):
        report = evaluate_fixed_policy(
            n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=200, seed=7, surrender=None)
        self.assertEqual(312, report["physical_remaining"])
        self.assertGreater(report["physical_remaining"], PREDEAL_MAX_REMAINING)
        self.assertIsNotNone(report["ev"])
        self.assertEqual(0, report["n_failed"])
        self.assertEqual(200, report["n_ok"])
        self.assertTrue(report["complete_pre_registered_sample"])
        self.assertIsNotNone(report["standard_error"])
        self.assertNotEqual("PREDEAL_SHOE_TOO_LARGE", report["reason_code"])
        self.assertFalse(report["window_claim_allowed"])
        self.assertEqual("indeterminate", report["window_state"])
        self.assertGreater(report["samples_per_second"], 0)
        self.assertEqual(report["n_ok"], report["samples_attempted"])
        peak = report["peak_rss_bytes"]
        if peak is None:
            self.assertNotEqual("win32", report["platform"])
        else:
            self.assertGreater(peak, 0)
        self.assertEqual(report["platform"], __import__("sys").platform)

    def test_declared_mid_shoe_remaining_is_evaluable(self):
        report = evaluate_fixed_policy(
            n_decks=6, remaining=208, policy=POLICY_ALWAYS_STAND, n_samples=80, seed=9,
            surrender=None)
        self.assertEqual(208, report["physical_remaining"])
        self.assertGreater(report["physical_remaining"], PREDEAL_MAX_REMAINING)
        self.assertEqual(0, report["n_failed"])
        self.assertIsNotNone(report["ev"])
        self.assertIsNotNone(report["standard_error"])
        self.assertTrue(report["not_exact_optimal"])

    def test_seven_and_eight_deck_openings_are_evaluable(self):
        for decks, size in ((7, 364), (8, 416)):
            report = evaluate_fixed_policy(
                n_decks=decks, policy=POLICY_TOY_HARD, n_samples=40, seed=5, surrender=None)
            self.assertEqual(size, report["physical_remaining"])
            self.assertEqual(0, report["n_failed"])
            self.assertIsNotNone(report["ev"])

    def test_exact_optimal_policy_is_refused(self):
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(n_decks=6, policy=PREDEAL_STRATEGY_VERSION, n_samples=8)
        self.assertEqual("OPTIMAL_POLICY_NOT_MC", caught.exception.code)

    def test_float_sample_count_is_rejected(self):
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(n_decks=6, n_samples=8.5)
        self.assertEqual("ILLEGAL_INT", caught.exception.code)

    def test_failed_samples_are_kept_in_the_denominator(self):
        report = evaluate_fixed_policy(
            pack=(10, 9, 8), policy=POLICY_ALWAYS_STAND, n_samples=5, seed=1, surrender=None)
        self.assertEqual(5, report["n_samples_planned"])
        self.assertEqual(5, report["n_failed"])
        self.assertEqual(0, report["n_ok"])
        self.assertEqual("indeterminate", report["status"])
        self.assertEqual(5, len(report["failures"]))
        self.assertFalse(report["complete_pre_registered_sample"])

    def test_cancel_stops_without_dropping_the_plan(self):
        report = evaluate_fixed_policy(
            n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=80, seed=2,
            cancelled=lambda: True, surrender=None)
        self.assertTrue(report["cancelled"])
        self.assertEqual("indeterminate", report["status"])
        self.assertEqual(80, report["n_not_run"])
        self.assertIsNotNone(report["cancel_latency_seconds"])
        self.assertLess(report["cancel_latency_seconds"], 1.0)

    def test_mc_requires_declared_surrender(self):
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(pack=FOUR_TENS, n_samples=8, seed=1)
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)

    def test_mc_records_the_declared_surrender_rule(self):
        none = evaluate_fixed_policy(
            pack=FOUR_TENS, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1, surrender=None)
        late = evaluate_fixed_policy(
            pack=FOUR_TENS, policy=POLICY_ALWAYS_STAND, n_samples=8, seed=1, surrender="late")
        self.assertIsNone(none["surrender"])
        self.assertEqual("late", late["surrender"])
        self.assertAlmostEqual(none["ev"], 0.0, delta=1e-12)
        self.assertAlmostEqual(late["ev"], 0.0, delta=1e-12)

    def test_capability_row_exists(self):
        status, note = CAPABILITY_MATRIX["完整牌靴固定策略离线MC"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("不是精确最优", note)
        self.assertIn("16", note)
