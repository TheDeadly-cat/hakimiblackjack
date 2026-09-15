"""Frozen-policy Monte Carlo is not exact small-shoe optimal EV."""
import unittest

from blackjack_lab.analysis.fixed_policy_mc import (
    INPUT_SCOPE_FIXED_COMPOSITION, INPUT_SCOPE_REMAINING_COUNT_PRIOR,
    POLICY_ALWAYS_STAND, POLICY_LEGAL_UNSPLIT, POLICY_TOY_HARD,
    FixedPolicyError, evaluate_fixed_policy,
)
from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL, counts_from_values
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.research_windows import build_predeal_input
from blackjack_lab.analysis.shoe_windows import legal_unsplit_s17_action
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
        self.assertEqual(INPUT_SCOPE_FIXED_COMPOSITION, report["input_scope"])
        self.assertEqual("nonpositive_supported", report["window_state"])
        self.assertEqual(64, len(report["rules_digest"]))
        self.assertEqual(64, len(report["policy_digest"]))
        self.assertEqual(64, len(report["algorithm_digest"]))
        self.assertNotEqual(report["rules_digest"], report["policy_digest"])
        self.assertNotEqual(report["rules_digest"], report["algorithm_digest"])
        self.assertIsNotNone(report["result_ready_at"])
        self.assertTrue(report["not_exact_optimal"])
        self.assertFalse(report["exact_positive"])
        self.assertFalse(report["desktop_attested"])
        self.assertTrue(report["window_claim_allowed"])
        self.assertTrue(report["statistical_nonpositive"])
        self.assertLessEqual(report["ci_high"], 0)
        self.assertTrue(report["complete_pre_registered_sample"])
        self.assertEqual(0, report["n_failed"])
        self.assertAlmostEqual(report["ev"], 0.0, delta=1e-12)
        self.assertAlmostEqual(report["ev"], oracle["ev"], delta=1e-12)
        self.assertEqual(1.0, report["p_push"])
        self.assertEqual(list(counts_from_values(FOUR_TENS)), report["composition_counts"])
        self.assertFalse(report["rules"]["split"])
        self.assertFalse(report["rules"]["insurance"])
        self.assertEqual(16, report["exact_small_max_remaining"])
        self.assertEqual(16, PREDEAL_MAX_REMAINING)
        self.assertEqual(5.0, report["interactive_exact_budget_seconds"])

    def test_positive_wald_interval_is_statistical_not_exact_optimal_live(self):
        report = evaluate_fixed_policy(
            pack=ACE_THREE_TENS, policy=POLICY_ALWAYS_STAND, n_samples=200, seed=3,
            surrender=None)
        self.assertGreater(report["ev"], 0)
        self.assertEqual(INPUT_SCOPE_FIXED_COMPOSITION, report["input_scope"])
        self.assertTrue(report["window_claim_allowed"])
        self.assertTrue(report["statistical_positive"])
        self.assertEqual("positive_supported", report["window_state"])
        self.assertGreater(report["ci_low"], 0)
        self.assertTrue(report["not_exact_optimal"])
        self.assertFalse(report["exact_positive"])
        self.assertFalse(report["timely"])
        self.assertFalse(report["desktop_attested"])
        self.assertFalse(report["independent_video"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual(64, len(report["rules_digest"]))
        self.assertEqual(POLICY_ALWAYS_STAND, report["policy_id"])
        self.assertEqual("fixed_policy_monte_carlo", report["evaluation_method"])

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
        self.assertEqual(INPUT_SCOPE_FIXED_COMPOSITION, report["input_scope"])
        self.assertFalse(report["exact_positive"])
        self.assertTrue(report["not_exact_optimal"])
        self.assertFalse(report["timely"])
        if report["window_claim_allowed"]:
            self.assertIn(report["window_state"], ("nonpositive_supported", "positive_supported"))
            self.assertTrue(report["statistical_nonpositive"] or report["statistical_positive"])
        else:
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
        self.assertEqual(INPUT_SCOPE_REMAINING_COUNT_PRIOR, report["input_scope"])
        self.assertEqual(0, report["n_failed"])
        self.assertIsNotNone(report["ev"])
        self.assertIsNotNone(report["standard_error"])
        self.assertTrue(report["not_exact_optimal"])
        self.assertFalse(report["window_claim_allowed"])
        self.assertEqual("indeterminate", report["window_state"])
        self.assertFalse(report["statistical_positive"])

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
        self.assertIsNone(report["ev"])
        self.assertIsNone(report["successful_subsample_ev"])
        self.assertFalse(report["window_claim_allowed"])

    def test_cancel_stops_without_dropping_the_plan(self):
        report = evaluate_fixed_policy(
            n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=80, seed=2,
            cancelled=lambda: True, surrender=None)
        self.assertTrue(report["cancelled"])
        self.assertEqual("indeterminate", report["status"])
        self.assertEqual(80, report["n_not_run"])
        self.assertIsNone(report["ev"])
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
        self.assertIn("合法未分牌", note)
        self.assertIn("失败样本", note)

    def test_legal_unsplit_chart_covers_hard_soft_double_and_late_surrender(self):
        self.assertEqual("surrender", legal_unsplit_s17_action([10, 6], 10, surrender="late"))
        self.assertEqual("surrender", legal_unsplit_s17_action([10, 6], 9, surrender="late"))
        self.assertEqual("surrender", legal_unsplit_s17_action([10, 6], 1, surrender="late"))
        self.assertEqual("surrender", legal_unsplit_s17_action([10, 5], 10, surrender="late"))
        self.assertEqual("hit", legal_unsplit_s17_action([10, 6], 10, surrender=None))
        self.assertEqual("double", legal_unsplit_s17_action([9, 2], 6, surrender=None))
        self.assertEqual("hit", legal_unsplit_s17_action([9, 2], 1, surrender=None))
        self.assertEqual("double", legal_unsplit_s17_action([1, 2], 6, surrender=None))
        self.assertEqual("stand", legal_unsplit_s17_action([1, 7], 2, surrender=None))
        self.assertEqual("hit", legal_unsplit_s17_action([1, 7], 9, surrender=None))
        self.assertEqual("stand", legal_unsplit_s17_action([10, 7], 1, surrender=None))
        self.assertEqual("hit", legal_unsplit_s17_action(
            [10, 6], 10, surrender="late", can_surrender=False))

    def test_legal_unsplit_late_surrender_tracks_six_card_oracle_better_than_stand(self):
        stand = evaluate_fixed_policy(
            pack=SIX_CARD_PACK, policy=POLICY_ALWAYS_STAND, n_samples=400, seed=11,
            surrender="late")
        legal = evaluate_fixed_policy(
            pack=SIX_CARD_PACK, policy=POLICY_LEGAL_UNSPLIT, n_samples=400, seed=11,
            surrender="late")
        exact = calculate(build_predeal_input(
            counts=counts_from_values(SIX_CARD_PACK),
            rules=research_rules(6, surrender="late")))
        self.assertAlmostEqual(exact["ev"], 5 / 36, delta=1e-9)
        self.assertAlmostEqual(legal["ev"], 5 / 36, delta=0.08)
        self.assertGreater(abs(stand["ev"] - 5 / 36), abs(legal["ev"] - 5 / 36))
        self.assertEqual(POLICY_LEGAL_UNSPLIT, legal["policy_id"])
        self.assertIn("不分牌", legal["policy_note"])
        self.assertIn("不买保险", legal["policy_note"])
        self.assertTrue(legal["not_exact_optimal"])

    def test_failed_or_unrun_samples_cannot_stand_in_for_the_population(self):
        state = {"seen": 0}

        def cancel_after_two():
            state["seen"] += 1
            return state["seen"] > 2

        report = evaluate_fixed_policy(
            pack=FOUR_TENS, policy=POLICY_ALWAYS_STAND, n_samples=10, seed=1,
            cancelled=cancel_after_two, surrender=None)
        self.assertEqual(2, report["n_ok"])
        self.assertGreater(report["n_not_run"], 0)
        self.assertIsNone(report["ev"])
        self.assertIsNotNone(report["successful_subsample_ev"])
        self.assertAlmostEqual(report["successful_subsample_ev"], 0.0, delta=1e-12)
        self.assertFalse(report["complete_pre_registered_sample"])
        self.assertFalse(report["window_claim_allowed"])
        self.assertEqual("indeterminate", report["window_state"])

    def test_illegal_z_and_boolean_budget_are_rejected_before_sampling(self):
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(pack=FOUR_TENS, n_samples=8, surrender=None, z=True)
        self.assertEqual("ILLEGAL_NUMBER", caught.exception.code)
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(pack=FOUR_TENS, n_samples=8, surrender=None, z=float("nan"))
        self.assertEqual("ILLEGAL_NUMBER", caught.exception.code)
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(pack=FOUR_TENS, n_samples=8, surrender=None, z=0)
        self.assertEqual("ILLEGAL_NUMBER", caught.exception.code)
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(
                pack=FOUR_TENS, n_samples=8, surrender=None, budget_seconds=True)
        self.assertEqual("ILLEGAL_BUDGET", caught.exception.code)
        with self.assertRaises(FixedPolicyError) as caught:
            evaluate_fixed_policy(
                pack=FOUR_TENS, n_samples=8, surrender=None, play_budget_seconds=float("inf"))
        self.assertEqual("ILLEGAL_BUDGET", caught.exception.code)

    def test_full_six_deck_opening_is_fixed_composition_not_a_redraw_prior(self):
        full = evaluate_fixed_policy(
            n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=40, seed=7, surrender=None)
        same_count = evaluate_fixed_policy(
            n_decks=6, remaining=312, policy=POLICY_ALWAYS_STAND, n_samples=40, seed=7,
            surrender=None)
        prior = evaluate_fixed_policy(
            n_decks=6, remaining=208, policy=POLICY_ALWAYS_STAND, n_samples=40, seed=7,
            surrender=None)
        self.assertEqual(INPUT_SCOPE_FIXED_COMPOSITION, full["input_scope"])
        self.assertEqual(INPUT_SCOPE_FIXED_COMPOSITION, same_count["input_scope"])
        self.assertEqual(INPUT_SCOPE_REMAINING_COUNT_PRIOR, prior["input_scope"])
        self.assertEqual(full["composition_counts"], same_count["composition_counts"])
        self.assertEqual(312, sum(full["composition_counts"]))
        self.assertNotEqual(full["algorithm_digest"], prior["algorithm_digest"])
