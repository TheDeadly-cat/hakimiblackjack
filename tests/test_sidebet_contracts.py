from dataclasses import replace
from fractions import Fraction
import unittest
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile


class SidebetContractTests(unittest.TestCase):
    def test_roundtrip_research_identity_and_unknown_paytable(self):
        p=SidebetProfile()
        self.assertEqual(SidebetProfile.from_dict(p.to_dict()),p)
        self.assertIn('不是已核对',p.source)
        self.assertIsNone(replace(p,confirmation='unconfirmed').payouts('perfect_pairs'))
        self.assertIsNone(replace(p,perfect_pairs=None).payouts('perfect_pairs'))

    def test_net_gross_and_loss_do_not_double_subtract_principal(self):
        p=SidebetProfile();dist={k:Fraction(k=='perfect') for k in ('perfect','coloured','mixed','loss')}
        self.assertEqual(p.ev('perfect_pairs',dist),25)
        gross=replace(p,payout_convention='gross_return',perfect_pairs=(26,13,7))
        self.assertEqual(gross.ev('perfect_pairs',dist),25)
        self.assertNotEqual(p.rules_digest,gross.rules_digest)
        self.assertIsNone(replace(p,confirmation='unconfirmed').ev('perfect_pairs',dist))
        dist['perfect'],dist['loss']=Fraction(),Fraction(1)
        self.assertEqual(p.ev('perfect_pairs',dist),-1)

    def test_bad_configs_fail_before_any_calculation(self):
        for values in (dict(perfect_pairs=(True,12,6)),dict(perfect_pairs=('NaN',12,6)),
                       dict(perfect_pairs=(float('inf'),12,6)),dict(ka2=1),
                       dict(three_priority=('flush',)*5),dict(payout_convention='guess'),
                       dict(split_policy='new_bet_on_split'),dict(version=True)):
            with self.assertRaises(ValueError):SidebetProfile(**values)
        data=SidebetProfile().to_dict();data['surprise']=True
        with self.assertRaises(ValueError):SidebetProfile.from_dict(data)
