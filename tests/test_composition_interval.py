"""Interval research publishes min/max, never a mean shoe as the window."""
import unittest

from blackjack_lab.analysis.composition_interval import (
    IntervalError, evaluate_interval, refuse_mean_shoe, refuse_unknown_removal,
)
from blackjack_lab.analysis.research_windows import WINDOW_PRE_DEAL
from blackjack_lab.core.rules import CAPABILITY_MATRIX, EXPERIMENTAL


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
        report = evaluate_interval([rich, poor])
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

    def test_unknown_removal_stays_a_gap(self):
        refused = refuse_unknown_removal()
        self.assertEqual("UNKNOWN_REMOVAL_GAP", refused["reason_code"])
        self.assertTrue(refused["forbids_mean_shoe"])
        self.assertIsNone(refused["published_point_ev"])

    def test_capability_row_forbids_mean_shoe(self):
        status, note = CAPABILITY_MATRIX["未知组成区间研究"]
        self.assertEqual(EXPERIMENTAL, status)
        self.assertIn("平均牌靴", note)
