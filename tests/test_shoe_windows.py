"""Shoe-window study: unsupported large shoes, exact late shoes, no future leak."""
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import UNSUPPORTED, AVAILABLE
from blackjack_lab.analysis.predeal import solve_predeal_counts
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL, counts_from_values
from blackjack_lab.analysis.shoe_windows import (
    KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE, KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE,
    choose_action, evaluate_predeal, physical_mean, play_round, run_independent_shoes,
    run_policy_contrast, run_window_study,
)


class ShoeWindowStudyTest(unittest.TestCase):
    def test_full_six_deck_reshuffle_never_publishes_opening_ev(self):
        report = run_window_study(kind=KIND_FULL_RESHUFFLE, n_decks=6, seed=3, max_rounds=5,
                                  surrender=None)
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertEqual(5, report["summary"]["round_count"])
        self.assertEqual(5, report["summary"]["predeal_unsupported"])
        self.assertEqual(0, report["summary"]["predeal_available"])
        self.assertFalse(report["summary"]["zero_window"])
        self.assertTrue(report["summary"]["incomplete_cannot_claim_zero_window"])
        self.assertTrue(report["summary"]["no_positive_signal_detected"])
        self.assertFalse(report["summary"]["no_positive_window_in_complete_evaluation"])
        self.assertFalse(report["summary"]["complete_evaluation"])
        for item in report["rounds"]:
            self.assertEqual(UNSUPPORTED, item["predeal"]["status"])
            self.assertEqual("PREDEAL_SHOE_TOO_LARGE", item["predeal"]["reason_code"])
            self.assertEqual("unavailable", item["predeal"]["window_state"])
            self.assertIsNone(item["predeal"]["ev"])
            self.assertGreater(item["predeal"]["physical_remaining"], PREDEAL_MAX_REMAINING)
            self.assertTrue(item["reshuffled"])
            self.assertFalse(item["history_removed"])
            self.assertIsNotNone(item["realized_net"])
        self.assertEqual(5, report["summary"]["realized_count"])
        self.assertIsNotNone(report["summary"]["realized_mean"])
        self.assertTrue(report["current_hand_not_used_as_opening"])
        self.assertEqual("independent-reset", report["stop_reason"])

    def test_declared_cut_stops_before_the_tail(self):
        pack = list(range(1, 11)) * 3  # 30 cards
        report = run_window_study(kind=KIND_FULL_DEPLETE, pack=pack, n_decks=6, seed=2,
                                  cut_remaining=12, max_rounds=40, play_budget_seconds=2.0,
                                  surrender=None)
        self.assertEqual(12, report["cut_remaining"])
        self.assertTrue(report["cut_declared"])
        self.assertEqual("cut", report["stop_reason"])
        self.assertGreater(report["summary"]["round_count"], 0)
        for item in report["rounds"]:
            self.assertGreater(item["predeal"]["physical_remaining"], 12)

    def test_late_reshuffle_keeps_the_same_predeal_ev(self):
        pack = (10, 10, 9, 9, 8, 7)
        report = run_window_study(kind=KIND_LATE_RESHUFFLE, pack=pack, seed=4, max_rounds=4,
                                  surrender=None)
        self.assertIsNone(report["surrender"])
        evs = [item["predeal"]["ev"] for item in report["rounds"]]
        self.assertEqual(4, len(evs))
        self.assertTrue(all(status == AVAILABLE for status in
                            (item["predeal"]["status"] for item in report["rounds"])))
        self.assertTrue(all(abs(ev - evs[0]) < 1e-12 for ev in evs))

    def test_physical_plays_match_exact_predeal_in_expectation(self):
        cards = (10, 10, 9, 9, 8, 7)
        exact_late = solve_predeal_counts(counts_from_values(cards), surrender="late")
        mean_late, _pays = physical_mean(cards, 800, seed=11, surrender="late")
        self.assertAlmostEqual(mean_late, exact_late["ev"], delta=0.09)
        exact_none = solve_predeal_counts(counts_from_values(cards), surrender=None)
        mean_none, _ = physical_mean(cards, 400, seed=11, surrender=None)
        self.assertAlmostEqual(exact_none["ev"], 0.0, delta=1e-12)
        self.assertAlmostEqual(mean_none, 0.0, delta=0.08)
        self.assertGreater(abs(exact_late["ev"] - exact_none["ev"]), 0.1)

    def test_strategy_is_not_handed_the_hole_rank(self):
        pack = [10, 9, 6, 8, 7, 5]
        calls = []
        real = choose_action

        def wrapped(counts, player, up, peek, **kwargs):
            calls.append((tuple(player), up, peek, counts))
            return real(counts, player, up, peek, **kwargs)

        with patch("blackjack_lab.analysis.shoe_windows.choose_action", wrapped):
            play_round(pack, surrender=None)
        self.assertTrue(calls)
        hole = pack[3]
        for player, up, peek, counts in calls:
            self.assertEqual(up, pack[1])
            self.assertEqual(player[:2], (pack[0], pack[2]))
            self.assertEqual(peek, up in (1, 10) and sorted((up, hole)) != [1, 10])
            extra = len(player) - 2
            self.assertEqual(sum(counts), len(pack) - 3 - extra)

    def test_same_public_information_keeps_the_same_first_action(self):
        first = [5, 10, 6, 9, 8, 7, 4]
        second = [5, 10, 6, 8, 9, 7, 4]
        left, right = [], []
        play_round(first, decisions=left, surrender=None)
        play_round(second, decisions=right, surrender=None)
        self.assertTrue(left and right)
        self.assertEqual(left[0]["player"], right[0]["player"])
        self.assertEqual(left[0]["up"], right[0]["up"])
        self.assertEqual(left[0]["peek"], right[0]["peek"])
        self.assertEqual(left[0]["counts"], right[0]["counts"])
        self.assertEqual(left[0]["action"], right[0]["action"])
        self.assertTrue(left[0]["peek"])
        self.assertNotEqual(first[3], second[3])

    def test_toy_deplete_records_unsupported_then_exact(self):
        pack = [10, 10, 10, 9, 9, 8, 8, 7, 6, 5, 4, 3, 2, 2, 2, 2, 3, 3, 4, 4]
        self.assertGreater(len(pack), PREDEAL_MAX_REMAINING)
        report = run_window_study(kind=KIND_FULL_DEPLETE, pack=pack, n_decks=6, seed=2,
                                  budget_seconds=5.0, play_budget_seconds=2.0, surrender=None)
        statuses = [item["predeal"]["status"] for item in report["rounds"]]
        self.assertEqual(UNSUPPORTED, statuses[0])
        self.assertTrue(any(status == AVAILABLE for status in statuses) or
                        any(item["predeal"].get("reason_code") == "TIMEOUT"
                            for item in report["rounds"]))
        remainings = [item["predeal"]["physical_remaining"] for item in report["rounds"]]
        self.assertEqual(remainings, sorted(remainings, reverse=True))

    def test_late_deplete_can_report_a_zero_window_without_forcing_advantage(self):
        pack = (9, 8, 7, 6, 5, 4)
        report = run_window_study(kind=KIND_LATE_DEPLETE, pack=pack, seed=1, margin=0.5,
                                  surrender=None)
        self.assertIn(report["summary"]["zero_window"], (True, False))
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertIn("positive_streak_max", report["summary"])
        if report["summary"]["predeal_available"]:
            self.assertLessEqual(report["summary"]["exceeds_margin"],
                                 report["summary"]["predeal_available"])

    def test_evaluate_predeal_does_not_use_current_hand_schema(self):
        record = evaluate_predeal([10, 9, 8, 7, 6, 5], surrender=None)
        self.assertEqual(WINDOW_PRE_DEAL, record["window"])
        self.assertEqual(AVAILABLE, record["status"])
        self.assertEqual("synthetic-composition", record["source_mode"])
        self.assertIn("rules_digest", record)
        self.assertEqual(64, len(record["rules_digest"]))
        self.assertFalse(record["timely"])
        self.assertIsNone(record["decision_deadline"])
        self.assertTrue(record["not_a_reliable_window_claim"])
        self.assertGreater(record["ev"], 0)
        self.assertEqual("positive_supported", record["window_state"])
        self.assertNotIn("player_ranks", record)
        self.assertNotIn("dealer_up", record)
        six_none = evaluate_predeal([10, 10, 9, 9, 8, 7], surrender=None)
        self.assertEqual("nonpositive_supported", six_none["window_state"])
        self.assertAlmostEqual(0.0, six_none["ev"], places=12)
        late = evaluate_predeal([10, 10, 9, 9, 8, 7], surrender="late")
        self.assertEqual("positive_supported", late["window_state"])
        self.assertAlmostEqual(5 / 36, late["ev"], places=12)
        too_large = evaluate_predeal([10] * 20, surrender=None)
        self.assertEqual("unavailable", too_large["window_state"])
        self.assertEqual("PREDEAL_SHOE_TOO_LARGE", too_large["reason_code"])

    def test_independent_shoes_are_the_sample_unit(self):
        pack = (10, 10, 9, 9, 8, 7, 6, 5)
        report = run_independent_shoes(kind=KIND_LATE_DEPLETE, pack=pack, n_shoes=3,
                                       base_seed=2, max_rounds=3, surrender=None)
        self.assertIsNone(report["surrender"])
        self.assertEqual("shoe", report["sample_unit"])
        self.assertTrue(report["not_independent_round_samples"])
        self.assertFalse(report["independent_video"])
        self.assertEqual(3, report["n_shoes"])
        self.assertEqual(3, len(report["shoes"]))
        self.assertIn("zero_window_rate", report["summary"])
        self.assertFalse(report["summary"]["incomplete_cannot_claim_zero_window"])
        self.assertIsNotNone(report["summary"]["zero_window_rate"])
        self.assertGreater(report["summary"]["complete_shoes"], 0)
        self.assertIn("incomplete_shoe_rate", report["summary"])
        self.assertIn("no_positive_signal_rate", report["summary"])
        self.assertIn("mean_signal_coverage", report["summary"])
        self.assertGreaterEqual(report["summary"]["mean_signal_coverage"], 0.0)
        self.assertLessEqual(report["summary"]["mean_signal_coverage"], 1.0)
        for shoe in report["shoes"]:
            self.assertEqual("round-within-shoe", shoe["summary"]["sample_unit"])
            self.assertIn("signal_coverage", shoe["summary"])
            self.assertIn("negative_ev", shoe["summary"])
            self.assertTrue(shoe["summary"]["realized_path_is_not_counterfactual_truth"])
            self.assertTrue(shoe["checkpoints"])
            self.assertEqual(len(shoe["checkpoints"]), shoe["summary"]["round_count"])
            first = shoe["checkpoints"][0]
            self.assertIn("predeal_remaining", first)
            self.assertIn("remaining_after_round", first)
            self.assertEqual(WINDOW_PRE_DEAL, first["predeal"]["window"])
            self.assertFalse(first["predeal"]["timely"])
            self.assertIn(shoe["stop_reason"], ("max_rounds", "cut", "exhausted", "play_error"))
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])
        self.assertIn("exceeds_margin_shoe_rate", report["summary"])
        self.assertIn("realized_distribution_pooled_not_independent", report["summary"])

    def test_policy_contrast_does_not_share_a_realized_path(self):
        from blackjack_lab.analysis.shoe_windows import (
            CONSUMPTION_BASIC, CONSUMPTION_STAND, run_policy_contrast,
        )
        pack = [5, 10, 6, 9, 10, 10, 10, 2, 3, 4]
        report = run_policy_contrast(pack=pack, seed=4, max_rounds=2,
                                     policies=(CONSUMPTION_STAND, CONSUMPTION_BASIC),
                                     surrender=None)
        self.assertIsNone(report["surrender"])
        self.assertFalse(report["shared_realized_path"])
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])
        self.assertFalse(report["independent_video"])
        self.assertEqual(2, len(report["arms"]))
        self.assertEqual(
            {CONSUMPTION_STAND, CONSUMPTION_BASIC},
            {arm["policy"] for arm in report["arms"]})
        self.assertEqual(
            {CONSUMPTION_STAND, CONSUMPTION_BASIC},
            set(report["path_policy_id_by_arm"]))
        self.assertEqual(PREDEAL_STRATEGY_VERSION, report["evaluation_policy_id"])
        self.assertNotEqual(report["arms"][0]["path_policy_id"], report["evaluation_policy_id"])

    def test_play_error_still_keeps_the_predeal_snapshot(self):
        from blackjack_lab.analysis.probability import InsufficientCards
        pack = (10, 9, 8, 7, 6, 5)
        with patch("blackjack_lab.analysis.shoe_windows.play_round",
                   side_effect=InsufficientCards("合成耗牌失败")):
            report = run_window_study(kind=KIND_LATE_DEPLETE, pack=pack, seed=1, max_rounds=3,
                                      surrender=None)
        self.assertEqual(1, report["summary"]["round_count"])
        first = report["rounds"][0]
        self.assertEqual(WINDOW_PRE_DEAL, first["predeal"]["window"])
        self.assertIn(first["predeal"]["status"], (AVAILABLE, UNSUPPORTED, "timeout", "failed"))
        self.assertIsNone(first["realized_net"])
        self.assertIn("合成耗牌失败", first["play_error"] or "")
        self.assertIsNotNone(first["predeal"].get("status"))

    def test_window_apis_require_declared_surrender(self):
        pack = [10, 9, 8, 7, 6, 5]
        with self.assertRaises(ValueError) as caught:
            play_round(pack)
        self.assertIn("不能默认晚投降", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            evaluate_predeal(pack)
        self.assertIn("不能默认晚投降", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            run_window_study(kind=KIND_LATE_DEPLETE, pack=pack, seed=1, max_rounds=1)
        self.assertIn("不能默认晚投降", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            physical_mean(pack, 2, seed=1)
        self.assertIn("不能默认晚投降", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            run_independent_shoes(kind=KIND_LATE_DEPLETE, pack=pack, n_shoes=1, max_rounds=1)
        self.assertIn("不能默认晚投降", str(caught.exception))
        with self.assertRaises(ValueError) as caught:
            run_policy_contrast(pack=pack, seed=1, max_rounds=1)
        self.assertIn("不能默认晚投降", str(caught.exception))

    def test_choose_action_requires_declared_actions(self):
        with self.assertRaises(ValueError) as caught:
            choose_action(counts_from_values([10, 10, 10]), (10, 6), 9, False)
        self.assertIn("不能默认含投降", str(caught.exception))
