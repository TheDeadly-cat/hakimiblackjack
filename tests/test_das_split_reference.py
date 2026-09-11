"""T5 DAS independent-reference tests. Not a production solver and not native C#."""
import ast
import unittest
from pathlib import Path
from fractions import Fraction

from tests.das_contract import DAS_PROFILE, MAX_POST_SPLIT_INVESTMENT, can_das, nets_for_stake
from tests.das_split_reference import das_split_reference, pair_payoff, points
from tests.split_reference import split_reference

REPO = Path(__file__).resolve().parents[1]
B1_CASES = [
    ((8, 8, 9, 9, 10, 10), ((8,), (8,)), 6, {}),
    ((8, 8, 9, 9, 10, 10), ((1,), (1,)), 10, {"split_aces": True}),
    ((8, 8, 9, 9, 10, 10), ((10,), (10,)), 1, {}),
    ((8, 8, 9, 9, 10, 10), ((8, 10, 10), (8,)), 6, {"active": 1}),
    ((8, 8, 9, 9, 10, 10), ((8, 8), (8, 9)), 10, {"active": 1}),
    ((8, 8, 9, 9, 10, 10), ((8, 8), (8, 9)), 6, {"active": 2}),
    ((1, 8, 8, 9, 10, 10), ((8, 9), (8,)), 10, {}),
    ((1, 8, 8, 9, 10, 10), ((8,), (8,)), 1, {}),
    ((1, 8, 8, 9, 10, 10, 10, 10, 10), ((2,), (2,)), 2, {}),
    ((1, 1, 8, 9, 10, 10, 10), ((2, 1), (2,)), 10, {}),
    ((1, 1, 8, 9, 10, 10, 10), ((8,), (8,)), 6, {"peek": False}),
]


def mass_pairs(joint):
    return {pair: probability for pair, probability in joint.items() if probability}


class TestDasSettlementIdentities(unittest.TestCase):
    def test_required_combined_nets(self):
        bust_then_win = pair_payoff((22, 20), (2, 1), (6, 10, 10), False)
        self.assertEqual(bust_then_win, (-2, 1))
        self.assertEqual(sum(bust_then_win), -1)
        both_win = pair_payoff((21, 21), (2, 2), (6, 10, 10), False)
        self.assertEqual(both_win, (2, 2))
        self.assertEqual(sum(both_win), 4)
        both_lose = pair_payoff((22, 22), (2, 2), (10, 10), False)
        self.assertEqual(both_lose, (-2, -2))
        self.assertEqual(sum(both_lose), -4)
        double_win_plain_lose = pair_payoff((21, 16), (2, 1), (10, 10), False)
        self.assertEqual(double_win_plain_lose, (2, -1))
        self.assertEqual(sum(double_win_plain_lose), 1)

    def test_split_twenty_one_is_not_natural_against_dealer_bj(self):
        pair = pair_payoff((21, 21), (1, 1), (1, 10), True)
        self.assertEqual(pair, (-1, -1))

    def test_doubled_first_bust_is_counted_once(self):
        result = das_split_reference(
            (8, 8, 9, 9, 10, 10), ((8, 8, 10), (8,)), 6, active=1, stakes=(2, 1))
        self.assertEqual(result["deal"]["hand_evs"][0], -2)
        self.assertEqual(result["deal"]["current_investment"], 3)
        for (first, _second), probability in mass_pairs(result["deal"]["joint_distribution"]).items():
            self.assertEqual(first, -2)
            self.assertGreater(probability, 0)
        self.assertEqual(sum(result["deal"]["hand_evs"]), result["deal"]["ev"])


