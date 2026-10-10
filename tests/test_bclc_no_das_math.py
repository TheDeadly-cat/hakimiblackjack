"""BCLC both-card/no-DAS model against independent physical-world fractions."""
import unittest

from blackjack_lab.analysis.native_backend import build_native, solve_native, solve_presplit_native
from blackjack_lab.analysis.split_contracts import NO_DAS_BOTH_INITIAL_STRATEGY
from tests.das_split_reference import das_split_reference
from tests.test_das_split_engine import counts_of


class BclcNoDasMathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def compare(self, cards, hands, up=6, peek=False, active=0, aces=False):
        expected = das_split_reference(cards, hands, up, peek=peek, active=active,
            split_aces=aces, allow_das=False, both_initial=True)
        result = solve_native(counts_of(cards), hands, up, peek, active=active,
            split_aces=aces, allow_das=False, both_initial=True)
        self.assertEqual(set(result['actions']), set(expected))
        self.assertEqual(result['strategy'], NO_DAS_BOTH_INITIAL_STRATEGY)
        for action, reference in expected.items():
            item = result['actions'][action]
            self.assertAlmostEqual(item['ev'], float(reference['ev']), delta=1e-10)
            self.assertEqual(len(item['joint_distribution']), 9)
            for pair, p in item['joint_distribution'].items():
                ref = reference['joint_distribution'].get(tuple(map(int, pair.split(','))), 0)
                self.assertAlmostEqual(p, float(ref), delta=1e-10, msg=f'{action} {pair}')
            for net, p in item['net_distribution'].items():
                self.assertAlmostEqual(p, float(reference['net_distribution'][int(net)]), delta=1e-10)
            for actual, ref in zip(item['hand_evs'], reference['hand_evs']):
                self.assertAlmostEqual(actual, float(ref), delta=1e-10)
        return result

    def test_deal_both_then_first_hand_and_second_hand(self):
        cards = (8, 8, 9, 9, 10, 10)
        for hands, active in [(((8,), (8,)), 0), (((8, 3), (8,)), 1),
                              (((8, 3), (8, 9)), 0), (((8, 9), (8, 3)), 1),
                              (((8, 9), (8, 9)), 2)]:
            with self.subTest(hands=hands, active=active):
                self.compare(cards, hands, active=active)

    def test_split_aces_and_split_21_are_not_naturals(self):
        self.compare((10,) * 6, ((1,), (1,)), aces=True)
        self.compare((8, 8, 9, 9, 10, 10), ((10, 1), (10,)), active=1)
        result = self.compare((1, 9, 9, 10, 10, 10), ((10, 1), (10, 1)), up=10, active=2)
        self.assertGreater(result['actions']['complete']['joint_distribution']['-1,-1'], 0)

    def test_ace_negative_peek_and_ten_no_peek_conditioning(self):
        self.compare((8, 8, 9, 9, 10, 10), ((8,), (8,)), up=1, peek=True)
        self.compare((1, 9, 9, 10, 10, 10), ((8,), (8,)), up=10)

    def test_presplit_uses_same_joint_tree_and_original_double_still_exists(self):
        cards = (1, 5, 8, 9, 10, 10, 10, 10)
        new = self.compare(cards, ((8,), (8,)), up=10)['actions']['deal']
        pre = solve_presplit_native(counts_of(cards), (8, 8), 10, False,
            ('stand', 'hit', 'double', 'split'), allow_das=False, both_initial=True)
        self.assertEqual(new['joint_distribution'], pre['actions']['split']['joint_distribution'])
        self.assertIn('double', pre['actions'])
        old = solve_native(counts_of(cards), ((8,), (8,)), 10, False, allow_das=False)
        self.assertNotAlmostEqual(new['ev'], old['actions']['deal']['ev'], delta=1e-10)

    def test_physical_order_and_unit_stakes_reject_invalid_inputs(self):
        counts = counts_of((8, 8, 9, 9, 10, 10))
        for hands, stakes in [(((8, 3, 10), (8,)), (1, 1)),
                              (((8, 3), (8, 9)), (2, 1))]:
            with self.subTest(hands=hands, stakes=stakes), self.assertRaises(ValueError):
                solve_native(counts, hands, 6, False, stakes=stakes, allow_das=False, both_initial=True)
