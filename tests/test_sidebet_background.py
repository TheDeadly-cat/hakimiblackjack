from dataclasses import replace
import json
import threading
from time import perf_counter,sleep
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.sidebets.background import LatestWorker
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.analysis.dealer_blackjack import scalar
from tests import test_sidebet_history as fixture


class SidebetWorkerTests(unittest.TestCase):
    def test_each_channel_keeps_only_latest_waiting_request(self):
        worker=LatestWorker();self.addCleanup(worker.close)
        entered,release=threading.Event(),threading.Event()
        def first():entered.set();release.wait(3);return 'first'
        worker.submit('forecast',first)
        try:
            self.assertTrue(entered.wait(1))
            for n in range(100):worker.submit('observed',lambda n=n:n)
            self.assertEqual(len(worker.pending),1)
        finally:release.set()
        end=perf_counter()+3;values=[]
        while perf_counter()<end and len(values)<2:
            values.extend(worker.poll());sleep(.01)
        self.assertEqual([r['result'] for r in values],['first',99])
        worker.close();self.assertFalse(worker.thread.is_alive());self.assertFalse(worker.pending)

    def test_invalid_scalar_flags_do_not_become_peek_facts(self):
        with self.assertRaises(ValueError):scalar((1,)*10,True)
        with self.assertRaises(ValueError):scalar((1,)*10,1,'no')


class SidebetBackgroundUITests(unittest.TestCase):
    setUp=fixture.SidebetUITests.setUp
    close=fixture.SidebetUITests.close
    pump=fixture.SidebetUITests.pump
    start=fixture.SidebetUITests.start
    forecast=fixture.SidebetUITests.forecast

    def test_late_old_paytable_result_cannot_replace_latest_configuration(self):
        entered,release=threading.Event(),threading.Event()
        from blackjack_lab.ui import sidebet_view
        original=sidebet_view.execute;first=[True]
        def slow(*args):
            if first[0]:first[0]=False;entered.set();release.wait(4)
            return original(*args)
        with patch('blackjack_lab.ui.sidebet_view.execute',side_effect=slow):
            self.start()
            try:
                self.assertTrue(entered.wait(1))
                latest=replace(SidebetProfile(),perfect_pairs=(30,10,5))
                self.view.apply_profile(latest)
            finally:release.set()
            result=self.forecast()['result']
        self.assertEqual(result['input']['rules_digest'],latest.rules_digest)
        self.assertEqual(result['input']['profile']['perfect_pairs'],[30,10,5])

    def test_close_during_calculation_does_not_publish_or_save_late_result(self):
        entered,release=threading.Event(),threading.Event()
        from blackjack_lab.ui import sidebet_view
        original=sidebet_view.execute
        def slow(*args):entered.set();release.wait(3);return original(*args)
        with patch('blackjack_lab.ui.sidebet_view.execute',side_effect=slow):
            self.start();self.assertTrue(entered.wait(1))
            timer=threading.Timer(.05,release.set);timer.start()
            self.view.close();timer.join()
        self.assertTrue(self.view.closed)
        self.assertFalse(self.view.worker.thread.is_alive())
        self.assertFalse(list(self.view.store.directory.glob('*.json')))

    def test_damaged_record_remains_visible_after_successful_database_verification(self):
        self.start();self.forecast()
        bad=self.view.store.directory/('b'*32+'.json');bad.write_text('[]',encoding='utf-8')
        self.view.show_history();history=self.view.history
        self.pump(lambda:history.verified is not None)
        self.assertIn(bad.name,history.status.get())
        self.assertEqual(bad.read_text(encoding='utf-8'),'[]')

    def test_configuration_dialog_uses_separate_explicit_research_or_verified_status(self):
        self.start();self.forecast();before=self.app.ctrl.ledger.to_list()
        self.view.show_details();dialog=self.view.details
        dialog.status.set('真实桌规已核对');dialog.apply()
        self.assertIn('实际已核对',dialog.error.get())
        self.assertEqual(self.view.profile.confirmation,'research')
        dialog.status.set('赔付未核对');dialog.apply();self.forecast()
        self.assertEqual(self.view.profile.confirmation,'unconfirmed')
        self.assertIsNone(self.view.forecasts['玩家1']['result']['output']['bets']['perfect_pairs']['ev'])
        self.assertEqual(self.app.ctrl.ledger.to_list(),before)
        self.assertEqual(json.loads(self.view.settings.read_text(encoding='utf-8'))['profile']['confirmation'],'unconfirmed')
