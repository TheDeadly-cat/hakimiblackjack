"""T6 production DAS solver vs independent Fraction reference. Old b1 tests stay separate."""
import unittest
from math import fsum

from blackjack_lab.analysis.native_backend import build_native, solve_presplit_native
from blackjack_lab.analysis.split_actions import solve_split_counts
from blackjack_lab.analysis.split_contracts import (
    DAS_PROFILE, DAS_ENGINE, supported_das_rules, supported_split_rules, das_research_rules,
    split_research_rules)
from tests.das_contract import DAS_PROFILE as REF_DAS_PROFILE
from tests.das_split_reference import das_split_reference
from tests.test_das_split_reference import B1_CASES


def counts_of(cards):
    return tuple(cards.count(i) for i in range(1, 11))


class TestDasProductionIdentity(unittest.TestCase):
    def test_new_profile_is_not_b1_and_not_silent_four_hand_das(self):
        self.assertEqual(DAS_PROFILE, REF_DAS_PROFILE)
        self.assertTrue(supported_das_rules(das_research_rules()))
        self.assertFalse(supported_split_rules(das_research_rules()))
        self.assertFalse(supported_das_rules(split_research_rules()))
        four = split_research_rules()
        four.profile_id = DAS_PROFILE
        four.max_split_hands = 4
        four.double_after_split = True
        self.assertFalse(supported_das_rules(four))
        self.assertNotEqual(DAS_ENGINE, "v0.2b1-finite-two-hand-1")


class TestDasEngineMatchesReference(unittest.TestCase):
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
            force_close=awaiting == "double")
        self.assertEqual(set(result["actions"]), set(expected))
        if allow_das:
            self.assertEqual(result["information_bound_prunes"], 0)
        for action, item in expected.items():
            actual = result["actions"][action]
            self.assertAlmostEqual(actual["ev"], float(item["ev"]), delta=1e-10, msg=action)
            self.assertAlmostEqual(sum(actual["net_distribution"].values()), 1.0, delta=1e-10)
            self.assertAlmostEqual(actual["ev"], fsum(float(v) * p for v, p in actual["net_distribution"].items()),
                                   delta=1e-10)
            self.assertAlmostEqual(actual["ev"], sum(actual["hand_evs"]), delta=1e-10)
            for (a, b), p in item["joint_distribution"].items():
                self.assertAlmostEqual(actual["joint_distribution"].get(f"{a},{b}", 0.0), float(p), delta=1e-10)
            for pair, p in actual["joint_distribution"].items():
                a, b = map(int, pair.split(","))
                expected_p = float(item["joint_distribution"].get((a, b), 0))
                self.assertAlmostEqual(p, expected_p, delta=1e-10, msg=pair)
        return result

    def test_das_off_matches_reference_on_b1_shoes(self):
        for cards, hands, up, kwargs in B1_CASES:
            with self.subTest(hands=hands, up=up, kwargs=kwargs):
                self.compare(cards, hands, up, allow_das=False, **kwargs)

    def test_das_on_matches_reference_legal_and_payoff_cases(self):
        self.compare((8, 8, 9, 9, 10, 10), ((8,), (8,)), 6)
        self.compare((10,) * 6, ((1,), (1,)), 6, split_aces=True)
        self.compare((8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6, active=0)
        self.compare((8, 8, 9, 9, 10, 10), ((8, 8, 2), (8,)), 6, active=0)
        self.compare((8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6, active=0, stakes=(2, 1), awaiting="double")
        self.compare((8, 8, 9, 9, 10, 10), ((8, 8, 10), (8,)), 6, active=1, stakes=(2, 1))
        self.compare((5, 5, 10, 10, 9, 9), ((10, 10), (6,)), 6)
        self.compare((10,) * 6, ((10, 1), (10,)), 6, active=0)
        self.compare((8,) * 6, ((2, 10, 10), (2, 3)), 6, peek=False, active=1, awaiting="deal")
        self.compare((8,) * 8, ((2, 3), (2,)), 6, peek=False, awaiting="deal")

    def test_das_does_not_use_b1_information_bound(self):
        result = solve_split_counts(
            counts_of((8, 8, 9, 9, 10, 10)), ((8, 8), (8,)), 6, True, active=0,
            allow_das=True, stakes=(1, 1))
        self.assertEqual(result["information_bound_prunes"], 0)
        self.assertIn("double", result["actions"])

    def test_presplit_split_under_das_matches_forced_first_deal(self):
        cards = (8, 8, 9, 9, 10, 10)
        expected = das_split_reference(cards, ((8,), (8,)), 6)["deal"]
        result = solve_presplit_native(
            counts_of(cards), (8, 8), 6, True, ("stand", "hit", "double", "split"),
            allow_das=True)
        actual = result["actions"]["split"]
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-10)
        for (a, b), p in expected["joint_distribution"].items():
            self.assertAlmostEqual(actual["joint_distribution"].get(f"{a},{b}", 0.0), float(p), delta=1e-10)


if __name__ == "__main__":
    unittest.main()
