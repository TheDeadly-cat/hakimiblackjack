"""Moderate finite physical-world differentials, separate from timing measurements."""
import unittest

from blackjack_lab.analysis.native_backend import build_native, solve_native
from tests.das_split_reference import das_split_reference


class TestCandidateModelMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def compare(self, hands, *, both=True, up=10, active=0, stakes=(1,1), awaiting=None):
        # 10 physical cards, 5 ranks, 5,040 distinct possible full deals before peek.
        cards = (1, 5, 8, 9, 10, 10, 10, 10, 10, 10)
        expected = das_split_reference(cards, hands, up, peek=up==1, active=active, stakes=stakes,
                                       awaiting=awaiting, both_initial=both)
        actual = solve_native(tuple(cards.count(v) for v in range(1,11)), hands, up, up==1,
            active=active, stakes=stakes, force_active=awaiting is not None, force_close=awaiting=='double',
            allow_das=True, both_initial=both)['actions']
        self.assertEqual(set(actual), set(expected))
        for action, result in actual.items():
            reference = expected[action]
            self.assertAlmostEqual(result['ev'], float(reference['ev']), delta=1e-10, msg=action)
            for pair, probability in result['joint_distribution'].items():
                key = tuple(map(int, pair.split(',')))
                self.assertAlmostEqual(probability, float(reference['joint_distribution'].get(key,0)), delta=1e-10, msg=f'{action}:{pair}')
            for net, probability in result['net_distribution'].items():
                self.assertAlmostEqual(probability, float(reference['net_distribution'][int(net)]), delta=1e-10)
            for number, ref in zip(result['hand_evs'], reference['hand_evs']):
                self.assertAlmostEqual(number, float(ref), delta=1e-10)

    def test_both_visible_soft_and_multi_ace(self):
        self.compare(((2,1),(2,7)))
        self.compare(((2,1,1),(2,7)), up=1)

    def test_hit_and_double_pending_use_distinct_continuations(self):
        self.compare(((8,3),(8,9)), awaiting='deal')
        self.compare(((8,3),(8,9)), stakes=(2,1), awaiting='double')
        self.compare(((8,9),(8,3)), active=1, awaiting='deal', up=1)

    def test_old_order_still_matches_its_own_information_set(self):
        self.compare(((8,3),(8,)), both=False, awaiting='deal')
        self.compare(((8,3),(8,)), both=False, stakes=(2,1), awaiting='double', up=1)


if __name__ == '__main__':
    unittest.main()
