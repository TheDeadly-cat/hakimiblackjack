import json
import unittest
from dataclasses import FrozenInstanceError, replace

from blackjack_lab.analysis.contracts import research_rules
from blackjack_lab.analysis.split_contracts import (
    SplitHand, SPLIT_ORDER, split_research_rules, supported_split_rules,
    HARD_BUDGET_SECONDS, P95_TARGET_SECONDS)
from blackjack_lab.core.rules import RuleProfile
from tests.split_reference import split_reference


class TestSplitContracts(unittest.TestCase):
    def test_explicit_new_template_does_not_rewrite_old_four_hand_identity(self):
        for decks in (6,7,8):
            old=research_rules(decks)
            before=old.to_json()
            new=split_research_rules(decks)
            self.assertNotEqual(old.profile_id,new.profile_id)
            self.assertEqual(old.max_split_hands,4)
            self.assertIsNone(old.split_deal_order)
            self.assertEqual(old.to_json(),before)
            self.assertTrue(supported_split_rules(new))
            self.assertEqual(new.n_decks,decks)
            self.assertEqual(new.split_deal_order,SPLIT_ORDER)
            self.assertEqual(RuleProfile.from_json(new.to_json()),new)

    def test_legacy_rule_json_has_unknown_order_and_new_combinations_are_explicit(self):
        data=json.loads(research_rules().to_json())
        del data['split_deal_order']
        self.assertIsNone(RuleProfile.from_json(json.dumps(data)).split_deal_order)
        for field,value in [('max_split_hands',4),('double_after_split',True),
                            ('resplit_aces',True),('split_deal_order','both_second_cards_first'),
                            ('split_match','same_value'),('split_ace_hit_once',False)]:
            rules=split_research_rules()
            setattr(rules,field,value)
            with self.subTest(field=field):
                self.assertFalse(supported_split_rules(rules))

    def test_original_card_identity_and_units_are_immutable(self):
        hand=SplitHand('one',None,('8',),('card-one',),('8',),('card-one',),True,False,False,True)
        hand.validate()
        self.assertEqual(SplitHand.from_dict({**hand.__dict__}),hand)
        with self.assertRaises(FrozenInstanceError):
            hand.bet_units=2
        for changed in (replace(hand,origin_event_ids=('future-card',)),
                        replace(hand,bet_units=2),replace(hand,forced_draw=False),
                        replace(hand,closed=True),replace(hand,split_ace=True)):
            with self.assertRaises(ValueError):
                changed.validate()

    def test_fraction_reference_uses_net_not_returned_stake(self):
        value=split_reference((10,)*6,((1,),(1,)),6,split_aces=True)['deal']
        self.assertEqual(value['ev'],2)
        self.assertEqual(value['net_distribution'][2],1)
        self.assertEqual(value['joint_distribution'][(1,1)],1)

    def test_frozen_request_budget_and_performance_target(self):
        self.assertEqual(HARD_BUDGET_SECONDS,5.0)
        self.assertEqual(P95_TARGET_SECONDS,2.0)
