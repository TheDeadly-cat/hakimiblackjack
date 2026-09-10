import unittest
from math import fsum
from blackjack_lab.analysis.split_actions import solve_split_counts
from blackjack_lab.analysis.probability import CalculationStopped, InsufficientCards
from blackjack_lab.analysis.native_backend import build_native
from tests.split_reference import split_reference


class TestSplitMath(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def compare(self, cards, hands, up, active=0, aces=False, peek=True):
        expected=split_reference(cards,hands,up,peek,active,aces)
        result=solve_split_counts(tuple(cards.count(i) for i in range(1,11)),hands,up,peek,active=active,split_aces=aces)
        self.assertEqual(set(result['actions']),set(expected))
        for action,item in expected.items():
            actual=result['actions'][action]
            self.assertAlmostEqual(actual['ev'],float(item['ev']),delta=1e-10)
            self.assertAlmostEqual(sum(actual['net_distribution'].values()),1.,delta=1e-10)
            self.assertTrue(all(p>=0 for p in actual['joint_distribution'].values()))
            self.assertAlmostEqual(actual['ev'],fsum(float(v)*p for v,p in actual['net_distribution'].items()),delta=1e-10)
            self.assertAlmostEqual(actual['ev'],sum(actual['hand_evs']),delta=1e-10)
            for (a,b),p in item['joint_distribution'].items():
                self.assertAlmostEqual(actual['joint_distribution'][f'{a},{b}'],float(p),delta=1e-10)
            for v,p in item['net_distribution'].items():
                self.assertAlmostEqual(actual['net_distribution'][str(v)],float(p),delta=1e-10)
        return result

    def test_physical_worlds_match_joint_and_total_payoffs(self):
        cases=[((8,8,9,9,10,10),((8,),(8,)),6,0,False),
               ((8,8,9,9,10,10),((1,),(1,)),10,0,True),
               ((8,8,9,9,10,10),((10,),(10,)),1,0,False),
               ((8,8,9,9,10,10),((8,10,10),(8,)),6,1,False),
               ((8,8,9,9,10,10),((8,8),(8,9)),10,1,False),
               ((8,8,9,9,10,10),((8,8),(8,9)),6,2,False),
               ((1,8,8,9,10,10),((8,9),(8,)),10,0,False),
               ((1,8,8,9,10,10),((8,),(8,)),1,0,False),
               ((1,8,8,9,10,10,10,10,10),((2,),(2,)),2,0,False)]
        for cards,hands,up,active,aces in cases:
            with self.subTest(hands=hands,up=up):
                self.compare(cards,hands,up,active,aces)

    def test_safe_low_dominance_with_fraction_worlds_at_64_card_threshold(self):
        # Few distinct ranks keep complete physical-world enumeration tractable;
        # every distinct permutation still carries its exact labeled multiplicity.
        for size in (63,64,66):
            with self.subTest(size=size):
                self.compare((10,)*(size-2)+(1,8),((2,),(2,)),10)

    def test_information_gain_bound_is_exercised_and_matches_complete_fraction_search(self):
        result=self.compare((10,)*64+(1,8),((8,10,1),(8,)),10)
        self.assertGreater(result['information_bound_prunes'],0)

    def test_ace_soft_transition_and_negative_peek(self):
        self.compare((1,1,8,9,10,10,10),((2,1),(2,)),10)
        self.compare((1,1,8,9,10,10,10),((8,),(8,)),6,peek=False)

    def test_shared_dealer_is_not_independent_convolution(self):
        r=self.compare((8,8,9,9,10,10),((10,9),(10,9)),10,2)
        d=r['actions']['complete']['joint_distribution']
        self.assertEqual(d['1,-1'],0)
        self.assertEqual(d['-1,1'],0)
        self.assertGreater(d['1,1'],0)
        self.assertGreater(d['-1,-1'],0)

    def test_split_ace_21_is_ordinary_two_units(self):
        r=self.compare((10,)*6,((1,),(1,)),6,aces=True)
        self.assertEqual(r['actions']['deal']['ev'],2.)
        self.assertEqual(r['actions']['deal']['net_distribution']['2'],1.)

    def test_first_bust_still_values_second_hand(self):
        r=self.compare((8,8,9,9,10,10),((8,8,10),(8,)),6,1)
        value=r['actions']['deal']
        self.assertEqual(value['hand_evs'][0],-1.)
        self.assertGreater(value['hand_evs'][1],-1.)
        self.assertEqual(value['net_distribution']['1'],0.)
        self.assertEqual(value['net_distribution']['2'],0.)

    def test_wrong_order_and_insufficient_physical_cards_are_explicit(self):
        with self.assertRaises(ValueError):
            solve_split_counts((1,)*10,((8,),(8,9)),6,False)
        with self.assertRaises(InsufficientCards):
            solve_split_counts((0,)*9+(1,),((8,),(8,)),6,False)
        with self.assertRaises(InsufficientCards):
            solve_split_counts((0,)*9+(1,),((8,10),(10,10)),10,True,active=1)

    def test_timeout_does_not_return_partial_ev(self):
        with self.assertRaises(CalculationStopped):
            solve_split_counts((24,)*9+(96,),((2,),(2,)),2,False,budget_seconds=.0001)


if __name__=='__main__':
    unittest.main()
