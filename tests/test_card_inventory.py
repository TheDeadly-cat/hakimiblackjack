from dataclasses import replace
import unittest

from blackjack_lab.ledger.card_inventory import project, original_cards, TYPE_INDEX
from blackjack_lab.analysis.dealer_blackjack import evaluate
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.core.table import ACTION_SPLIT
from tests.test_dealer_blackjack import ledger
from tests import test_analysis_ui as fixture


class CardInventoryTests(unittest.TestCase):
    def test_full_shoe_exact_counts_and_capacity_in_multideck(self):
        for d in (6,7,8):
            b=ledger(d);self.assertEqual(project(b)['counts'],(d,)*52)
        b=ledger();b.start_round([f'玩家{i}' for i in range(1,8)])
        for i in range(8):b.deal(f'玩家{i%7+1}','8',suit='S')
        before=b.to_list();result=project(b)
        self.assertEqual(result['status'],'available')
        self.assertEqual(result['counts'][TYPE_INDEX[('8','S')]],0)
        self.assertEqual(len(result['cards']),8)
        b.deal('玩家2','8',suit='S')
        result=project(b);self.assertEqual(result['reason_code'],'SUIT_CAPACITY')
        self.assertIsNone(result['counts']);self.assertEqual(b.to_list()[:-1],before)

    def test_unknown_types_and_suits_are_not_imputed(self):
        b=ledger();b.start_round(['玩家1'])
        shown=b.deal('玩家1','8')
        result=project(b);self.assertEqual(result['missing'],{'unknown_suit':1})
        self.assertIsNone(result['counts']);self.assertIsNotNone(evaluate(b)['probability'])
        b.correct(shown.event_id,{'suit':'S'},'observed suit')
        self.assertEqual(project(b)['status'],'available')
        b.deal('庄家','T',suit='H')
        self.assertEqual(project(b)['missing'],{'ten_rank_unspecified':1})
        b.burn(1);self.assertIn('unknown_burn',project(b)['missing'])
        b.gap('missing observations');self.assertIn('observation_gap',project(b)['missing'])

    def test_reveal_correct_undo_reuse_one_physical_id_and_preserve_prefix(self):
        b=ledger();b.start_round(['玩家1']);b.deal('庄家','6',suit='H')
        hole=b.deal('庄家',hidden=True)
        before=project(b);seq=hole.seq
        revealed=b.reveal(hole.event_id,'9',suit='S')
        after=project(b)
        self.assertEqual(len(after['cards']),2)
        self.assertEqual(sum(after['counts']),414)
        self.assertEqual(after['cards'][-1]['event_id'],hole.event_id)
        self.assertEqual(project(b,seq),before)
        b.correct(revealed.event_id,{'suit':'C'},'corrected actual suit')
        self.assertEqual(project(b)['counts'][TYPE_INDEX[('9','C')]],7)
        b.undo_last();self.assertEqual(project(b)['counts'],after['counts'])
        b.undo_last();self.assertIsNone(project(b)['counts'])
        self.assertEqual(len(project(b)['cards']),2)

    def test_split_keeps_original_ids_and_dealer_up_excludes_hole(self):
        b=ledger();b.start_round(['玩家1'])
        first=b.deal('玩家1','8',suit='S');up=b.deal('庄家','6',suit='D')
        second=b.deal('玩家1','8',suit='H');hole=b.deal('庄家',hidden=True)
        current=b.replay().current;rid=current.round_id
        b.player_action('玩家1',current.table.players['玩家1'].hands[0].hand_id,ACTION_SPLIT)
        hands=b.replay().current.table.players['玩家1'].hands
        b.deal('玩家1','2',suit='D',hand_id=hands[0].hand_id)
        b.deal('玩家1','3',suit='C',hand_id=hands[1].hand_id)
        b.reveal(hole.event_id,'9',suit='D')
        result=original_cards(project(b),rid,'玩家1')
        self.assertEqual([c['event_id'] for c in result['perfect_pairs']],[first.event_id,second.event_id])
        self.assertEqual([c['event_id'] for c in result['twenty_one_plus_three']],
                         [first.event_id,second.event_id,up.event_id])
        self.assertEqual(sum(project(b)['counts']),410)

    def test_suit_only_correction_keeps_main_bet_numerics(self):
        b=ledger();b.start_round(['玩家1'])
        b.deal('庄家','6',suit='D');b.deal('庄家',hidden=True)
        b.deal('玩家1','10',suit='S');card=b.deal('玩家1','6')
        before=build_input(b,'玩家1');a=calculate(before)
        b.correct(card.event_id,{'suit':'H'},'suit only')
        after=build_input(b,'玩家1');z=calculate(after)
        self.assertEqual(before.counts,after.counts)
        self.assertNotEqual(before.prefix_digest,after.prefix_digest)
        self.assertEqual(a['status'],'available');self.assertEqual(z['status'],'available')
        self.assertEqual({k:v.get('ev') for k,v in a['actions'].items()},
                         {k:v.get('ev') for k,v in z['actions'].items()})

    def test_new_shoe_and_group_undo_do_not_mix_inventory(self):
        # Real composite replacement/recovery are covered by controller tests; this isolates prefixes.
        b=ledger();b.start_round(['玩家1']);b.deal('玩家1','8',suit='S')
        b.end_round(settle=False,observation_status='unknown');b.end_shoe()
        before=project(b)
        from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
        b.create_shoe(ace_peek_das_research_rules(7))
        self.assertEqual(project(b)['counts'],(7,)*52)
        b.undo_last();self.assertEqual(project(b)['cards'],before['cards'])


class SuitInputUITests(unittest.TestCase):
    setUp=fixture.TestAnalysisUI.setUp
    close=fixture.TestAnalysisUI.close

    def test_optional_one_card_suit_and_recent_append_correction(self):
        app=self.app;app.act_common_settings();app.act_new_shoe();app.act_new_round()
        panel=app.compact_panel
        self.assertFalse(app.var_sidebet_suits.get())
        app._key_rank('8')
        self.assertEqual(project(app.ctrl.ledger)['missing'],{'unknown_suit':1})
        panel.open_correction();panel.edit_suit.set('S');panel.save_correction.invoke()
        self.assertEqual(project(app.ctrl.ledger)['status'],'available')
        self.assertFalse(app.ctrl.entry_plan.paused)
        app.var_sidebet_suits.set(True);panel.toggle_suit_input();app.var_suit.set('H')
        app._key_rank('6')
        self.assertEqual(app.var_suit.get(),'未知')
        self.assertEqual(project(app.ctrl.ledger)['cards'][-1]['suit'],'H')
        app._key_rank('8')
        self.assertIsNone(project(app.ctrl.ledger)['cards'][-2]['suit'])
        self.assertIn('unknown_suit',project(app.ctrl.ledger)['missing'])
        self.assertIsNotNone(app.ctrl.current_decision_input('玩家1'))
        self.assertEqual(self.errors,[])
