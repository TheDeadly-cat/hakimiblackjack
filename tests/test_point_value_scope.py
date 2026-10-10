"""Point-value product scope on real Tk and the normal spawned recorder."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from time import perf_counter,sleep
import unittest
from unittest.mock import patch

from blackjack_lab import main
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.core.table import ACTION_SPLIT,ACTION_STAND,ACTION_DOUBLE
from blackjack_lab.ledger.events import CARD_DEALT,CARD_REVEALED
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from blackjack_lab.ui.table_modes import default_settings
from scripts.tk_lifecycle import close_app


class PointValueScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'point.db';self.app=None;self.errors=[]
        for name,effect in [('askyesno',lambda *a,**k:True),('showinfo',lambda *a,**k:None),
                            ('showerror',lambda *a,**k:self.errors.append(a))]:
            context=patch('blackjack_lab.ui.app.messagebox.'+name,side_effect=effect)
            context.start();self.addCleanup(context.stop)
        self.addCleanup(self.close)

    def create(self,research=False):
        self.app=BlackjackLabApp(self.db,background_recording=True,recording_process=True,
                                 sidebet_research=research)
        self.app.update();return self.app

    def close(self):
        if self.app:
            self.pump(lambda:not self.app.recording_busy)
            if self.app._recording_faults:
                if self.app._recording_owner:self.pump(lambda:self.app._recording_owner.stopped)
                self.app.act_reconcile_recording()
            close_app(self.app,discard_fixture_results=True);self.app=None

    def pump(self,predicate,timeout=8):
        end=perf_counter()+timeout
        while perf_counter()<end:
            self.app.update()
            if predicate():return
            sleep(.005)
        self.fail('point-value state wait expired')

    def saved(self):
        self.pump(lambda:not self.app.recording_busy)
        self.assertFalse(self.app._recording_faults,self.app.recording_fault_message())

    def start(self,mode='pragmatic',players=1,same_rank=False):
        app=self.app;app.select_table_mode(mode)
        rules=default_settings(mode).rules
        # Declared truth of this owned synthetic shoe, never a live-table guess.
        rules.start_from_new_shoe=True;rules.burn_cards_known=True;rules.initial_burn_count=0
        if same_rank:rules.split_match='same_rank'
        app._set_rule_form(rules);app.var_auto_next.set(False)
        for seat,var in app.var_participants.items():var.set(int(seat[-1])<=players)
        app.act_new_shoe();app.act_new_round();app.update()

    def card(self,rank):
        next(b for b in self.app.compact_panel.card_buttons if b.cget('text')==rank).invoke()

    def test_normal_cli_keeps_main_auto_and_does_not_enable_card_identity_sidebets(self):
        observed={}
        def factory(*args,**kwargs):
            app=BlackjackLabApp(*args,**kwargs);self.app=app
            def inspect():
                observed.update(auto=app.analysis_panel.auto.get(),background=app.background_recording,
                    process=app.recording_process,side=app.sidebets.enabled.get(),
                    poll=app.sidebets.poll_id,worker=app.sidebets.worker.thread)
                app.on_close()
            app.after(30,inspect);return app
        with patch('blackjack_lab.ui.app.BlackjackLabApp',side_effect=factory), \
             patch('sys.argv',['blackjack_lab.main','--db',str(self.db),'--table-mode','bclc']):
            self.assertEqual(main.main(),0)
        self.app=None
        self.assertTrue(observed['auto']);self.assertTrue(observed['background']);self.assertTrue(observed['process'])
        self.assertFalse(observed['side']);self.assertIsNone(observed['poll']);self.assertIsNone(observed['worker'])

    def test_old_enabled_true_profiles_cannot_restore_live_work_and_bytes_are_preserved(self):
        paths=[]
        for suffix in ('.sidebet-profile.json','.bclc-sidebet-profile.json'):
            path=Path(str(self.db)+suffix)
            raw=json.dumps(dict(schema=1,profile=SidebetProfile().to_dict(),enabled=True),indent=3).encode()
            path.write_bytes(raw);paths.append((path,raw))
        app=self.create();view=app.sidebets
        with patch.object(view.worker,'submit',wraps=view.worker.submit) as submit:
            for mode in ('bclc','pragmatic'):
                self.start(mode);view.enabled.set(True);view.refresh()
                self.assertFalse(view.enabled.get());self.assertFalse(view.live_enabled())
                self.assertIsNone(view.poll_id)
            submit.assert_not_called()
        for path,raw in paths:self.assertEqual(path.read_bytes(),raw)
        self.close();self.create()
        self.assertFalse(self.app.sidebets.enabled.get())
        for path,raw in paths:self.assertEqual(path.read_bytes(),raw)

    def test_disabled_unchanged_refresh_and_manual_poll_do_not_capture_or_schedule(self):
        app=self.create();view=app.sidebets;view.refresh()
        before=app.ctrl.ledger.to_list()
        with patch.object(PrefixSnapshot,'capture',wraps=PrefixSnapshot.capture) as capture:
            for _ in range(100):view.refresh();view.poll()
            capture.assert_not_called()
        self.assertIsNone(view.poll_id);self.assertIsNone(view.worker.thread)
        self.assertEqual(app.ctrl.ledger.to_list(),before)

    def test_research_idle_refresh_is_light_but_mode_changes_still_verify(self):
        app=self.create(research=True);view=app.sidebets;view.refresh()
        with patch.object(PrefixSnapshot,'capture',wraps=PrefixSnapshot.capture) as capture:
            for _ in range(100):view.refresh();view.poll()
            capture.assert_not_called()
            app.select_table_mode('bclc')
            self.assertGreater(capture.call_count,0)
        self.assertEqual(view._profile_mode,'bclc');self.assertIsNone(view.profile.a23)
        self.assertIsNone(view.profile.qka);self.assertIsNone(view.profile.ka2)
        self.assertIsNone(view.worker.thread)

    def test_real_point_input_auto_main_result_has_no_sidebet_requests_or_live_snapshots(self):
        app=self.create();view=app.sidebets
        with patch.object(view.worker,'submit',wraps=view.worker.submit) as submit:
            for mode in ('pragmatic','bclc'):
                self.start(mode)
                for rank in ('T','6','8'):self.card(rank)
                if mode=='bclc':app.compact_panel.hole_button.invoke()
                self.saved()
                self.pump(lambda:app.analysis_panel.last_result is not None and
                    app.analysis_panel.last_result['status']=='available')
                result=app.analysis_panel.last_result
                self.assertEqual(result['input_digest'],app.ctrl.current_decision_input('玩家1').input_digest)
                self.assertTrue(app.analysis_panel.auto.get())
                self.assertEqual(app.ctrl.state().current.table.players['玩家1'].hands[0].ranks,['T','8'])
                self.assertEqual(app.ctrl.state().current.shoe.t_bucket_out,1)
                self.assertFalse(view.enabled.get());self.assertIsNone(view.poll_id)
            submit.assert_not_called()
        self.assertEqual(list(view.store.directory.glob('*.json')),[])

    def test_insurance_and_dealer_bj_remain_available_under_point_scope(self):
        app=self.create();self.start('bclc')
        for rank in ('9','A','8'):self.card(rank)
        app.compact_panel.hole_button.invoke();self.saved()
        self.assertIn('研究EV',app.compact_panel.flow_message.get())
        self.assertIn('%',app.compact_panel.identity.get())
        self.assertFalse(app.sidebets.live_enabled())
        self.card('T');self.saved()
        seg=app.ctrl.state().current
        self.assertEqual(seg.table.dealer.hands[0].ranks,['A','T'])
        self.assertEqual(seg.shoe.physical_remaining(),412)
        self.assertFalse(any(e.etype=='PEEK_NEGATIVE' for e in app.ctrl.ledger.events))
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype==CARD_REVEALED]),1)
        self.assertNotIn('研究EV',app.compact_panel.flow_message.get())

    def test_point_same_value_split_and_no_das_keep_rule_boundaries(self):
        app=self.create();self.start('bclc')
        for rank in ('T','6','T'):self.card(rank)
        app.compact_panel.hole_button.invoke();self.saved()
        app.act_action(ACTION_SPLIT);self.card('2');self.card('3');self.saved()
        seg=app.ctrl.state().current;hands=seg.table.players['玩家1'].hands
        self.assertEqual([h.ranks for h in hands],[['T','2'],['T','3']])
        self.assertFalse(seg.table.action_states('玩家1',hands[0].hand_id)[ACTION_DOUBLE].allowed)
        self.assertFalse(seg.table.action_states('玩家1',hands[0].hand_id)[ACTION_SPLIT].allowed)
        self.assertTrue(app.analysis_panel.auto.get());self.assertFalse(app.sidebets.live_enabled())
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(),app.ctrl.ledger.to_list())

    def test_all_a_to_nine_and_t_buttons_follow_both_seven_seat_orders(self):
        app=self.create()
        for mode in ('pragmatic','bclc'):
            self.start(mode,players=7)
            ranks=('A','2','3','4','5','6','7','8','9','T','2','3','4','5','6')
            for rank in ranks:self.card(rank)
            if mode=='bclc':app.compact_panel.hole_button.invoke()
            self.saved()
            seg=app.ctrl.state().current
            events=[e for e in app.ctrl.ledger.events if e.etype==CARD_DEALT and e.round_id==seg.round_id]
            seats=['玩家'+str(i) for i in (range(7,0,-1) if mode=='bclc' else range(1,8))]
            self.assertEqual([e.payload['seat'] for e in events],seats+['庄家']+seats+['庄家'])
            self.assertEqual([e.payload['rank'] for e in events[:-1]],list(ranks))
            self.assertTrue(all(e.payload['suit'] is None for e in events))
            self.assertEqual(seg.shoe.t_bucket_out,1);self.assertEqual(seg.shoe.physical_remaining(),400)
            self.assertFalse(app.sidebets.live_enabled());self.assertTrue(app.analysis_panel.auto.get())

    def test_t_cannot_assert_same_specific_rank_and_rejected_split_does_not_commit(self):
        app=self.create();self.start(same_rank=True)
        for rank in ('T','6','T'):self.card(rank)
        self.saved();seg=app.ctrl.state().current;hand=seg.table.players['玩家1'].hands[0]
        state=seg.table.action_states('玩家1',hand.hand_id)[ACTION_SPLIT]
        self.assertFalse(state.allowed);self.assertEqual(state.reason_code,'PAIR_UNKNOWN')
        before=app.ctrl.ledger.to_list();app.act_action(ACTION_SPLIT)
        self.pump(lambda:not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual(app._recording_faults[0]['status'],'failed_before_commit')
        self.assertEqual(app.ctrl.ledger.to_list(),before)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(),before)

    def test_disable_research_retains_unsaved_original_and_explicit_retry(self):
        app=self.create(research=True);view=app.sidebets
        with patch.object(view.store,'save',side_effect=OSError('owned controlled snapshot fault')):
            self.start()
            self.pump(lambda:'玩家1' in view.forecasts)
            request=view.forecasts['玩家1']['result']['request_id']
            frozen=copy.deepcopy(view.pending_saves[request])
            view.enabled.set(False);view.change_enabled()
            with patch.object(view.worker,'submit',wraps=view.worker.submit) as submit:
                self.card('8');self.saved();view.refresh()
                submit.assert_not_called()
            self.assertEqual(view.pending_saves[request],frozen)
            self.assertIsNone(view.poll_id)
        view.retry_saves();self.pump(lambda:request not in view.pending_saves and view.poll_id is None)
        saved=view.store.load(view.saved_ids[request])
        self.assertEqual(saved['result'],frozen['result']);self.assertEqual(saved['event_prefix'],frozen['event_prefix'])

    def test_actual_t_capacity_keeps_t_unknown_and_rejects_the_next_t(self):
        app=self.create();self.start();app.var_auto_next.set(True)
        for index in range(32):
            self.card('T');self.card('T');self.card('T')
            app.act_action(ACTION_STAND);self.card('T');self.saved()
            self.assertEqual(app.ctrl.state().current.shoe.t_bucket_out,4*(index+1))
        shoe=app.ctrl.state().current.shoe
        self.assertEqual(shoe.physical_remaining(),288)
        self.assertEqual({r:shoe.remaining[r] for r in ('10','J','Q','K')},dict.fromkeys(('10','J','Q','K'),32))
        self.assertEqual({r:shoe.exact_out[r] for r in ('10','J','Q','K')},dict.fromkeys(('10','J','Q','K'),0))
        self.assertTrue(app.analysis_panel.auto.get())
        before=app.ctrl.ledger.to_list()
        app._key_rank('T')
        self.pump(lambda:not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual(app._recording_faults[0]['status'],'failed_before_commit')
        self.assertEqual(app.ctrl.ledger.to_list(),before)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(),before)
        cards=[e for e in app.ctrl.ledger.events if e.etype==CARD_DEALT and e.payload['rank']=='T']
        self.assertEqual(len(cards),96)
        self.assertTrue(all(e.payload['suit'] is None for e in cards))

    def test_withdraw_correction_and_restart_preserve_precise_legacy_prefix(self):
        from blackjack_lab.ledger.ledger import EventLedger
        app=self.create();rules=default_settings('pragmatic').rules
        rules.start_from_new_shoe=rules.burn_cards_known=True;rules.initial_burn_count=0
        b=EventLedger('owned-precise-history');b.start_session('Owned synthetic precise history')
        b.create_shoe(rules);b.start_round(['玩家1','玩家2'])
        for seat,rank,suit in [('玩家1','10','S'),('玩家2','J','H'),('庄家','6','C'),
                               ('玩家1','Q','D'),('玩家2','K','S')]:b.deal(seat,rank,suit=suit)
        hole=b.deal('庄家',hidden=True)
        for seat in ('玩家1','玩家2'):
            b.player_action(seat,b.replay().current.table.players[seat].hands[0].hand_id,ACTION_STAND)
        b.reveal(hole.event_id,'T');b.deal('庄家','2',suit='C')
        b.end_round(settle=True,observation_status='complete')
        original=b.to_list();app.ctrl.store.save_ledger(b);app.ctrl.load_session(b.session_id)
        self.close();app=self.create()
        self.assertEqual(app.ctrl.ledger.to_list(),original)
        self.start();self.card('T');self.saved()
        event=next(e for e in reversed(app.ctrl.ledger.events) if e.etype==CARD_DEALT)
        self.assertEqual((event.payload['rank'],event.payload['suit']),('T',None))
        app.act_undo();self.saved();self.card('T');self.saved()
        event=next(e for e in reversed(app.ctrl.ledger.events) if e.etype==CARD_DEALT)
        app.ctrl.correct(event.event_id,{'rank':'J','suit':'H'},'Owned explicit correction')
        app.refresh_all()
        self.assertEqual(event.payload['rank'],'T');self.assertIsNone(event.payload['suit'])
        self.assertEqual(app.ctrl.state().current.shoe.t_bucket_out,0)
        self.assertEqual(app.ctrl.state().current.shoe.exact_out['J'],1)
        self.assertEqual(app.ctrl.ledger.to_list()[:len(original)],original)
        saved=app.ctrl.ledger.to_list();self.close();app=self.create()
        self.assertEqual(app.ctrl.ledger.to_list(),saved)
        self.assertFalse(app.sidebets.live_enabled());self.assertTrue(app.analysis_panel.auto.get())

    def test_history_and_original_configuration_remain_read_only_in_point_entry(self):
        app=self.create(research=True);self.start();view=app.sidebets
        self.pump(lambda:'玩家1' in view.forecasts)
        record=view.forecasts['玩家1'];snapshot=view.store.directory/(record['saved_id']+'.json')
        raw=snapshot.read_bytes();view.apply_profile(view.profile)
        config=view.settings;config_raw=config.read_bytes();events=app.ctrl.ledger.to_list()
        self.close();app=self.create();view=app.sidebets
        view.show_history();history=view.history
        self.pump(lambda:not history.loading and history.verified is not None)
        self.assertTrue(history.recompute_button.instate(['disabled']))
        self.assertTrue(history.corrected_button.instate(['disabled']))
        with patch.object(history.worker,'submit',wraps=history.worker.submit) as submit:
            history.recompute();history.corrected();submit.assert_not_called()
        view.show_details();view.details.apply()
        self.assertTrue(view.details.save_button.instate(['disabled']))
        self.assertEqual(snapshot.read_bytes(),raw);self.assertEqual(config.read_bytes(),config_raw)
        self.assertEqual(app.ctrl.ledger.to_list(),events)


if __name__=='__main__':unittest.main()
