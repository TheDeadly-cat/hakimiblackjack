from collections import Counter
from dataclasses import replace
from fractions import Fraction
from itertools import combinations
import json
from math import comb
from pathlib import Path
import random
import unittest

from blackjack_lab.analysis.sidebets.contracts import SidebetProfile, CATEGORIES
from blackjack_lab.analysis.sidebets.exact import calculate, classify, distribution, validate_counts
from blackjack_lab.ledger.card_inventory import TYPES


def physical_oracle(cards,name,profile):
    """Enumerate distinct physical card instances, using independently expressed rank sets."""
    outcomes=Counter();k=2 if name=='perfect_pairs' else 3
    runs=[set(range(n,n+3)) for n in range(2,12)]
    if profile.a23:runs.append({1,2,3})
    if profile.qka:runs.append({1,12,13})
    if profile.ka2:runs.append({1,2,13})
    values={r:i+1 for i,r in enumerate(('A','2','3','4','5','6','7','8','9','10','J','Q','K'))}
    for ids in combinations(range(len(cards)),k):
        chosen=[cards[i] for i in ids]
        rank=Counter(values[c[0]] for c in chosen);suits=Counter(c[1] for c in chosen)
        if k==2:
            if len(rank)!=1:kind='loss'
            elif len(suits)==1:kind='perfect'
            elif set(suits) in ({'H','D'},{'S','C'}):kind='coloured'
            else:kind='mixed'
        else:
            same_suit=max(suits.values())==3
            triple=max(rank.values())==3
            run=len(rank)==3 and set(rank) in runs
            matches=set()
            if same_suit:matches.add('flush')
            if triple:matches.add('trips')
            if run:matches.add('straight')
            if same_suit and triple:matches.add('suited_trips')
            if same_suit and run:matches.add('straight_flush')
            kind=next((x for x in profile.three_priority if x in matches),'loss')
        outcomes[kind]+=1
    return {kind:Fraction(outcomes[kind],comb(len(cards),k)) for kind in (*CATEGORIES[name],'loss')}


class SidebetMathTests(unittest.TestCase):
    def test_physical_instances_independently_match_all_categories(self):
        rng=random.Random(927)
        deck=[('A','S'),('A','S'),('A','H'),('2','S'),('3','S'),('8','S'),('8','S'),
              ('8','S'),('8','H'),('8','C'),('10','C'),('J','H'),('Q','S'),('K','D')]
        for i in range(48):
            cards=deck if i==0 else rng.sample(deck,rng.randrange(3,len(deck)+1))
            counts=tuple(cards.count(t) for t in TYPES)
            p=replace(SidebetProfile(),a23=bool(i&1),qka=bool(i&2),ka2=bool(i&4))
            for name in CATEGORIES:
                expected=physical_oracle(cards,name,p)
                actual,_,_=distribution(name,counts,p)
                self.assertEqual(actual,expected,(i,name))
                self.assertEqual(sum(actual.values()),1)

    def test_full_shoes_against_closed_forms_and_review_fixtures(self):
        reference=json.loads((Path(__file__).resolve().parents[1]/'fixtures/acceptance/sidebet_math_v1.json').read_text(encoding='utf-8'))
        for fixture in reference['full_shoes']:
            d=fixture['n_decks'];r=calculate((d,)*52)
            closed={'suited_trips':52*comb(d,3),'straight_flush':48*d**3,
                    'trips':13*comb(4*d,3)-52*comb(d,3),
                    'straight':12*(4*d)**3-48*d**3,
                    'flush':4*comb(13*d,3)-52*comb(d,3)-48*d**3}
            closed['loss']=comb(52*d,3)-sum(closed.values())
            self.assertEqual(r['bets']['21+3']['ways'],closed)
            for name in CATEGORIES:
                bet=r['bets'][name]
                self.assertEqual(bet['fractions'],{k:v['fraction'] for k,v in fixture[name]['probabilities'].items()})
                self.assertEqual(bet['ev_fraction'],fixture[name]['ev_fraction'])
                self.assertAlmostEqual(sum(bet['probabilities'].values()),1)
            pp=r['bets']['perfect_pairs']['fractions']
            self.assertEqual(Fraction(pp['perfect']),Fraction(d-1,52*d-1))
            self.assertEqual(Fraction(pp['coloured']),Fraction(d,52*d-1))
            self.assertEqual(Fraction(pp['mixed']),Fraction(2*d,52*d-1))

    def test_original_rank_not_ten_bucket_and_multideck_categories(self):
        self.assertEqual(classify('perfect_pairs',[('10','S'),('K','S')]),'loss')
        self.assertEqual(classify('perfect_pairs',[('8','S'),('8','S')]),'perfect')
        self.assertEqual(classify('21+3',[('8','S')]*3),'suited_trips')
        self.assertEqual(classify('21+3',[('10','H'),('J','S'),('Q','D')]),'straight')
        with self.assertRaises(ValueError):classify('perfect_pairs',[('T','S'),('T','H')])

    def test_ace_policy_and_explicit_priority_change(self):
        p=SidebetProfile()
        for cards,field in (([('A','S'),('2','H'),('3','D')],'a23'),
                            ([('Q','S'),('K','H'),('A','D')],'qka')):
            self.assertEqual(classify('21+3',cards,p),'straight')
            self.assertEqual(classify('21+3',cards,replace(p,**{field:False})),'loss')
        cards=[('K','S'),('A','H'),('2','D')]
        self.assertEqual(classify('21+3',cards,p),'loss')
        self.assertEqual(classify('21+3',cards,replace(p,ka2=True)),'straight')
        cards=[('A','S'),('2','S'),('3','S')]
        self.assertEqual(classify('21+3',cards,p),'straight_flush')
        changed=replace(p,three_priority=('flush','suited_trips','straight_flush','trips','straight'))
        self.assertEqual(classify('21+3',cards,changed),'flush')

    def test_payout_changes_ev_only_and_unknown_is_null(self):
        p=SidebetProfile();a=calculate((8,)*52,p)
        z=calculate((8,)*52,replace(p,perfect_pairs=(30,10,5)))
        for name in CATEGORIES:self.assertEqual(a['bets'][name]['probabilities'],z['bets'][name]['probabilities'])
        self.assertNotEqual(a['bets']['perfect_pairs']['ev'],z['bets']['perfect_pairs']['ev'])
        unknown=calculate((8,)*52,replace(p,confirmation='unconfirmed'))
        for bet in unknown['bets'].values():
            self.assertIsNone(bet['ev']);self.assertEqual(bet['status'],'available')

    def test_partial_small_pool_and_illegal_inputs(self):
        counts=[0]*52;counts[0]=2
        result=calculate(counts)
        self.assertEqual(result['bets']['perfect_pairs']['ev'],25)
        self.assertEqual(result['bets']['21+3']['status'],'unavailable')
        for counts in ([],[True]*52,[-1]*52,[9]*52):
            with self.assertRaises(ValueError):validate_counts(counts)
        for value in ('1e1000000000','NaN','Infinity','0.0000000000001'):
            with self.assertRaises(ValueError):SidebetProfile(perfect_pairs=(value,12,6))
