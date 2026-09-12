"""Bounded DAS optimization diffs vs the independent Fraction oracle.

Does not rewrite C#. Product budget stays 5s; the reference path may take longer.
Compares legal actions, EV, both-hand margins, and every joint/total cell.
"""
import os
import unittest
from math import fsum

from blackjack_lab.analysis.native_backend import build_native
from blackjack_lab.analysis.split_actions import solve_split_counts
from tests.das_split_reference import das_split_reference


def counts_of(cards):
    return tuple(cards.count(i) for i in range(1, 11))


@unittest.skipUnless(os.name == "nt", "Requires the actual Windows/.NET backend")
class TestDasOptimizationDiff(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def compare(self, cards, hands, up, allow_das=True, **kwargs):
        peek = kwargs.get("peek", True)
        active = kwargs.get("active", 0)
        aces = kwargs.get("split_aces", False)
        stakes = kwargs.get("stakes", (1, 1))
        awaiting = kwargs.get("awaiting", None)
        expected = das_split_reference(
            cards, hands, up, peek=peek, active=active, split_aces=aces,
            stakes=stakes, awaiting=awaiting, allow_das=allow_das)
        result = solve_split_counts(
            counts_of(cards), hands, up, peek, active=active, split_aces=aces,
            allow_das=allow_das, stakes=stakes,
            force_active=awaiting in ("double", "deal") or (active < 2 and len(hands[active]) == 1),
            force_close=awaiting == "double",
            budget_seconds=5.0)
        self.assertEqual(set(result["actions"]), set(expected))
        self.assertGreater(result["nodes"], 0)
        self.assertEqual(len(result["caches"]), 6)
        self.assertGreater(sum(result["caches"]), 0)
        for action, item in expected.items():
            actual = result["actions"][action]
            self.assertAlmostEqual(actual["ev"], float(item["ev"]), delta=1e-10, msg=action)
            self.assertAlmostEqual(sum(actual["net_distribution"].values()), 1.0, delta=1e-10, msg=action)
            self.assertAlmostEqual(sum(actual["joint_distribution"].values()), 1.0, delta=1e-10, msg=action)
            self.assertAlmostEqual(actual["ev"], fsum(float(v) * p for v, p in actual["net_distribution"].items()),
                                   delta=1e-10, msg=action)
            self.assertAlmostEqual(actual["ev"], sum(actual["hand_evs"]), delta=1e-10, msg=action)
            self.assertAlmostEqual(actual["hand_evs"][0], float(item["hand_evs"][0]), delta=1e-10, msg=action)
            self.assertAlmostEqual(actual["hand_evs"][1], float(item["hand_evs"][1]), delta=1e-10, msg=action)
            for (a, b), p in item["joint_distribution"].items():
                self.assertAlmostEqual(actual["joint_distribution"].get(f"{a},{b}", 0.0), float(p),
                                       delta=1e-10, msg=f"{action} joint {a},{b}")
            for pair, p in actual["joint_distribution"].items():
                a, b = map(int, pair.split(","))
                self.assertAlmostEqual(p, float(item["joint_distribution"].get((a, b), 0)),
                                       delta=1e-10, msg=f"{action} extra joint {pair}")
            for value, p in item["net_distribution"].items():
                self.assertAlmostEqual(actual["net_distribution"].get(str(value), 0.0), float(p),
                                       delta=1e-10, msg=f"{action} net {value}")
            for key, p in actual["net_distribution"].items():
                self.assertAlmostEqual(p, float(item["net_distribution"].get(int(key), 0)),
                                       delta=1e-10, msg=f"{action} extra net {key}")
        return result

    def test_remaining_63_is_below_information_bound_threshold(self):
        cards = (10,) * 61 + (1, 8)
        result = self.compare(cards, ((8, 10, 1), (8,)), 10)
        self.assertEqual(result["information_bound_prunes"], 0)

    def test_remaining_64_and_65_match_fraction_cells(self):
        for size in (64, 65):
            with self.subTest(remaining=size):
                cards = (10,) * (size - 2) + (1, 8)
                self.compare(cards, ((8, 10, 1), (8,)), 10)

    def test_known_66_card_bound_still_fires_and_matches(self):
        result = self.compare((10,) * 64 + (1, 8), ((8, 10, 1), (8,)), 10)
        self.assertGreater(result["information_bound_prunes"], 0)

    def test_peek_ace_ten_and_no_peek_six(self):
        cards = (8, 8, 9, 9, 10, 10)
        self.compare(cards, ((8,), (8,)), 1, peek=True)
        self.compare(cards, ((8,), (8,)), 10, peek=True)
        self.compare(cards, ((8,), (8,)), 6, peek=False)

    def test_soft_multi_ace_first_hand(self):
        self.compare((1, 1, 1, 8, 9, 10, 10), ((1, 1, 8), (8,)), 10)

    def test_offset_composition_is_not_a_ten_pile(self):
        self.compare((9,) * 8 + (10, 10), ((8, 8), (8,)), 6)

    def test_stakes_and_first_bust_and_second_das_gate(self):
        cards = (8, 8, 9, 9, 10, 10)
        self.compare(cards, ((8, 8), (8,)), 6, stakes=(1, 1))
        self.compare(cards, ((8, 8), (8,)), 6, stakes=(2, 1), awaiting="double")
        busted = self.compare(cards, ((8, 8, 10), (8,)), 6, active=1, stakes=(1, 1))
        self.assertAlmostEqual(busted["actions"]["deal"]["hand_evs"][0], -1.0, delta=1e-10)
        doubled_bust = self.compare(cards, ((8, 8, 10), (8,)), 6, active=1, stakes=(2, 1))
        self.assertAlmostEqual(doubled_bust["actions"]["deal"]["hand_evs"][0], -2.0, delta=1e-10)
        self.compare(cards, ((8, 8, 10), (8, 9)), 6, active=1, allow_das=True)
        self.compare(cards, ((8, 8, 10), (8, 9)), 6, active=1, allow_das=False)
        self.compare(cards, ((8, 8, 10), (8, 3)), 6, active=1, stakes=(1, 2))

    def test_three_pending_hit_classes(self):
        self.compare((8,) * 8, ((2,), (2,)), 6, peek=False, awaiting="deal")
        self.compare((8,) * 8, ((2, 3), (2,)), 6, peek=False, awaiting="deal")
        self.compare((8,) * 6, ((2, 10, 10), (2, 3)), 6, peek=False, active=1, awaiting="deal")
        self.compare((8,) * 6, ((2, 10, 10), (2, 3)), 6, peek=False, active=1,
                     stakes=(1, 2), awaiting="double")


if __name__ == "__main__":
    unittest.main()
