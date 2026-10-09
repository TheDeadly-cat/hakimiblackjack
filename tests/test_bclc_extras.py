"""Side-bet unknowns and insurance unit accounting, separate from main EV."""
from dataclasses import replace
from types import SimpleNamespace
import unittest

from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.analysis.sidebets.exact import calculate, classify
from blackjack_lab.ui.bclc_help import insurance_summary
from blackjack_lab.ui.table_modes import bclc_rules


class BclcExtrasTests(unittest.TestCase):
    def profile(self):
        return SidebetProfile(confirmation='unconfirmed', perfect_pairs=None,
            twenty_one_plus_three=None, a23=None, qka=None, ka2=None)

    def test_unknown_ace_runs_do_not_inherit_research_assumptions_or_payouts(self):
        profile = self.profile()
        self.assertEqual(SidebetProfile.from_dict(profile.to_dict()), profile)
        result = calculate((8,)*52, profile)
        self.assertEqual(result['bets']['perfect_pairs']['status'], 'available')
        self.assertIsNone(result['bets']['perfect_pairs']['ev'])
        self.assertEqual(result['bets']['21+3']['status'], 'unavailable')
        self.assertIsNone(result['bets']['21+3']['probabilities'])
        self.assertIn('待确认', result['bets']['21+3']['reason'])
        with self.assertRaises(ValueError): replace(profile, confirmation='verified')

    def test_observed_unambiguous_category_works_but_ambiguous_ace_run_does_not(self):
        profile = self.profile()
        self.assertEqual(classify('21+3', [('8','S')]*3, profile), 'suited_trips')
        self.assertEqual(classify('21+3', [('3','S'),('4','H'),('5','C')], profile), 'straight')
        with self.assertRaisesRegex(ValueError, 'A顺子规则待确认'):
            classify('21+3', [('A','S'),('2','H'),('3','C')], profile)

    def test_insurance_units_use_two_to_one_net_odds_without_main_bet(self):
        plan = SimpleNamespace(mode='peek_wait', dealer_up_rank='A')
        text = insurance_summary(bclc_rules(), plan, dict(probability=1/3, fraction='1/3'))
        self.assertIn('最多半原注', text)
        self.assertIn('每1保险注', text)
        self.assertIn('+0.0000', text)
        self.assertIn('-0.2500', insurance_summary(bclc_rules(), plan,
            dict(probability=.25, fraction='1/4')))

    def test_insurance_unknown_composition_or_closed_window_has_no_ev(self):
        plan = SimpleNamespace(mode='peek_wait', dealer_up_rank='A')
        self.assertIn('研究EV不可用', insurance_summary(bclc_rules(), plan, dict(probability=None)))
        plan.mode = 'player_continuation'
        self.assertEqual(insurance_summary(bclc_rules(), plan, dict(probability=.3, fraction='3/10')), '')
