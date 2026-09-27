import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from time import perf_counter,sleep
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import canonical,digest,InputUnavailable
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.analysis.sidebets.information import build_input,execute
from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
from blackjack_lab.storage.sidebet_snapshots import SidebetSnapshots
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.app import BlackjackLabApp
from tests import test_analysis_ui as fixture


class SidebetStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Path(self.temp.name)/'sidebets.db';self.ctrl=SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(ace_peek_das_research_rules(8));self.store=self.ctrl.sidebet_store

    def capture(self,profile=None):
        b=self.ctrl.ledger;result=execute(build_input(b,'玩家1',profile))
        return self.store.save(result,b.to_list(),'captured_predeal')

    def test_immutable_original_recompute_link_and_old_paytable(self):
        saved=self.capture();path=self.store.directory/(saved['snapshot_id']+'.json')
        before=path.read_bytes();original=self.store.verified_input(saved,self.db)
        recomputed=execute(original)
        later=self.store.save(recomputed,saved['event_prefix'],'historical_recompute',recomputed_from=saved['snapshot_id'])
        self.assertNotEqual(saved['snapshot_id'],later['snapshot_id'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(later['result']['output'],saved['result']['output'])
        changed=self.capture(replace(SidebetProfile(),perfect_pairs=(30,10,5)))
        self.assertNotEqual(changed['result']['output']['bets']['perfect_pairs']['ev'],saved['result']['output']['bets']['perfect_pairs']['ev'])
        self.assertEqual(self.store.load(saved['snapshot_id'])['result']['input']['profile']['perfect_pairs'],[25,12,6])

    def test_missing_or_different_database_never_certifies_prefix(self):
        saved=self.capture();missing=Path(self.temp.name)/'absent.db'
        with self.assertRaisesRegex(ValueError,'不存在'):self.store.verified_input(saved,missing)
        self.assertFalse(missing.exists())
        other=SessionController(Path(self.temp.name)/'other.db');self.addCleanup(other.close)
        with self.assertRaisesRegex(ValueError,'不符'):self.store.verified_input(saved,other.store.db_path)
        self.assertEqual(self.store.load(saved['snapshot_id']),saved)

    def test_tampered_numbers_even_resigned_are_rejected_and_preserved(self):
        saved=self.capture();path=self.store.directory/(saved['snapshot_id']+'.json')
        saved['result']['output']['bets']['perfect_pairs']['ev']=25
        saved['content_digest']=digest({k:v for k,v in saved.items() if k!='content_digest'})
        path.write_text(canonical(saved),encoding='utf-8');before=path.read_bytes()
        with self.assertRaises(ValueError):self.store.load(saved['snapshot_id'])
        entries,damaged=self.store.list();self.assertFalse(entries);self.assertEqual(len(damaged),1)
        self.assertEqual(path.read_bytes(),before)

    def test_closed_forecast_and_later_suit_correction_cannot_rewrite_prediction(self):
        saved=self.capture();c=self.ctrl;c.start_round(['玩家1'],simple_hole=True)
        first=c.deal_shown('玩家1','8');c.deal_shown('庄家','6',suit='D');c.deal_shown('玩家1','8',suit='H')
        with self.assertRaises(InputUnavailable):build_input(c.ledger,'玩家1')
        observed=execute(build_input(c.ledger,'玩家1',purpose='observed'))
        self.assertEqual(observed['output']['bets']['perfect_pairs']['status'],'unavailable')
        c.correct(first.event_id,{'suit':'S'},'actual suit observed')
        known=execute(build_input(c.ledger,'玩家1',purpose='observed'))
        self.assertEqual(known['output']['bets']['perfect_pairs']['category'],'mixed')
        self.assertNotIn('probabilities',known['output']['bets']['perfect_pairs'])
        self.store.save(known,c.ledger.to_list(),'observed',prediction_id=saved['snapshot_id'])
        self.assertEqual(self.store.load(saved['snapshot_id']),saved)
        self.assertEqual(self.store.verified_input(saved,self.db),saved['result']['input'])

    def test_bad_root_source_missing_input_and_io_failure_are_isolated(self):
        saved=self.capture();before=self.ctrl.ledger.to_list()
        for change in ('sources','prefix','input'):
            data=copy.deepcopy(saved)
            if change=='sources':data['algorithm_manifest']={}
            if change=='prefix':data['event_prefix']=[]
            if change=='input':data['result']['input']['seat']='庄家'
            from blackjack_lab.storage.sidebet_snapshots import validate
            with self.assertRaises(Exception):validate(data)
        path=self.store.directory/('a'*32+'.json');path.write_text('[]',encoding='utf-8')
        self.assertEqual(len(self.store.list()[1]),1)
        with patch('blackjack_lab.storage.sidebet_snapshots.atomic_write',side_effect=OSError('disk fault')):
            with self.assertRaises(OSError):self.capture()
        self.assertEqual(self.ctrl.ledger.to_list(),before)
        self.assertEqual(self.store.load(saved['snapshot_id']),saved)

    def test_later_suit_information_reconstructs_old_inventory_only_as_separate_research(self):
        c=self.ctrl;c.start_round(['玩家1'])
        first=c.deal_shown('玩家1','8');c.deal_shown('庄家','10',suit='H')
        c.deal_shown('玩家1','6',suit='S');hole=c.deal_hidden('庄家')
        hand=c.state().current.table.players['玩家1'].hands[0].hand_id
        c.player_action('玩家1',hand,'停牌');c.reveal(hole.event_id,'7',suit='D');c.end_round()
        saved=self.capture();self.assertEqual(saved['result']['status'],'unavailable')
        original_path=self.store.directory/(saved['snapshot_id']+'.json');original_bytes=original_path.read_bytes()
        c.start_round(['玩家1']);later=c.deal_shown('玩家1','A',suit='S')
        c.correct(first.event_id,{'suit':'C'},'later actual suit verification')
        from blackjack_lab.analysis.sidebets.research import origin_of,build_corrected_input
        live=self.store.current_ledger_for(saved,self.db)
        research=build_corrected_input(live,origin_of(saved['result']['input']),SidebetProfile())
        self.assertEqual(research['stage'],'hindsight_research')
        self.assertEqual(research['composition']['status'],'available')
        self.assertEqual(sum(research['composition']['counts']),412)
        self.assertEqual(research['composition']['counts'][0],8)  # The later AS is returned to the old inventory.
        self.assertEqual(research['excluded_later_card_ids'],[later.event_id])
        result=execute(research)
        new=self.store.save(result,live.to_list(),'historical_recompute',recomputed_from=saved['snapshot_id'])
        self.assertEqual(self.store.load(new['snapshot_id']),new)
        self.assertEqual(original_path.read_bytes(),original_bytes)
        with self.assertRaisesRegex(ValueError,'冒充'):
            self.store.save(result,live.to_list(),'captured_predeal')
        self.assertEqual(self.store.load(saved['snapshot_id'])['result']['status'],'unavailable')


class SidebetUITests(unittest.TestCase):
    setUp=fixture.TestAnalysisUI.setUp
    close=fixture.TestAnalysisUI.close

    def pump(self,predicate,timeout=8):
        end=perf_counter()+timeout
        while perf_counter()<end:
            self.app.update()
            if predicate():return
            sleep(.01)
        self.fail('sidebet UI wait expired: '+str({k:v.get() for k,v in self.app.sidebets.lines.items()}))

    def start(self):
        app=self.app;app.act_common_settings();app.act_new_shoe();app.act_new_round()
        self.view=app.sidebets;self.view.enabled.set(True);self.view.refresh()

    def forecast(self):
        self.pump(lambda:'玩家1' in self.view.forecasts)
        return self.view.forecasts['玩家1']

    def test_forecast_freeze_actual_outcome_and_viewing_do_not_write_cards(self):
        self.start();forecast=self.forecast();before=self.app.ctrl.ledger.to_list()
        self.view.show_details();self.app.update()
        self.assertEqual(self.view.details.text.cget('state'),'disabled')
        self.assertEqual(self.app.ctrl.ledger.to_list(),before)
        for rank,suit in (('8','S'),('6','D'),('8','H')):
            self.app.var_suit.set(suit);self.app._key_rank(rank)
        self.pump(lambda:self.view.observed is not None)
        self.assertIn('已揭晓 混色对子',self.view.lines['perfect_pairs'].get())
        self.assertIn('已封盘',self.view.lines['perfect_pairs'].get())
        self.assertEqual(self.view.forecasts['玩家1'],forecast)
        self.view.apply_profile(replace(SidebetProfile(),perfect_pairs=(30,10,5)))
        self.pump(lambda:self.view.observed is not None)
        self.assertEqual(self.view.observed['result']['input']['profile']['perfect_pairs'],[25,12,6])
        self.assertEqual(self.errors,[])

    def test_save_failure_retry_uses_original_input_after_first_card(self):
        self.start()
        with patch.object(self.view.store,'save',side_effect=OSError('explicit save fault')):
            value=self.forecast();self.assertIsNone(value['saved_id'])
        self.assertIn('未保存',self.view.pending_label.get())
        original=copy.deepcopy(value['result']);self.app._key_rank('8')
        self.view.retry_saves()
        self.pump(lambda:not self.view.pending_saves)
        saved=self.view.store.load(self.view.saved_ids[original['request_id']])
        self.assertEqual(saved['result'],original)
        self.assertLess(saved['result']['input']['through_seq'],self.app.ctrl.ledger.events[-1].seq)
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(),415)

    def test_late_predeal_completion_is_only_frozen_after_card(self):
        entered,release=threading.Event(),threading.Event()
        from blackjack_lab.ui import sidebet_view
        original=sidebet_view.execute
        def slow(*args):
            entered.set();release.wait(4);return original(*args)
        with patch('blackjack_lab.ui.sidebet_view.execute',side_effect=slow):
            self.start()
            try:
                self.assertTrue(entered.wait(1))
                self.app._key_rank('8')
                self.assertTrue(self.view.sealed)
            finally:release.set()
            self.forecast()
        self.assertIn('已封盘',self.view.lines['perfect_pairs'].get())
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(),415)

    def test_history_readonly_recompute_separate_and_restart_not_current(self):
        self.start();value=self.forecast();sid=value['saved_id']
        path=self.view.store.directory/(sid+'.json');original=path.read_bytes()
        before=tuple(v.get() for v in self.view.lines.values());events=self.app.ctrl.ledger.to_list()
        self.view.show_history();history=self.view.history
        self.pump(lambda:history.verified==sid)
        self.assertEqual(history.text.cget('state'),'disabled')
        history.recompute();self.pump(lambda:not history.recomputing and not history.loading and history.verified is not None)
        selected=history.selected();self.assertNotEqual(selected['snapshot_id'],sid)
        self.assertEqual(selected['recomputed_from'],sid)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(tuple(v.get() for v in self.view.lines.values()),before)
        self.assertEqual(self.app.ctrl.ledger.to_list(),events)
        self.close();self.app=BlackjackLabApp(self.db,auto_analysis=False)
        self.assertFalse(self.app.sidebets.forecasts)
        self.assertEqual(self.app.ctrl.ledger.to_list(),events)
        self.app.sidebets.show_history();history=self.app.sidebets.history
        self.pump(lambda:not history.loading and history.verified is not None)
        self.assertTrue(any(r['snapshot_id']==sid for r in history.entries))

    def test_unknown_payout_and_target_switch_never_publish_old_current(self):
        self.start();self.forecast()
        self.view.apply_profile(replace(SidebetProfile(),confirmation='unconfirmed'))
        self.forecast()
        self.assertIn('赔付未核对',self.view.lines['perfect_pairs'].get())
        self.app.var_analysis_target.set('玩家2');self.app.update()
        self.assertNotIn('命中 7.470%',self.view.lines['perfect_pairs'].get())
        self.pump(lambda:bool(self.view.problem))
        self.assertIn('未参与',self.view.problem)

    def test_unknown_suits_only_affect_sidebets_and_settings_focus_is_not_card_input(self):
        self.start();self.forecast()
        for rank in ('8','6','8'):self.app._key_rank(rank)
        self.pump(lambda:self.view.observed is not None)
        self.assertEqual(self.view.observed['result']['status'],'unavailable')
        self.assertIsNotNone(self.app.ctrl.current_decision_input('玩家1'))
        self.assertIn('BJ 0.00%',self.app.compact_panel.identity.get())
        self.view.show_details();self.app.update()
        before=len(self.app.ctrl.ledger.events)
        self.view.details.text.focus_force();self.app.update()
        self.view.details.text.event_generate('<KeyPress>',keysym='8');self.app.update()
        self.assertEqual(len(self.app.ctrl.ledger.events),before)
        self.assertEqual(self.errors,[])
