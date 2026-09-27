from dataclasses import replace
import threading
import unittest
from unittest.mock import patch

from blackjack_lab.ui.observation_identity import observed_identity
from blackjack_lab.analysis.sidebets.information import build_input,execute
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.core.table import ACTION_SPLIT
from tests.test_dealer_blackjack import ledger
from tests import test_sidebet_history as fixture


class ObservationIdentityTests(unittest.TestCase):
    def identity(self,b,seat='玩家1',profile=None):
        return observed_identity(b,b.replay().current,seat,profile or SidebetProfile())

    def test_later_cards_split_reveal_do_not_change_original_result_but_correction_and_undo_do(self):
        b=ledger();b.start_round(['玩家1'])
        first=b.deal('玩家1','8',suit='S');b.deal('庄家','6',suit='D')
        b.deal('玩家1','8',suit='H');hole=b.deal('庄家',hidden=True)
        key=self.identity(b);before=execute(build_input(b,'玩家1',purpose='observed'))['output']
        b.player_action('玩家1',b.replay().current.table.players['玩家1'].hands[0].hand_id,ACTION_SPLIT)
        hands=b.replay().current.table.players['玩家1'].hands
        b.deal('玩家1','2',suit='C',hand_id=hands[0].hand_id)
        b.deal('玩家1','3',suit='S',hand_id=hands[1].hand_id)
        b.reveal(hole.event_id,'9',suit='D')
        self.assertEqual(self.identity(b),key)
        self.assertEqual(execute(build_input(b,'玩家1',purpose='observed'))['output'],before)
        b.correct(first.event_id,{'suit':'H'},'correct observed suit')
        self.assertNotEqual(self.identity(b),key)
        b.undo_last();self.assertEqual(self.identity(b),key)
        self.assertNotEqual(self.identity(b,profile=replace(SidebetProfile(),confirmation='unconfirmed')),key)

    def test_unrelated_capacity_violation_and_undo_invalidate_even_when_original_cards_unchanged(self):
        b=ledger();b.start_round([f'玩家{i}' for i in range(1,8)])
        b.deal('玩家1','2',suit='S');b.deal('玩家1','3',suit='H');b.deal('庄家','6',suit='D')
        b.deal('庄家',hidden=True)
        key=self.identity(b)
        for i in range(8):b.deal(f'玩家{2+i%6}','8',suit='S')
        self.assertEqual(self.identity(b),key)
        b.deal('玩家4','8',suit='S');self.assertNotEqual(self.identity(b),key)
        self.assertEqual(execute(build_input(b,'玩家1',purpose='observed'))['status'],'unavailable')
        b.undo_last();self.assertEqual(self.identity(b),key)
        self.assertNotEqual(self.identity(b,'玩家2'),key)


class ObservedDedupUITests(unittest.TestCase):
    setUp=fixture.SidebetUITests.setUp
    close=fixture.SidebetUITests.close
    start=fixture.SidebetUITests.start
    forecast=fixture.SidebetUITests.forecast
    pump=fixture.SidebetUITests.pump

    def test_unchanged_followup_retains_original_saved_prefix_late_completion_and_correction_refreshes(self):
        self.start();self.forecast()
        for rank,suit in [('2','S'),('6','D'),('3','H')]:
            self.app.var_suit.set(suit);self.app._key_rank(rank)
        self.pump(lambda:self.view.observed is not None)
        observed=self.view.observed;request=observed['result']['request_id']
        path=self.view.store.directory/(observed['saved_id']+'.json');original=path.read_bytes()
        with patch.object(self.view,'_request',wraps=self.view._request) as calls:
            self.app.var_suit.set('C');self.app._key_rank('2');self.app.update()
            self.assertEqual(calls.call_count,0)
        self.assertEqual(self.view.observed['result']['request_id'],request)
        self.assertLess(observed['result']['input']['through_seq'],self.app.ctrl.ledger.events[-1].seq)
        card=observed['result']['input']['original_cards']['perfect_pairs'][0]['event_id']
        self.app.ctrl.correct(card,{'suit':'H'},'verified correction');self.app.refresh_all()
        self.pump(lambda:self.view.observed is not None and self.view.observed['result']['request_id']!=request)
        self.assertEqual(path.read_bytes(),original)

    def test_late_observed_can_publish_only_if_original_identity_still_matches(self):
        self.start();self.forecast()
        from blackjack_lab.ui import sidebet_view
        original=sidebet_view.execute;entered=threading.Event();release=threading.Event()
        for rank,suit in [('2','S'),('6','D')]:self.app.var_suit.set(suit);self.app._key_rank(rank)
        self.pump(lambda:self.view.observed is not None)
        def slow(*args):entered.set();release.wait(4);return original(*args)
        try:
            with patch('blackjack_lab.ui.sidebet_view.execute',side_effect=slow):
                self.app.var_suit.set('H');self.app._key_rank('3');self.assertTrue(entered.wait(1))
                request=self.view.observed_request
                self.app.var_suit.set('C');self.app._key_rank('2')
                self.assertEqual(self.view.observed_request,request)
                release.set();self.pump(lambda:self.view.observed is not None)
                self.assertEqual(self.view.observed['result']['request_id'],request)
        finally:release.set()
