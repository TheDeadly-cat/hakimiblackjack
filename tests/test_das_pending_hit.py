"""Native regressions: ordinary hit-pending must not re-enable DAS.

Hit-pending is a 2-card force_active deal. The initial split second card is a
1-card deal and must still allow later DAS. DAS unique card remains stake 2
and force_close.
"""
import os
import unittest


@unittest.skipUnless(os.name == "nt", "Requires the actual Windows/.NET backend")
class TestDasPendingHit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from blackjack_lab.analysis.native_backend import build_native
        build_native()

    def solve(self, cards, hands, *, active=0, stakes=(1, 1), force=False, close=False):
        from blackjack_lab.analysis.split_actions import solve_split_counts
        counts = tuple(cards.count(rank) for rank in range(1, 11))
        return solve_split_counts(counts, hands, 6, False, active=active,
                                  stakes=stakes, allow_das=True,
                                  force_active=force, force_close=close,
                                  budget_seconds=5.0)

    def assert_reference(self, cards, hands, actual, *, active=0, stakes=(1, 1), awaiting="deal"):
        from tests.das_split_reference import das_split_reference
        expected = das_split_reference(cards, hands, 6, peek=False, active=active,
                                       stakes=stakes, awaiting=awaiting, allow_das=True)["deal"]
        self.assertAlmostEqual(actual["ev"], float(expected["ev"]), delta=1e-10)
        for key, value in actual["joint_distribution"].items():
            pair = tuple(map(int, key.split(",")))
            self.assertAlmostEqual(value, float(expected["joint_distribution"].get(pair, 0)), delta=1e-10)
        for key, value in actual["net_distribution"].items():
            self.assertAlmostEqual(value, float(expected["net_distribution"].get(int(key), 0)), delta=1e-10)

    def test_second_hand_hit_and_pending_hit_are_same_strategy(self):
        cards, hands = (8,) * 6, ((2, 10, 10), (2, 3))
        before = self.solve(cards, hands, active=1)["actions"]["hit"]
        pending = self.solve(cards, hands, active=1, force=True)["actions"]["deal"]
        self.assertAlmostEqual(before["ev"], 0.0, delta=1e-10)
        self.assert_reference(cards, hands, pending, active=1)
        self.assertAlmostEqual(pending["ev"], before["ev"], delta=1e-10)
        self.assertAlmostEqual(pending["hand_evs"][1], 1.0, delta=1e-10)

    def test_first_hand_hit_preserves_only_second_hand_das(self):
        cards, hands = (8,) * 8, ((2, 3), (2,))
        before = self.solve(cards, hands)["actions"]["hit"]
        pending = self.solve(cards, hands, force=True)["actions"]["deal"]
        self.assertAlmostEqual(before["ev"], 3.0, delta=1e-10)
        self.assert_reference(cards, hands, pending)
        self.assertAlmostEqual(pending["ev"], before["ev"], delta=1e-10)

    def test_initial_second_card_still_allows_later_das(self):
        cards, hands = (8,) * 8, ((2,), (2,))
        actual = self.solve(cards, hands, force=True)["actions"]["deal"]
        self.assert_reference(cards, hands, actual)
        self.assertAlmostEqual(actual["ev"], 4.0, delta=1e-10)

    def test_chosen_double_still_takes_one_card_at_stake_two(self):
        cards, hands = (8,) * 6, ((2, 10, 10), (2, 3))
        actual = self.solve(cards, hands, active=1, stakes=(1, 2), force=True, close=True)["actions"]["deal"]
        self.assert_reference(cards, hands, actual, active=1, stakes=(1, 2), awaiting="double")
        self.assertAlmostEqual(actual["ev"], 1.0, delta=1e-10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
