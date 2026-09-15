"""Formal bounded-mean gate: Wald may look certain; Hoeffding need not."""
import math
import unittest

from blackjack_lab.analysis.bounded_mean import bounded_mean_interval as guard


def assess(pays, **kwargs):
    params = dict(planned_n=len(pays), n_failed=0, n_not_run=0,
                  lower_payoff=-2, upper_payoff=2)
    params.update(kwargs)
    return guard(pays, **params)


class BoundedMeanTests(unittest.TestCase):
    def test_two_wins_do_not_prove_positive(self):
        result = assess([1, 1])
        self.assertFalse(result["window_claim_allowed"])
        self.assertFalse(result["statistical_positive"])
        self.assertLessEqual(result["ci_low"], 0)

    def test_two_zero_samples_do_not_prove_zero(self):
        result = assess([0, 0])
        self.assertFalse(result["window_claim_allowed"])
        self.assertFalse(result["statistical_nonpositive"])
        self.assertGreater(result["ci_high"], 0)

    def test_one_sample_is_not_positive(self):
        self.assertFalse(assess([1])["window_claim_allowed"])

    def test_complete_large_positive_constructed_sample(self):
        result = assess([1] * 2000)
        self.assertTrue(result["statistical_positive"])
        self.assertTrue(result["window_claim_allowed"])
        self.assertFalse(result["accepted"])
        self.assertFalse(result["timely"])

    def test_complete_large_negative_constructed_sample(self):
        result = assess([-1] * 2000)
        self.assertTrue(result["statistical_nonpositive"])
        self.assertTrue(result["window_claim_allowed"])

    def test_symmetric_constructed_sample_stays_uncertain(self):
        self.assertFalse(assess([-1, 1] * 1000)["window_claim_allowed"])

    def test_family_adjustment_is_wider(self):
        one = assess([1] * 100, family_size=1)
        many = assess([1] * 100, family_size=100)
        self.assertLess(many["ci_low"], one["ci_low"])
        self.assertEqual(many["alpha_per_claim"], 0.0005)

    def test_failure_nulls_population_mean(self):
        result = assess([1], planned_n=2, n_failed=1)
        self.assertIsNone(result["ev"])
        self.assertEqual(result["successful_subsample_mean"], 1)
        self.assertFalse(result["window_claim_allowed"])

    def test_unrun_nulls_population_mean(self):
        result = assess([1], planned_n=3, n_not_run=2)
        self.assertIsNone(result["ci_low"])
        self.assertFalse(result["window_claim_allowed"])

    def test_zero_completed_is_unavailable(self):
        self.assertIsNone(assess([], planned_n=3, n_not_run=3)["ev"])

    def test_broken_count_accounting_raises(self):
        with self.assertRaises(ValueError):
            assess([1], planned_n=3)

    def test_prior_average_cannot_be_fixed_composition_signal(self):
        self.assertFalse(assess([1] * 2000, fixed_composition=False)["window_claim_allowed"])

    def test_unfrozen_policy_cannot_publish(self):
        self.assertFalse(assess([1] * 2000, fixed_policy=False)["window_claim_allowed"])

    def test_iid_not_declared_cannot_publish(self):
        self.assertFalse(assess([1] * 2000, iid_design_declared=False)["window_claim_allowed"])

    def test_nonfinite_bool_text_payoffs_raise(self):
        for value in [True, math.nan, math.inf, -math.inf, "1"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess([value])

    def test_alpha_is_validated(self):
        for alpha in [0, 1, -1, math.nan, True]:
            with self.subTest(alpha=alpha), self.assertRaises(ValueError):
                assess([0], alpha_family=alpha)

    def test_bounds_are_from_model_not_observed_degenerate_range(self):
        with self.assertRaises(ValueError):
            assess([1, 1], lower_payoff=1, upper_payoff=1)

    def test_payoff_outside_model_rejected(self):
        with self.assertRaises(ValueError):
            assess([3])

    def test_sample_plan_rejects_bool(self):
        with self.assertRaises(ValueError):
            assess([1], planned_n=True)

    def test_threshold_is_not_the_nonpositive_boundary(self):
        result = assess([0.1] * 20000, positive_threshold=0.2)
        self.assertGreater(result["ci_low"], 0)
        self.assertFalse(result["statistical_positive"])
        self.assertFalse(result["window_claim_allowed"])

    def test_tiny_negative_high_is_not_a_no_window_proof(self):
        result = assess([-1e-16] * 2000, positive_threshold=1e-10)
        self.assertFalse(result["statistical_nonpositive"])
        self.assertFalse(result["window_claim_allowed"])
