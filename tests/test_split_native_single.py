"""The accelerated pre-split alternatives must retain the single-hand mathematics."""
import unittest

from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.native_backend import build_native
from tests.analysis_reference import reference


class TestSplitNativeSingle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_native()

    def assert_values(self,actual,expected):
        self.assertEqual(set(actual['actions']),set(expected['actions']))
        for action,item in expected['actions'].items():
            result=actual['actions'][action]
            self.assertAlmostEqual(result['ev'],float(item['ev']),delta=1e-10)
            target={float(k):float(v) for k,v in item['net_distribution'].items()}
            observed={float(k):v for k,v in result['net_distribution'].items()}
            for outcome in set(target)|set(observed):
                self.assertAlmostEqual(observed.get(outcome,0),target.get(outcome,0),delta=1e-10)

    def test_independent_fraction_stand_hit_double_surrender_and_natural(self):
        from blackjack_lab.analysis.native_backend import solve_presplit_native
        for player,up,peek in (((10,6),6,False),((1,6),10,True),((8,8),1,True),((1,10),10,True)):
            cards=(1,2,8,9,10,10,10)
            actions=('stand',) if player==(1,10) else ('stand','hit','double','surrender')
            expected=reference(cards,player,up,peek,actions)
            result=solve_presplit_native(tuple(cards.count(v) for v in range(1,11)),player,up,peek,actions)
            self.assert_values(result,expected)

    def test_full_shoe_alternatives_match_existing_solver_on_three_deck_sizes(self):
        from blackjack_lab.analysis.native_backend import solve_presplit_native
        for decks in (6,7,8):
            for player,up in ((('2','2'),'2'),(('A','A'),'6'),(('8','8'),'6'),
                              (('10','10'),'A'),(('A','10'),'10'),(('A','6'),'9')):
                values={'A':1,**{str(v):v for v in range(2,11)}}
                hand=tuple(values[v] for v in player)
                dealer=values[up]
                counts=[4*decks]*9+[16*decks]
                for v in (*hand,dealer):
                    counts[v-1]-=1
                actions=('stand',) if sorted(hand)==[1,10] else ('stand','hit','double','surrender')
                expected=solve_counts(counts,hand,dealer,dealer in (1,10),actions)
                result=solve_presplit_native(counts,hand,dealer,dealer in (1,10),actions)
                with self.subTest(decks=decks,player=player,up=up):
                    self.assert_values(result,expected)
                    self.assertAlmostEqual(result['hit_bust'],expected['hit_bust'],delta=1e-10)
                    for k,value in expected['next_draw'].items():
                        self.assertAlmostEqual(result['next_draw'][k],value,delta=1e-10)
                    for k,value in expected['dealer_distribution'].items():
                        self.assertAlmostEqual(result['dealer_distribution'][k],value,delta=1e-10)


if __name__=='__main__':
    unittest.main()
