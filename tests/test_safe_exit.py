import copy
import threading
from time import perf_counter,sleep
import tkinter as tk
import unittest
from unittest.mock import patch

from tests import test_sidebet_history as side_fixture
from tests import test_analysis_ui as main_fixture
from blackjack_lab.analysis.opening import build_opening_input
from blackjack_lab.analysis.opening_service import terminal_opening_result
from blackjack_lab.storage.opening_snapshots import algorithm_manifest


def pump_closed(app,seconds=8):
    end=perf_counter()+seconds
    while perf_counter()<end:
        try:
            if not app.winfo_exists():return
            app.update()
        except tk.TclError:
            if app.exit_flow.phase=='finished':return
            raise
        sleep(.005)
    raise AssertionError('application did not close')


class SafeExitTests(unittest.TestCase):
    setUp=side_fixture.SidebetUITests.setUp
    close=side_fixture.SidebetUITests.close
    start=side_fixture.SidebetUITests.start
    forecast=side_fixture.SidebetUITests.forecast
    pump=side_fixture.SidebetUITests.pump

    def test_slow_atomic_save_over_three_seconds_does_not_block_exit_painting(self):
        app=self.app;view=app.sidebets;store=view.store
        entered=threading.Event();release=threading.Event();original=store.save
        def slow(*a,**kw):entered.set();release.wait(8);return original(*a,**kw)
        beats=[];beat_id=[None]
        def beat():beats.append(perf_counter());beat_id[0]=app.after(20,beat)
        with patch.object(store,'save',side_effect=slow):
            self.start();self.assertTrue(entered.wait(1));beat()
            worker=view.worker
            try:
                before=app.ctrl.ledger.to_list();started=perf_counter();app.on_close()
                self.assertLess(perf_counter()-started,.15)
                app._key_rank('8');self.assertEqual(app.ctrl.ledger.to_list(),before)
                until=perf_counter()+3.15
                while perf_counter()<until:app.update();sleep(.005)
                self.assertGreaterEqual(len(beats),80)
                self.assertTrue(app.winfo_exists());self.assertTrue(app._closing)
            finally:
                if beat_id[0]:app.after_cancel(beat_id[0])
                release.set()
            pump_closed(app);self.app=None
        self.assertTrue(worker.stopped);self.assertFalse(app.exit_flow.thread.is_alive())
        saved,damaged=store.list();self.assertEqual(len(saved),1);self.assertFalse(damaged)
        store.verified_input(saved[0],self.db)

    def test_history_close_remains_responsive_during_slow_verification(self):
        self.start();self.forecast();self.view.show_history();h=self.view.history
        self.pump(lambda:h.verified is not None)
        entered=threading.Event();release=threading.Event();original=self.view.store.verified_input
        def slow(*args):entered.set();release.wait(8);return original(*args)
        try:
            with patch.object(self.view.store,'verified_input',side_effect=slow):
                h.select();self.assertTrue(entered.wait(1));start=perf_counter();h.close()
                self.assertLess(perf_counter()-start,.15)
                until=perf_counter()+3.1;updates=0
                while perf_counter()<until:self.app.update();updates+=1;sleep(.01)
                self.assertGreater(updates,100);self.assertTrue(h.winfo_exists())
                release.set();self.pump(lambda:not h.winfo_exists())
            self.assertTrue(h.worker.stopped)
        finally:release.set()

    def test_close_drains_explicit_history_recompute_and_save(self):
        self.start();self.forecast();self.view.show_history();h=self.view.history
        self.pump(lambda:h.verified is not None)
        original=h.selected();before=(self.view.store.directory/(original['snapshot_id']+'.json')).read_bytes()
        entered=threading.Event();release=threading.Event();verify=self.view.store.verified_input
        def held(*args):entered.set();release.wait(8);return verify(*args)
        app=self.app
        try:
            with patch.object(self.view.store,'verified_input',side_effect=held):
                h.recompute();self.assertTrue(entered.wait(1));app.on_close()
                self.assertFalse(h.worker.closed.is_set())
                release.set();pump_closed(app);self.app=None
            records,damaged=self.view.store.list();self.assertFalse(damaged)
            self.assertTrue(any(r['recomputed_from']==original['snapshot_id'] for r in records))
            self.assertEqual((self.view.store.directory/(original['snapshot_id']+'.json')).read_bytes(),before)
            self.assertTrue(h.worker.stopped)
        finally:release.set()

    def test_pending_sidebet_return_retry_and_explicit_discard_are_separate(self):
        app=self.app;view=app.sidebets;before=None
        with patch.object(view.store,'save',side_effect=OSError('controlled disk full')):
            self.start();value=self.forecast();before=copy.deepcopy(next(iter(view.pending_saves.values())))
            events=app.ctrl.ledger.to_list();app.on_close()
            self.pump(lambda:app.exit_flow.phase=='needs_save')
            self.assertGreater(app.exit_flow.pending()['sidebets'],0)
            app.exit_flow.return_to_app()
            self.assertFalse(app._closing);self.assertTrue(view.pending_saves)
            self.assertEqual(app.ctrl.ledger.to_list(),events)
        app.on_close();self.pump(lambda:app.exit_flow.phase=='needs_save')
        app.exit_flow.retry();pump_closed(app);self.app=None
        records,damaged=view.store.list();self.assertFalse(damaged)
        matching=[r for r in records if r['result']['request_id']==before['result']['request_id']]
        self.assertEqual(len(matching),1);self.assertEqual(matching[0]['event_prefix'],before['event_prefix'])

    def test_explicit_discard_does_not_claim_failed_results_saved(self):
        app=self.app;view=app.sidebets
        with patch.object(view.store,'save',side_effect=OSError('controlled disk full')):
            self.start();self.forecast();events=app.ctrl.ledger.to_list()
            app.on_close();self.pump(lambda:app.exit_flow.phase=='needs_save')
            with patch('blackjack_lab.ui.shutdown.messagebox.askyesno',return_value=False):app.exit_flow.discard()
            self.assertTrue(app.winfo_exists());self.assertTrue(view.pending_saves)
            with patch('blackjack_lab.ui.shutdown.messagebox.askyesno',return_value=True):app.exit_flow.discard()
            pump_closed(app);self.app=None
        self.assertTrue(view.pending_saves);self.assertFalse(list(view.store.directory.glob('*.json')))
        from blackjack_lab.ui.controller import SessionController
        c=SessionController.recover(self.db,events[0]['session_id'])
        try:self.assertEqual(c.ledger.to_list(),events)
        finally:c.close()

    def test_opening_pending_result_is_in_same_exit_decision(self):
        self.start();self.forecast();app=self.app;owner=app.opening_estimate
        snapshot=build_opening_input(app.ctrl.ledger,('玩家1',),'玩家1','forward')
        result=terminal_opening_result(snapshot,'a'*32,'cancelled','controlled fixture')
        attempt=dict(prefix=app.ctrl.ledger.to_list(),sources=algorithm_manifest())
        with patch.object(app.ctrl.opening_store,'save',side_effect=OSError('controlled disk full')):
            self.assertIsNone(owner.persist(result,attempt))
        app.on_close();self.pump(lambda:app.exit_flow.phase=='needs_save')
        self.assertEqual(app.exit_flow.pending()['opening'],1)
        app.exit_flow.retry();pump_closed(app);self.app=None
        records,damaged=app.ctrl.opening_store.list()
        self.assertFalse(damaged);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['event_prefix'],attempt['prefix'])