class TestDasLegalActions(unittest.TestCase):
    def test_split_aces_cannot_das(self):
        result = das_split_reference((10,) * 6, ((1,), (1,)), 6, split_aces=True)
        self.assertEqual(set(result), {"deal"})
        self.assertFalse(can_das(True, (1, 10)))

    def test_natural_looking_split_twenty_one_cannot_das(self):
        result = das_split_reference((10,) * 6, ((10, 1), (10,)), 6, active=0)
        self.assertEqual(set(result), {"stand"})
        self.assertFalse(can_das(False, (10, 1)))

    def test_three_card_hand_cannot_double(self):
        result = das_split_reference((8, 8, 9, 9, 10, 10), ((8, 8, 2), (8,)), 6, active=0)
        self.assertNotIn("double", result)
        self.assertIn("stand", result)
        self.assertIn("hit", result)
        self.assertEqual(points((8, 8, 2)), 18)

    def test_awaiting_double_card_only_deals(self):
        result = das_split_reference(
            (8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6,
            active=0, stakes=(2, 1), awaiting="double")
        self.assertEqual(set(result), {"deal"})
        self.assertEqual(result["deal"]["stakes"], (2, 1))
        self.assertEqual(result["deal"]["additional_investment"], 0)
        self.assertEqual(result["deal"]["current_investment"], 3)

    def test_future_second_hand_cards_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "未来牌"):
            das_split_reference((8, 8, 9, 9, 10, 10), ((8, 8), (8, 9)), 6, active=0)

    def test_two_card_non_ace_offers_das_and_records_immediate_unit(self):
        result = das_split_reference((8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6, active=0)
        self.assertIn("double", result)
        self.assertEqual(result["double"]["additional_investment"], 1)
        self.assertEqual(result["double"]["current_investment"], 2)
        self.assertEqual(result["stand"]["additional_investment"], 0)
        self.assertEqual(result["stand"]["possible_future_additional"], 1)
        self.assertLessEqual(result["double"]["max_final_investment"], MAX_POST_SPLIT_INVESTMENT)
        self.assertEqual(result["double"]["max_final_investment"], 4)

    def test_forced_first_card_keeps_both_das_options_in_the_bound(self):
        result = das_split_reference((8, 8, 9, 9, 10, 10), ((8,), (8,)), 6)
        self.assertEqual(set(result), {"deal"})
        self.assertEqual(result["deal"]["current_investment"], 2)
        self.assertEqual(result["deal"]["additional_investment"], 0)
        self.assertEqual(result["deal"]["possible_future_additional"], 2)
        self.assertEqual(result["deal"]["max_final_investment"], 4)


class TestDasPayoffSupport(unittest.TestCase):
    def test_double_action_uses_doubled_first_hand_support(self):
        result = das_split_reference((8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6, active=0)
        for (first, second), probability in mass_pairs(result["double"]["joint_distribution"]).items():
            self.assertIn(first, nets_for_stake(2))
            self.assertIn(second, nets_for_stake(1) | nets_for_stake(2))
            self.assertGreater(probability, 0)
        self.assertEqual(result["double"]["joint_distribution"].get((-2, 1)), Fraction(1))
        self.assertEqual(result["double"]["ev"], -1)

    def test_das_on_stand_can_place_mass_outside_b1_nine_cells(self):
        result = das_split_reference((5, 5, 10, 10, 9, 9), ((10, 10), (6,)), 6)
        closed = das_split_reference((5, 5, 10, 10, 9, 9), ((10, 10), (6,)), 6, allow_das=False)
        self.assertNotEqual(result["stand"]["ev"], closed["stand"]["ev"])
        outside = [pair for pair in mass_pairs(result["stand"]["joint_distribution"])
                   if pair[0] not in (-1, 0, 1) or pair[1] not in (-1, 0, 1)]
        self.assertTrue(outside)
        for first, second in outside:
            self.assertIn(first, nets_for_stake(1))
            self.assertIn(second, nets_for_stake(2))


class TestDasOffMatchesB1Reference(unittest.TestCase):
    def assert_same_as_b1(self, cards, hands, up, **kwargs):
        closed = das_split_reference(cards, hands, up, allow_das=False, **kwargs)
        old = split_reference(cards, hands, up, **kwargs)
        self.assertEqual(set(closed), set(old))
        for action in old:
            self.assertEqual(closed[action]["ev"], old[action]["ev"])
            self.assertEqual(closed[action]["additional_investment"], 0)
            self.assertEqual(closed[action]["possible_future_additional"], 0)
            for pair, probability in old[action]["joint_distribution"].items():
                self.assertEqual(closed[action]["joint_distribution"].get(pair, Fraction(0)), probability)
            for pair, probability in mass_pairs(closed[action]["joint_distribution"]).items():
                self.assertIn(pair[0], (-1, 0, 1))
                self.assertIn(pair[1], (-1, 0, 1))
                self.assertEqual(old[action]["joint_distribution"].get(pair, Fraction(0)), probability)

    def test_existing_small_shoes_without_das(self):
        for cards, hands, up, kwargs in B1_CASES:
            with self.subTest(hands=hands, up=up, kwargs=kwargs):
                self.assert_same_as_b1(cards, hands, up, **kwargs)

    def test_das_off_never_lists_double(self):
        result = das_split_reference((8, 8, 9, 9, 10, 10), ((8, 8), (8,)), 6, allow_das=False)
        self.assertNotIn("double", result)


class TestDasContractIdentity(unittest.TestCase):
    def test_new_profile_is_not_the_b1_no_das_name(self):
        self.assertEqual(DAS_PROFILE, "research-s17-us-peek-two-sequential-das-v1")
        self.assertNotEqual(DAS_PROFILE, "research-s17-us-peek-two-sequential-v1")

    def test_reference_does_not_import_production_code(self):
        for name in ("das_contract.py", "das_split_reference.py"):
            tree = ast.parse((REPO / "tests" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    modules = [node.module]
                for module in modules:
                    self.assertFalse(module.startswith("blackjack_lab"), module)
                    self.assertNotIn(module, {"probability", "split_actions", "table"})


if __name__ == "__main__":
    unittest.main()
