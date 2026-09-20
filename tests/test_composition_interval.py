"""Interval research publishes min/max, never a mean shoe as the window."""
import unittest

from blackjack_lab.analysis.actions import (
    HIT_CONTINUATION_STAND, HIT_CONTINUATION_TOY, solve_counts,
)
from blackjack_lab.analysis.composition_interval import (
    IntervalError, composition_optimal_actions_at_infoset, enumerate_feasible_remaining,
    evaluate_common_policy_interval, evaluate_feasible_interval,
    evaluate_infoset_first_action_maxmin, evaluate_interval,
    evaluate_listed_policy_maxmin, exact_common_policy_ev, pack_from_counts,
    refuse_mean_shoe, refuse_unknown_removal,
)
from blackjack_lab.analysis.research_windows import WINDOW_CURRENT_HAND, WINDOW_PRE_DEAL, counts_from_values
from blackjack_lab.analysis.shoe_windows import CONSUMPTION_PI, CONSUMPTION_STAND
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL
from tests.independent_six_card_oracle import PACK as SIX_CARD_PACK, compute as six_card_oracle
from tests.independent_small_shoe import FOUR_TENS


class CompositionIntervalTest(unittest.TestCase):
    def test_empty_candidates_are_refused_as_mean_shoe(self):
        refused = refuse_mean_shoe()
        self.assertEqual("MEAN_SHOE_FORBIDDEN", refused["reason_code"])
        self.assertTrue(refused["forbids_mean_shoe"])
        self.assertIsNone(refused["published_point_ev"])
        self.assertIsNone(refused["ev"])
        with self.assertRaises(IntervalError) as caught:
            evaluate_interval([])
        self.assertEqual("CANDIDATES_MISSING", caught.exception.code)

    def test_interval_is_min_max_not_the_average(self):
        rich = [10, 10, 10, 9, 8, 7]
        poor = [9, 8, 7, 6, 5, 4]
        report = evaluate_interval([rich, poor], surrender=None)
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertTrue(report["forbids_mean_shoe"])
        self.assertIsNone(report["summary"]["published_point_ev"])
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertFalse(report["independent_video"])
        ev_min = report["summary"]["ev_min"]
        ev_max = report["summary"]["ev_max"]
        mean = report["summary"]["mean_ev_not_published"]
        self.assertIsNotNone(ev_min)
        self.assertIsNotNone(ev_max)
        self.assertLessEqual(ev_min, ev_max)
        self.assertIsNotNone(mean)
        self.assertIsNone(report["summary"]["published_point_ev"])
        self.assertIn("interval_width", report["summary"])
        self.assertAlmostEqual(report["summary"]["interval_width"], ev_max - ev_min, places=12)
        self.assertEqual("diagnostic_candidate_envelope", report["scope"])
        self.assertFalse(report["coverage_complete"])
        self.assertEqual("caller_listed", report["coverage_source"])
        self.assertFalse(report["robust_signal_allowed"])
        self.assertFalse(report["summary"]["zero_window_all"])
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])

    def test_partial_timeout_is_not_a_complete_envelope(self):
        rich = [10, 10, 10, 9, 8, 7]
        huge = [10] * 20
        report = evaluate_interval([rich, huge], coverage_complete=True, surrender=None)
        self.assertFalse(report["coverage_complete"])
        self.assertTrue(report["coverage_claimed_by_caller"])
        self.assertEqual(1, report["summary"]["n_missing"])
        self.assertFalse(report["robust_signal_allowed"])
        self.assertFalse(report["summary"]["zero_window_all"])
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])

    def test_float_candidate_is_rejected(self):
        with self.assertRaises(IntervalError) as caught:
            evaluate_interval([[1.8, 10, 10, 10]], surrender=None)
        self.assertEqual("CANDIDATE_INVALID", caught.exception.code)

    def test_unknown_removal_stays_a_gap(self):
        refused = refuse_unknown_removal()
        self.assertEqual("UNKNOWN_REMOVAL_GAP", refused["reason_code"])
        self.assertTrue(refused["forbids_mean_shoe"])
        self.assertIsNone(refused["published_point_ev"])
        self.assertFalse(refused["robust_signal_allowed"])

    def test_capability_row_forbids_mean_shoe(self):
        status, note = CAPABILITY_MATRIX["未知组成区间研究"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("平均牌靴", note)
        self.assertIn("稳健", note)
        self.assertIn("调用方", note)
        self.assertIn("第一动作", note)

    def test_common_stand_is_not_the_separately_optimized_envelope(self):
        separate = evaluate_interval([list(SIX_CARD_PACK), list(FOUR_TENS)], surrender="late")
        common = evaluate_common_policy_interval(
            [list(SIX_CARD_PACK), list(FOUR_TENS)], policy=CONSUMPTION_STAND, surrender=None)
        self.assertEqual("diagnostic_candidate_envelope", separate["scope"])
        self.assertEqual("common_frozen_policy_envelope", common["scope"])
        self.assertAlmostEqual(separate["summary"]["ev_max"], 5 / 36, delta=1e-9)
        self.assertAlmostEqual(common["summary"]["ev_max"], 0.0, delta=1e-12)
        self.assertAlmostEqual(common["summary"]["ev_min"], 0.0, delta=1e-12)
        self.assertGreater(separate["summary"]["ev_max"] - common["summary"]["ev_max"], 0.1)
        self.assertFalse(common["robust_signal_allowed"])
        self.assertFalse(common["summary"]["verified_robust_positive_lower_bound"])
        self.assertTrue(common["not_exact_optimal"])
        self.assertEqual(CONSUMPTION_STAND, common["evaluation_policy_id"])
        oracle = six_card_oracle(False)
        six_only = evaluate_common_policy_interval(
            [list(SIX_CARD_PACK)], policy=CONSUMPTION_STAND, surrender=None)
        self.assertAlmostEqual(six_only["summary"]["ev_max"], oracle["ev"], delta=1e-12)

    def test_interval_requires_declared_surrender(self):
        with self.assertRaises(IntervalError) as caught:
            evaluate_interval([list(SIX_CARD_PACK)])
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)
        with self.assertRaises(IntervalError) as caught:
            evaluate_common_policy_interval([list(SIX_CARD_PACK)], policy=CONSUMPTION_STAND)
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)
        with self.assertRaises(IntervalError) as caught:
            evaluate_listed_policy_maxmin([list(SIX_CARD_PACK)])
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)
        with self.assertRaises(IntervalError) as caught:
            evaluate_feasible_interval(remaining_total=6, origin_pack=SIX_CARD_PACK)
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)
        with self.assertRaises(IntervalError) as caught:
            exact_common_policy_ev(SIX_CARD_PACK)
        self.assertEqual("SURRENDER_REQUIRED", caught.exception.code)

    def test_no_surrender_interval_does_not_use_late_surrender_ev(self):
        none = evaluate_interval([list(SIX_CARD_PACK)], surrender=None)
        late = evaluate_interval([list(SIX_CARD_PACK)], surrender="late")
        self.assertIsNone(none["surrender"])
        self.assertNotIn("surrender", none["legal_actions"])
        self.assertAlmostEqual(none["summary"]["ev_max"], 0.0, delta=1e-12)
        self.assertEqual("late", late["surrender"])
        self.assertIn("surrender", late["legal_actions"])
        self.assertAlmostEqual(late["summary"]["ev_max"], 5 / 36, delta=1e-9)
        self.assertGreater(late["summary"]["ev_max"] - none["summary"]["ev_max"], 0.1)

    def test_caller_listed_successes_are_not_complete_coverage(self):
        report = evaluate_interval(
            [list(SIX_CARD_PACK), list(FOUR_TENS)], coverage_complete=True, surrender="late")
        self.assertEqual(0, report["summary"]["n_missing"])
        self.assertEqual(2, report["summary"]["n_available"])
        self.assertTrue(report["coverage_claimed_by_caller"])
        self.assertFalse(report["coverage_complete"])
        self.assertEqual("caller_listed", report["coverage_source"])
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])
        self.assertFalse(report["summary"]["verified_common_policy_positive_lower_bound"])

    def test_feasible_enumeration_covers_the_known_six_card_pack(self):
        enumeration = enumerate_feasible_remaining(
            remaining_total=6, origin_pack=SIX_CARD_PACK)
        self.assertEqual(1, enumeration["n_found"])
        self.assertTrue(enumeration["coverage_complete"])
        report = evaluate_feasible_interval(
            remaining_total=6, origin_pack=SIX_CARD_PACK, common_policy=CONSUMPTION_STAND,
            surrender=None)
        self.assertEqual("feasible_enumeration", report["coverage_source"])
        self.assertTrue(report["coverage_complete"])
        self.assertFalse(report["coverage_claimed_by_caller"])
        self.assertAlmostEqual(report["summary"]["ev_min"], 0.0, delta=1e-12)
        self.assertAlmostEqual(report["summary"]["ev_max"], 0.0, delta=1e-12)
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])
        self.assertFalse(report["summary"]["verified_common_policy_positive_lower_bound"])
        self.assertTrue(report["summary"]["verified_common_policy_nonpositive_upper_bound"])
        self.assertFalse(report["robust_signal_allowed"])

    def test_partial_knowledge_enumerates_all_four_card_remainings(self):
        known = (None, None, None, None, None, None, None, None, None, 2)
        enumeration = enumerate_feasible_remaining(
            remaining_total=4, origin_pack=SIX_CARD_PACK, known_remaining=known)
        packs = {tuple(sorted(pack_from_counts(item))) for item in enumeration["counts"]}
        self.assertTrue(enumeration["coverage_complete"])
        self.assertEqual({
            (9, 9, 10, 10),
            (8, 9, 10, 10),
            (7, 9, 10, 10),
            (7, 8, 10, 10),
        }, packs)
        listed = evaluate_common_policy_interval([[10, 10, 9, 9]], policy=CONSUMPTION_STAND,
                                                 coverage_complete=True, surrender=None)
        self.assertFalse(listed["coverage_complete"])
        report = evaluate_feasible_interval(
            remaining_total=4, origin_pack=SIX_CARD_PACK, known_remaining=known,
            common_policy=CONSUMPTION_STAND, surrender=None)
        self.assertEqual(4, report["summary"]["n_candidates"])
        self.assertEqual(2, report["summary"]["n_missing"])
        self.assertFalse(report["coverage_complete"])
        self.assertFalse(report["summary"]["verified_common_policy_positive_lower_bound"])
        self.assertFalse(report["summary"]["verified_common_policy_nonpositive_upper_bound"])

    def test_truncated_enumeration_is_not_coverage(self):
        report = evaluate_feasible_interval(
            remaining_total=6, n_decks=6, common_policy=CONSUMPTION_STAND,
            max_candidates=3, surrender=None)
        self.assertTrue(report["feasible_enumeration"]["truncated"])
        self.assertFalse(report["coverage_complete"])
        self.assertEqual(3, report["summary"]["n_candidates"])
        self.assertFalse(report["summary"]["verified_common_policy_positive_lower_bound"])
        self.assertFalse(report["summary"]["verified_common_policy_nonpositive_upper_bound"])

    def test_float_remaining_total_is_rejected(self):
        with self.assertRaises(IntervalError) as caught:
            enumerate_feasible_remaining(remaining_total=6.0, origin_pack=SIX_CARD_PACK)
        self.assertEqual("ILLEGAL_TOTAL", caught.exception.code)

    def test_opposite_optimal_actions_are_not_a_robust_bound(self):
        tens = [10, 6, 9, 10, 10, 10, 10, 10]
        fives = [10, 6, 9, 5, 5, 5, 5, 5]
        report = evaluate_interval([tens, fives], surrender=None)
        self.assertFalse(report["robust_signal_allowed"])
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])
        self.assertFalse(report["summary"]["zero_window_all"])
        actions = composition_optimal_actions_at_infoset(
            [tens, fives], (10, 6), 9, False, surrender=None)
        self.assertTrue(actions["disagree"])
        self.assertEqual({"stand", "double"}, set(actions["available_actions"]))
        self.assertTrue(actions["composition_optimal_is_not_information_feasible"])
        self.assertFalse(actions["robust_signal_allowed"])
        self.assertFalse(actions["verified_robust_positive_lower_bound"])
        self.assertEqual(WINDOW_CURRENT_HAND, actions["window"])

    def test_infoset_first_action_maxmin_is_not_the_separately_optimized_envelope(self):
        tens = [10, 6, 9, 10, 10, 10, 10, 10]
        fives = [10, 6, 9, 5, 5, 5, 5, 5]
        report = evaluate_infoset_first_action_maxmin(
            [tens, fives], (10, 6), 9, False, surrender=None)
        self.assertEqual("infoset_first_action_maxmin", report["scope"])
        self.assertEqual(WINDOW_CURRENT_HAND, report["window"])
        self.assertNotEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertTrue(report["disagree"])
        self.assertEqual({"stand", "double"}, set(report["available_actions"]))
        self.assertAlmostEqual(report["action_mins"]["stand"], -1.0, places=12)
        self.assertAlmostEqual(report["action_mins"]["double"], -2.0, places=12)
        self.assertAlmostEqual(report["infoset_maxmin_ev"], -1.0, places=12)
        self.assertEqual("stand", report["chosen_action"])
        self.assertNotEqual("double", report["chosen_action"])
        self.assertLess(report["infoset_maxmin_ev"], 2.0)
        self.assertFalse(report["robust_signal_allowed"])
        self.assertFalse(report["summary"]["verified_robust_positive_lower_bound"])
        self.assertFalse(report["summary"]["verified_infoset_maxmin_positive_lower_bound"])
        self.assertFalse(report["coverage_complete"])
        self.assertTrue(report["hit_continuation_is_composition_optimal"])
        self.assertFalse(report["hit_continuation_is_information_feasible"])
        self.assertEqual("composition-optimal", report["hit_continuation"])
        self.assertIsNone(report["summary"]["published_point_ev"])
        with self.assertRaises(IntervalError) as caught:
            evaluate_infoset_first_action_maxmin([], (10, 6), 9, False, surrender=None)
        self.assertEqual("CANDIDATES_MISSING", caught.exception.code)
        skipped = evaluate_infoset_first_action_maxmin(
            [tens, [2, 2, 2, 2], fives], (10, 6), 9, False, surrender=None)
        self.assertEqual(1, skipped["summary"]["n_not_compatible"])
        self.assertAlmostEqual(skipped["infoset_maxmin_ev"], -1.0, places=12)

    def test_frozen_hit_continuation_is_not_composition_optimal(self):
        tens = [10, 2, 9, 4, 10, 10, 10, 10]
        fives = [10, 2, 9, 4, 5, 5, 5, 5]
        optimal = evaluate_infoset_first_action_maxmin(
            [tens, fives], (10, 2), 9, False, surrender=None)
        frozen = evaluate_infoset_first_action_maxmin(
            [tens, fives], (10, 2), 9, False, surrender=None,
            hit_continuation=HIT_CONTINUATION_STAND)
        toy = evaluate_infoset_first_action_maxmin(
            [tens, fives], (10, 2), 9, False, surrender=None,
            hit_continuation=HIT_CONTINUATION_TOY)
        self.assertTrue(optimal["hit_continuation_is_composition_optimal"])
        self.assertFalse(frozen["hit_continuation_is_composition_optimal"])
        self.assertTrue(frozen["hit_continuation_is_information_feasible"])
        self.assertEqual(HIT_CONTINUATION_STAND, frozen["hit_continuation"])
        self.assertEqual(HIT_CONTINUATION_TOY, toy["hit_continuation"])
        opt_fives = next(row for row in optimal["rows"] if row["index"] == 1)
        frozen_fives = next(row for row in frozen["rows"] if row["index"] == 1)
        toy_fives = next(row for row in toy["rows"] if row["index"] == 1)
        self.assertAlmostEqual(opt_fives["actions"]["hit"], -0.2, places=12)
        self.assertAlmostEqual(frozen_fives["actions"]["hit"], -1.0, places=12)
        self.assertAlmostEqual(toy_fives["actions"]["hit"], -0.6, places=12)
        self.assertGreater(opt_fives["actions"]["hit"], frozen_fives["actions"]["hit"])
        remaining_fives = [4, 5, 5, 5, 5]
        composition = solve_counts(
            counts_from_values(remaining_fives), (10, 2), 9, False,
            actions=("stand", "hit"))
        stand_after_hit = solve_counts(
            counts_from_values(remaining_fives), (10, 2), 9, False,
            actions=("stand", "hit"), hit_continuation=HIT_CONTINUATION_STAND)
        self.assertNotAlmostEqual(
            composition["actions"]["hit"]["ev"], stand_after_hit["actions"]["hit"]["ev"],
            delta=1e-9)
        self.assertAlmostEqual(
            composition["actions"]["stand"]["ev"], stand_after_hit["actions"]["stand"]["ev"],
            places=12)
        self.assertFalse(frozen["robust_signal_allowed"])
        self.assertFalse(frozen["summary"]["verified_robust_positive_lower_bound"])
        self.assertNotEqual(WINDOW_PRE_DEAL, frozen["window"])
        with self.assertRaises(IntervalError) as caught:
            evaluate_infoset_first_action_maxmin(
                [tens, fives], (10, 2), 9, False, surrender=None,
                hit_continuation="mean-shoe")
        self.assertEqual("HIT_CONTINUATION_INVALID", caught.exception.code)

    def test_listed_maxmin_is_not_the_separately_optimized_envelope(self):
        diagnostic = evaluate_interval([list(SIX_CARD_PACK)], surrender="late")
        maxmin = evaluate_listed_policy_maxmin([list(SIX_CARD_PACK)], surrender="late")
        self.assertEqual("listed_frozen_policy_maxmin", maxmin["scope"])
        self.assertAlmostEqual(diagnostic["summary"]["ev_max"], 5 / 36, places=12)
        self.assertAlmostEqual(maxmin["listed_maxmin_ev"], 0.0, places=12)
        self.assertNotEqual(maxmin["listed_maxmin_ev"], diagnostic["summary"]["ev_max"])
        self.assertFalse(maxmin["robust_signal_allowed"])
        self.assertFalse(maxmin["summary"]["verified_robust_positive_lower_bound"])
        self.assertFalse(maxmin["summary"]["verified_listed_maxmin_positive_lower_bound"])
        self.assertFalse(maxmin["coverage_complete"])
        self.assertIn(CONSUMPTION_STAND, maxmin["policies"])
        with self.assertRaises(IntervalError) as caught:
            evaluate_listed_policy_maxmin([list(SIX_CARD_PACK)], policies=(CONSUMPTION_PI,))
        self.assertEqual("OPTIMAL_NOT_COMMON_POLICY", caught.exception.code)

    def test_composition_optimal_is_refused_as_common_policy(self):
        with self.assertRaises(IntervalError) as caught:
            evaluate_common_policy_interval([list(FOUR_TENS)], policy=CONSUMPTION_PI)
        self.assertEqual("OPTIMAL_NOT_COMMON_POLICY", caught.exception.code)
