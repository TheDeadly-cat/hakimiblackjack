"""Shoe-window study: unsupported large shoes, exact late shoes, no future leak."""
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import UNSUPPORTED, AVAILABLE
from blackjack_lab.analysis.predeal import solve_predeal_counts
from blackjack_lab.analysis.predeal_contracts import PREDEAL_MAX_REMAINING
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL, counts_from_values
from blackjack_lab.analysis.shoe_windows import (
    KIND_FULL_DEPLETE, KIND_FULL_RESHUFFLE, KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE,
    choose_action, evaluate_predeal, physical_mean, play_round, run_independent_shoes,
    run_window_study,
)


class ShoeWindowStudyTest(unittest.TestCase):
    def test_full_six_deck_reshuffle_never_publishes_opening_ev(self):
        report = run_window_study(kind=KIND_FULL_RESHUFFLE, n_decks=6, seed=3, max_rounds=5)
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertEqual(WINDOW_PRE_DEAL, report["window"])
        self.assertEqual(5, report["summary"]["round_count"])
        self.assertEqual(5, report["summary"]["predeal_unsupported"])
        self.assertEqual(0, report["summary"]["predeal_available"])
        self.assertTrue(report["summary"]["zero_window"])
        for item in report["rounds"]:
            self.assertEqual(UNSUPPORTED, item["predeal"]["status"])
            self.assertEqual("PREDEAL_SHOE_TOO_LARGE", item["predeal"]["reason_code"])
            self.assertIsNone(item["predeal"]["ev"])
            self.assertGreater(item["predeal"]["physical_remaining"], PREDEAL_MAX_REMAINING)

    def test_late_reshuffle_keeps_the_same_predeal_ev(self):
        pack = (10, 10, 9, 9, 8, 7)
        report = run_window_study(kind=KIND_LATE_RESHUFFLE, pack=pack, seed=4, max_rounds=4)
        evs = [item["predeal"]["ev"] for item in report["rounds"]]
        self.assertEqual(4, len(evs))
        self.assertTrue(all(status == AVAILABLE for status in
                            (item["predeal"]["status"] for item in report["rounds"])))
        self.assertTrue(all(abs(ev - evs[0]) < 1e-12 for ev in evs))

    def test_physical_plays_match_exact_predeal_in_expectation(self):
        cards = (10, 10, 9, 9, 8, 7)
        exact = solve_predeal_counts(counts_from_values(cards))
        mean, _pays = physical_mean(cards, 800, seed=11)
        self.assertAlmostEqual(mean, exact["ev"], delta=0.09)

    def test_strategy_is_not_handed_the_hole_rank(self):
        pack = [10, 9, 6, 8, 7, 5]
        calls = []
        real = choose_action

        def wrapped(counts, player, up, peek, **kwargs):
            calls.append((tuple(player), up, peek, counts))
            return real(counts, player, up, peek, **kwargs)

        with patch("blackjack_lab.analysis.shoe_windows.choose_action", wrapped):
            play_round(pack)
        self.assertTrue(calls)
        hole = pack[3]
        for player, up, peek, counts in calls:
            self.assertEqual(up, pack[1])
            self.assertEqual(player[:2], (pack[0], pack[2]))
            self.assertEqual(peek, up in (1, 10) and sorted((up, hole)) != [1, 10])
            extra = len(player) - 2
            self.assertEqual(sum(counts), len(pack) - 3 - extra)

    def test_toy_deplete_records_unsupported_then_exact(self):
        pack = [10, 10, 10, 9, 9, 8, 8, 7, 6, 5, 4, 3, 2, 2, 2, 2, 3, 3, 4, 4]
        self.assertGreater(len(pack), PREDEAL_MAX_REMAINING)
        report = run_window_study(kind=KIND_FULL_DEPLETE, pack=pack, n_decks=6, seed=2,
                                  budget_seconds=5.0, play_budget_seconds=2.0)
        statuses = [item["predeal"]["status"] for item in report["rounds"]]
        self.assertEqual(UNSUPPORTED, statuses[0])
        self.assertTrue(any(status == AVAILABLE for status in statuses) or
                        any(item["predeal"].get("reason_code") == "TIMEOUT"
                            for item in report["rounds"]))
        remainings = [item["predeal"]["physical_remaining"] for item in report["rounds"]]
        self.assertEqual(remainings, sorted(remainings, reverse=True))

    def test_late_deplete_can_report_a_zero_window_without_forcing_advantage(self):
        pack = (9, 8, 7, 6, 5, 4)
        report = run_window_study(kind=KIND_LATE_DEPLETE, pack=pack, seed=1, margin=0.5)
        self.assertIn(report["summary"]["zero_window"], (True, False))
        self.assertTrue(report["not_a_reliable_window_claim"])
        self.assertIn("positive_streak_max", report["summary"])
        if report["summary"]["predeal_available"]:
            self.assertLessEqual(report["summary"]["exceeds_margin"],
                                 report["summary"]["predeal_available"])

    def test_evaluate_predeal_does_not_use_current_hand_schema(self):
        record = evaluate_predeal([10, 9, 8, 7, 6, 5])
        self.assertEqual(WINDOW_PRE_DEAL, record["window"])
        self.assertEqual(AVAILABLE, record["status"])
        self.assertNotIn("player_ranks", record)
        self.assertNotIn("dealer_up", record)

    def test_independent_shoes_are_the_sample_unit(self):
        pack = (10, 10, 9, 9, 8, 7, 6, 5)
        report = run_independent_shoes(kind=KIND_LATE_DEPLETE, pack=pack, n_shoes=3,
                                       base_seed=2, max_rounds=3)
        self.assertEqual("shoe", report["sample_unit"])
        self.assertTrue(report["not_independent_round_samples"])
        self.assertFalse(report["independent_video"])
        self.assertEqual(3, report["n_shoes"])
        self.assertEqual(3, len(report["shoes"]))
        self.assertIn("zero_window_rate", report["summary"])
        self.assertIn("mean_signal_coverage", report["summary"])
        self.assertGreaterEqual(report["summary"]["mean_signal_coverage"], 0.0)
        self.assertLessEqual(report["summary"]["mean_signal_coverage"], 1.0)
        for shoe in report["shoes"]:
            self.assertEqual("round-within-shoe", shoe["summary"]["sample_unit"])
            self.assertIn("signal_coverage", shoe["summary"])
            self.assertIn("negative_ev", shoe["summary"])
            self.assertTrue(shoe["summary"]["realized_path_is_not_counterfactual_truth"])
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])
        self.assertIn("exceeds_margin_shoe_rate", report["summary"])
        self.assertIn("realized_distribution_pooled_not_independent", report["summary"])

    def test_policy_contrast_does_not_share_a_realized_path(self):
        from blackjack_lab.analysis.shoe_windows import (
            CONSUMPTION_BASIC, CONSUMPTION_STAND, run_policy_contrast,
        )
        pack = [5, 10, 6, 9, 10, 10, 10, 2, 3, 4]
        report = run_policy_contrast(pack=pack, seed=4, max_rounds=2,
                                     policies=(CONSUMPTION_STAND, CONSUMPTION_BASIC))
        self.assertFalse(report["shared_realized_path"])
        self.assertTrue(report["realized_path_is_not_counterfactual_truth"])
        self.assertFalse(report["independent_video"])
        self.assertEqual(2, len(report["arms"]))
        self.assertEqual(
            {CONSUMPTION_STAND, CONSUMPTION_BASIC},
            {arm["policy"] for arm in report["arms"]})

    def test_play_error_still_keeps_the_predeal_snapshot(self):
        from blackjack_lab.analysis.probability import InsufficientCards
        pack = (10, 9, 8, 7, 6, 5)
        with patch("blackjack_lab.analysis.shoe_windows.play_round",
                   side_effect=InsufficientCards("合成耗牌失败")):
            report = run_window_study(kind=KIND_LATE_DEPLETE, pack=pack, seed=1, max_rounds=3)
        self.assertEqual(1, report["summary"]["round_count"])
        first = report["rounds"][0]
        self.assertEqual(WINDOW_PRE_DEAL, first["predeal"]["window"])
        self.assertIn(first["predeal"]["status"], (AVAILABLE, UNSUPPORTED, "timeout", "failed"))
        self.assertIsNone(first["realized_net"])
        self.assertIn("合成耗牌失败", first["play_error"] or "")
        self.assertIsNotNone(first["predeal"].get("status"))
