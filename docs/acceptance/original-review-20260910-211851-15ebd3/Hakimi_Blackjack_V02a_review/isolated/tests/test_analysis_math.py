import unittest
from fractions import Fraction
from itertools import product

from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.probability import FiniteModel, LABELS
from tests.analysis_reference import reference


class TestIndependentMath(unittest.TestCase):
    def counts(self, cards):
        return tuple(cards.count(i) for i in range(1, 11))

    def test_small_physical_worlds_match_all_outcomes(self):
        scenarios = [
            ((1, 7, 8, 9, 10, 10), (5, 6), 6, False),
            ((1, 7, 8, 9, 10, 10), (10, 6), 10, True),
            ((1, 2, 7, 8, 9, 10), (10, 3), 1, True),
            ((1, 7, 7, 8, 9, 10), (1, 6), 9, False),
            ((1, 1, 7, 8, 9, 10), (1, 1), 2, False),
        ]
        for cards, player, up, peek in scenarios:
            with self.subTest(cards=cards, player=player, up=up):
                expected = reference(cards, player, up, peek)
                actual = solve_counts(self.counts(cards), player, up, peek, actions=("stand", "hit", "double", "surrender"))
                for action, result in expected["actions"].items():
                    self.assertAlmostEqual(actual["actions"][action]["ev"], float(result["ev"]), delta=1e-10)
                    for payoff, probability in actual["actions"][action]["net_distribution"].items():
                        self.assertAlmostEqual(probability, float(result["net_distribution"].get(Fraction(payoff), 0)), delta=1e-10)
                for label, p in actual["dealer_distribution"].items():
                    self.assertAlmostEqual(p, float(expected["dealer_distribution"].get(label, 0)), delta=1e-10)
                for value, label in enumerate(LABELS, 1):
                    self.assertAlmostEqual(actual["next_draw"][label], float(expected["next_draw"].get(value, 0)), delta=1e-10)
                self.assertAlmostEqual(actual["hit_bust"], float(expected["hit_bust"]), delta=1e-10)

    def test_peek_changes_both_hole_and_next_card_distribution(self):
        counts = self.counts((1, 7, 8, 9, 10, 10))
        no_peek = FiniteModel(10, False).target_draw(counts)
        peek = FiniteModel(10, True).target_draw(counts)
        self.assertNotEqual(peek, no_peek)
        self.assertEqual(peek[0], 1 / 5)  # Ace cannot be the hole, so all Aces remain drawable.
        self.assertAlmostEqual(sum(peek), 1.0)

    def test_hidden_card_oracle_cannot_inflate_continuation_ev(self):
        cards = (1, 7, 8, 9, 10, 10)
        expected = reference(cards, (1, 6), 9)
        actual = solve_counts(self.counts(cards), (1, 6), 9, False)
        self.assertEqual(expected["actions"]["hit"]["ev"], Fraction(-11, 20))
        self.assertEqual(expected["illegal_hole_oracle_hit_ev"], Fraction(-21, 40))
        self.assertAlmostEqual(actual["actions"]["hit"]["ev"], -0.55, delta=1e-10)
        self.assertGreater(float(expected["illegal_hole_oracle_hit_ev"]), actual["actions"]["hit"]["ev"] + 0.02)

    def test_three_deck_counts_actually_change_probabilities_and_ev(self):
        results = []
        for n in (6, 7, 8):
            counts = [4 * n] * 9 + [16 * n]
            for card in (10, 6, 10):
                counts[card - 1] -= 1
            result = solve_counts(counts, (10, 6), 10, True)
            results.append(result)
            for action in result["actions"].values():
                self.assertAlmostEqual(sum(action["net_distribution"].values()), 1, delta=1e-10)
                self.assertEqual(action["ev"], sum(float(k) * p for k, p in action["net_distribution"].items()))
        self.assertEqual(len({r["actions"]["hit"]["ev"] for r in results}), 3)
        self.assertEqual(len({r["next_draw"]["A"] for r in results}), 3)

    def test_hit_includes_repeated_decisions_not_forced_stand(self):
        counts = [24] * 9 + [96]
        for card in (3, 5, 10):
            counts[card - 1] -= 1
        result = solve_counts(counts, (3, 5), 10, True)
        self.assertGreater(result["actions"]["hit"]["ev"], result["actions"]["double"]["ev"] / 2 + 0.05)


if __name__ == "__main__":
    unittest.main()
