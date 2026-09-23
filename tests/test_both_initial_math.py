"""New order checked against independent physical-world Fraction enumeration."""
import unittest

from blackjack_lab.analysis.native_backend import build_native, solve_native, solve_presplit_native
from tests.das_split_reference import das_split_reference
from tests.test_das_split_engine import counts_of


class TestBothInitialMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def compare(self, cards, hands, up=6, peek=False, active=0, aces=False, stakes=(1, 1), awaiting=None):
        expected = das_split_reference(cards, hands, up, peek=peek, active=active,
            split_aces=aces, stakes=stakes, awaiting=awaiting, both_initial=True)
        result = solve_native(counts_of(cards), hands, up, peek, active=active, split_aces=aces,
            stakes=stakes, allow_das=True, both_initial=True,
            force_active=awaiting is not None, force_close=awaiting == 'double')
        self.assertEqual(set(result['actions']), set(expected))
        for action, item in expected.items():
            actual = result['actions'][action]
            self.assertAlmostEqual(actual['ev'], float(item['ev']), delta=1e-10)
            for pair, p in actual['joint_distribution'].items():
                ref = item['joint_distribution'].get(tuple(map(int, pair.split(','))), 0)
                self.assertAlmostEqual(p, float(ref), delta=1e-10, msg=f'{action} {pair}')
            for net, p in actual['net_distribution'].items():
                self.assertAlmostEqual(p, float(item['net_distribution'][int(net)]), delta=1e-10)
            for a, b in zip(actual['hand_evs'], item['hand_evs']):
                self.assertAlmostEqual(a, float(b), delta=1e-10)
        return result

    def test_initial_deals_and_visible_second_hand(self):
        cards = (8, 8, 9, 9, 10, 10)
        for hands, active in [(((8,), (8,)), 0), (((8, 3), (8,)), 1),
                              (((8, 3), (8, 9)), 0), (((8, 9), (8, 3)), 1),
                              (((8, 10, 3), (8,)), 1)]:
            if len(hands[0]) > 2:  # An action before the other initial card is forbidden.
                with self.assertRaises(ValueError):
                    solve_native(counts_of(cards), hands, 6, False, active=active, allow_das=True, both_initial=True)
                continue
            with self.subTest(hands=hands, active=active):
                self.compare(cards, hands, active=active)

    def test_natural_rules_terminal_hands_and_pending_double(self):
        self.compare((10,) * 6, ((1,), (1,)), aces=True)
        self.compare((8, 8, 9, 9, 10, 10), ((10, 1), (10,)), active=1)
        self.compare((8, 8, 9, 9, 10, 10), ((10, 9), (10, 1)))
        self.compare((8, 8, 9, 9, 10, 10), ((8, 3), (8, 9)), stakes=(2, 1), awaiting='double')
        self.compare((8, 8, 9, 9, 10, 10), ((8, 3, 10), (8, 9)), active=1, stakes=(2, 1))
        self.compare((1, 9, 9, 10, 10, 10), ((8,), (8,)), up=10)
        self.compare((8, 8, 9, 9, 10, 10), ((8,), (8,)), up=1, peek=True)

    def test_presplit_is_same_joint_tree_and_new_information_changes_ev(self):
        cards = (1, 5, 8, 9, 10, 10, 10, 10)
        new = self.compare(cards, ((8,), (8,)), up=10)['actions']['deal']
        pre = solve_presplit_native(counts_of(cards), (8, 8), 10, False,
            ('stand', 'hit', 'double', 'split'), allow_das=True, both_initial=True)
        self.assertEqual(new['joint_distribution'], pre['actions']['split']['joint_distribution'])
        old = solve_native(counts_of(cards), ((8,), (8,)), 10, False, allow_das=True)
        self.assertNotAlmostEqual(new['ev'], old['actions']['deal']['ev'], delta=1e-10)


if __name__ == '__main__':
    unittest.main()