class MainResultExitTests(unittest.TestCase):
    setUp=main_fixture.TestAnalysisUI.setUp
    close=main_fixture.TestAnalysisUI.close
    start=main_fixture.TestAnalysisUI.start
    wait_result=main_fixture.TestAnalysisUI.wait_result

    def test_active_native_request_is_reaped_before_exit_finishes(self):
        self.start(cards=('8','8'),up='6');app=self.app
        app.analysis_panel.calculate_current();process=app.analysis_panel.service.active['process'];pid=process.pid
        start=perf_counter();app.on_close();self.assertLess(perf_counter()-start,.15)
        pump_closed(app);self.app=None
        from scripts.process_metrics import ProcessMetrics
        self.assertFalse(ProcessMetrics().running(pid))
        self.assertFalse(app.exit_flow.thread.is_alive())

    def test_failed_main_save_survives_current_result_invalidation(self):
        self.start(cards=('10','9'),up='6');app=self.app;panel=app.analysis_panel
        with patch.object(app.ctrl.analysis_store,'save',side_effect=OSError('controlled disk full')):
            panel.calculate_current();result=self.wait_result()
        self.assertIn(result['request_id'],app.pending_analysis)
        panel._invalidate_current();self.assertIsNone(panel.last_result)
        app.on_close()
        end=perf_counter()+5
        while perf_counter()<end and app.exit_flow.phase!='needs_save':app.update();sleep(.005)
        self.assertEqual(app.exit_flow.phase,'needs_save');self.assertEqual(app.exit_flow.pending()['main'],1)
        app.exit_flow.retry();pump_closed(app);self.app=None
        records,damaged=app.ctrl.analysis_store.list()
        self.assertFalse(damaged);self.assertEqual(len(records),1)
        self.assertEqual(records[0]['result']['request_id'],result['request_id'])
