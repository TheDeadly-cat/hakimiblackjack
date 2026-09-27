from dataclasses import replace
from fractions import Fraction
import unittest

from blackjack_lab.analysis.dealer_blackjack import evaluate, scalar
from blackjack_lab.analysis.probability import FiniteModel
from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
from blackjack_lab.ledger.ledger import EventLedger
from tests import test_analysis_ui as fixture


def ledger(decks=8):
    book = EventLedger('dealer-bj-test')
    book.start_session();book.create_shoe(ace_peek_das_research_rules(decks))
    return book


class DealerBJTests(unittest.TestCase):
    def test_full_shoes_and_no_action_dependency(self):
        for d in (6,7,8):
            book=ledger(d);before=book.to_list();r=evaluate(book)
            self.assertEqual(Fraction(r['fraction']),Fraction(2*4*d*16*d,52*d*(52*d-1)))
            self.assertEqual(r['phase'],'predeal');self.assertTrue(r['forecast'])
            self.assertEqual(book.to_list(),before)
        self.assertEqual(Fraction(evaluate(ledger())['fraction']),Fraction(256,5395))

    def test_scalar_matches_existing_finite_model(self):
        # Small but sufficient compositions: exact recursion is separate from the new scalar.
        counts=(1,1,1,0,0,0,1,1,1,4)
        for up in range(1,11):
            for peek in (False,True):
                self.assertAlmostEqual(float(scalar(counts,up,peek)),
                    FiniteModel(up,peek).dealer_distribution(counts)[0],delta=1e-12)

    def test_pool_contains_hole_and_peek_undo_and_old_prefix(self):
        book=ledger();book.start_round(['玩家1'])
        book.deal('玩家1','8');book.deal('庄家','A');book.deal('玩家1','8')
        hole=book.deal('庄家',hidden=True);seq=hole.seq
        r=evaluate(book);self.assertEqual(Fraction(r['fraction']),Fraction(128,413))
        self.assertEqual(book.replay().current.shoe.physical_remaining(),412)
        self.assertEqual(r['phase'],'conditional')
        book.peek_negative();self.assertEqual(evaluate(book)['phase'],'excluded')
        book.undo_last();self.assertEqual(evaluate(book)['fraction'],r['fraction'])
        book.reveal(hole.event_id,'K')
        self.assertEqual(evaluate(book)['probability'],1)
        self.assertEqual(evaluate(book)['phase'],'revealed')
        self.assertEqual(evaluate(book,seq),r)

    def test_structural_zero_and_revealed_fact_survive_gap(self):
        for up in ('2','3','4','5','6','7','8','9'):
            book=ledger();book.start_round(['玩家1']);book.deal('庄家',up)
            book.gap('test missing observations')
            self.assertEqual(evaluate(book)['probability'],0)
        book=ledger();book.start_round(['玩家1']);book.deal('庄家','A')
        book.gap('test missing observations')
        self.assertIsNone(evaluate(book)['probability'])
        book.deal('庄家','K')
        self.assertEqual(evaluate(book)['probability'],1)

    def test_unconfirmed_start_does_not_invent_counts(self):
        book=EventLedger('incomplete');book.start_session()
        book.create_shoe(replace(ace_peek_das_research_rules(8),start_from_new_shoe=None))
        self.assertIsNone(evaluate(book)['probability'])
        book.start_round(['玩家1']);book.deal('庄家','6')
        self.assertEqual(evaluate(book)['probability'],0)

    def test_ten_ranks_and_three_card_21(self):
        for rank in ('10','J','Q','K','T'):
            book=ledger();book.start_round(['玩家1']);book.deal('庄家',rank)
            self.assertEqual(Fraction(evaluate(book)['fraction']),Fraction(32,415))
        book=ledger();book.start_round(['玩家1'])
        for rank in ('7','5','9'):book.deal('庄家',rank)
        self.assertEqual(evaluate(book)['probability'],0)
        self.assertEqual(evaluate(book)['phase'],'revealed')

    def test_invalid_prefix_insufficient_pool_and_contradiction_are_not_zero(self):
        self.assertIsNone(evaluate(ledger(),999)['probability'])
        with self.assertRaises(ValueError):scalar((0,)*10)
        with self.assertRaises(ValueError):scalar((0,)*9+(3,),1,True)
        with self.assertRaises(ValueError):scalar((True,)*10)


class DealerBJUITests(unittest.TestCase):
    setUp=fixture.TestAnalysisUI.setUp
    close=fixture.TestAnalysisUI.close

    def test_ace_wait_shows_risk_without_fabricating_check(self):
        app=self.app;app.act_common_settings();app.act_new_shoe();app.act_new_round()
        for rank in ('8','A','8'):app._key_rank(rank)
        app.update()
        with self.assertRaises(InputUnavailable):app.ctrl.current_decision_input('玩家1')
        self.assertIn('BJ 30.99%',app.compact_panel.identity.get())
        self.assertFalse(any(e.etype=='PEEK_NEGATIVE' for e in app.ctrl.ledger.events))
        app.act_peek_negative();app.update()
        self.assertIn('已排除BJ',app.compact_panel.identity.get())
        app.act_undo();app.update()
        self.assertIn('BJ 30.99%',app.compact_panel.identity.get())
        self.assertEqual(self.errors,[])
