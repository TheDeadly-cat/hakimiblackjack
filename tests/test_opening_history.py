import copy
import hashlib
import json
from pathlib import Path
import tempfile
import threading
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.opening import build_opening_input, SCHEMA
from blackjack_lab.analysis.contracts import canonical
from blackjack_lab.analysis.opening_service import terminal_opening_result, validate_opening_result, OpeningService
from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.storage.opening_snapshots import OpeningSnapshots
from blackjack_lab.ui.controller import SessionController
from tests.test_opening_ev import synthetic_result
from tests import test_opening_ui as ui_fixture


class TestOpeningStorage(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.db = self.root/'isolated.db'
        self.ctrl = SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(both_initial_das_rules())
        self.snapshot = build_opening_input(self.ctrl.ledger)
        self.result = synthetic_result(self.snapshot)
        self.prefix = self.ctrl.ledger.to_list()
        self.store = self.ctrl.opening_store

    def save(self, result=None, **kwargs):
        return self.store.save(self.result if result is None else result,self.prefix,**kwargs)

    def test_full_record_readonly_and_recompute_creates_another_file(self):
        first = self.save()
        path = self.store.directory/(first['snapshot_id']+'.json')
        original = path.read_bytes()
        self.result['ev'] = 123  # Mutation of the caller never changes the saved record.
        loaded = self.store.load(first['snapshot_id'])
        self.assertEqual(loaded['result']['ev'],1)
        self.assertEqual(loaded['event_prefix'],self.prefix)
        derived = self.store.verified_input(loaded,self.db)
        self.assertEqual(derived.input_digest,self.snapshot.input_digest)
        second = self.save(synthetic_result(derived),recomputed_from=first['snapshot_id'])
        self.assertNotEqual(first['snapshot_id'],second['snapshot_id'])
        self.assertEqual(second['recomputed_from'],first['snapshot_id'])
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len(self.store.list()[0]),2)

    def test_deleted_or_unrelated_database_never_becomes_verified_or_created(self):
        saved = self.save()
        missing = self.root/'absent.db'
        with self.assertRaisesRegex(ValueError,'不存在'):
            self.store.verified_input(saved,missing)
        self.assertFalse(missing.exists())
        other = SessionController(self.root/'different.db')
        try:
            with self.assertRaisesRegex(ValueError,'不匹配'):
                self.store.verified_input(saved,other.store.db_path)
        finally:
            other.close()
        self.ctrl.close()
        self.db.unlink()  # Owned test database only, after closing it.
        self.assertEqual(self.store.load(saved['snapshot_id'])['result']['ev'],1)
        with self.assertRaisesRegex(ValueError,'不存在'):
            self.store.verified_input(saved,self.db)
        self.assertFalse(self.db.exists())

    def test_historical_source_is_retained_but_cannot_publish_as_current(self):
        saved = self.save()
        with patch('blackjack_lab.analysis.opening_service.source_digest', return_value='f'*64):
            with self.assertRaises(ValueError):
                validate_opening_result(saved['result'],self.snapshot)
            self.assertEqual(canonical(self.store.load(saved['snapshot_id'])), canonical(saved))

    def test_cancel_timeout_and_failed_have_no_partial_samples(self):
        for status in ('cancelled','timeout','failed','stale','unsupported'):
            result = terminal_opening_result(self.snapshot,'test-'+status,status,'独立状态测试')
            saved = self.save(result)
            self.assertEqual(self.store.load(saved['snapshot_id'])['result']['status'],status)
            for field in ('histogram','samples','ev','interval'):
                bad = {**result, field: [] if field!='ev' else 0}
                with self.assertRaises(ValueError):
                    self.save(bad)
        self.assertEqual(len(self.store.list()[0]),5)

    def test_corrupt_record_is_reported_and_kept(self):
        saved = self.save()
        path = self.store.directory/(saved['snapshot_id']+'.json')
        bad = copy.deepcopy(saved); bad['result']['histogram'][0] += 1
        path.write_text(json.dumps(bad),encoding='utf-8')
        contents = path.read_bytes()
        entries, damaged = self.store.list()
        self.assertEqual(entries,[])
        self.assertEqual(len(damaged),1)
        self.assertEqual(path.read_bytes(),contents)

    def test_wrong_prefix_and_incomplete_results_never_save(self):
        before = self.ctrl.ledger.to_list()
        with self.assertRaises(ValueError):
            self.store.save(self.result,self.prefix[:-1])
        for field, value in [('ev',float('nan')),('samples',200),('interval',[0,1]),('native_binary_digest','unknown')]:
            bad = {**self.result,field:value}
            with self.assertRaises(ValueError):
                self.save(bad)
        self.assertEqual(self.ctrl.ledger.to_list(),before)
        self.assertEqual(self.store.list(),([],[]))

    def test_atomic_write_failure_keeps_prior_file_and_database(self):
        saved = self.save()
        path = self.store.directory/(saved['snapshot_id']+'.json')
        before = path.read_bytes(),self.ctrl.ledger.to_list()
        with patch('blackjack_lab.storage.safe_files.os.link',side_effect=OSError('disk publication fault')):
            with self.assertRaises(OSError):
                self.save()
        self.assertEqual((path.read_bytes(),self.ctrl.ledger.to_list()),before)
        self.assertEqual(len(self.store.list()[0]),1)

    def test_service_wall_timeout_and_cancel_use_opening_schema(self):
        service = OpeningService()
        try:
            service.start(self.snapshot)
            service.active['start'] -= 21
            result = service.poll()
            self.assertEqual((result['schema'],result['status']),(SCHEMA,'timeout'))
            self.save(result)
            service.start(self.snapshot)
            service.cancel()
            self.assertEqual((service.result['schema'],service.result['status']),(SCHEMA,'cancelled'))
            self.save(service.result)
        finally:
            service.close()


class TestOpeningHistoryUI(unittest.TestCase):
    setUp = ui_fixture.TestOpeningTitle.setUp
    close = ui_fixture.TestOpeningTitle.close
    tick = ui_fixture.TestOpeningTitle.tick
    publish = ui_fixture.TestOpeningTitle.publish

    def wait_history(self):
        deadline = perf_counter()+4
        while self.view.history._loading and perf_counter()<deadline:
            self.app.update(); sleep(.01)
        self.assertFalse(self.view.history._loading)

    def test_save_failure_keeps_result_recording_and_retry_original_prefix(self):
        with patch('blackjack_lab.storage.opening_snapshots.atomic_write',side_effect=OSError('disk fault')):
            self.publish(1)
        original = copy.deepcopy(self.view.result)
        self.assertIn('已计算、未保存',self.app.var_opening_ev.get())
        self.assertEqual(len(self.view.pending_saves),1)
        self.app.act_new_round()
        before = len(self.app.ctrl.ledger.events)
        self.app._key_rank('8')
        self.assertEqual(len(self.app.ctrl.ledger.events),before+1)
        self.assertIsNone(self.view.result)
        events = self.app.ctrl.ledger.to_list()
        self.view.retry_saves()
        entries,damaged = self.app.ctrl.opening_store.list()
        self.assertFalse(damaged)
        self.assertEqual(len(entries),1)
        self.assertEqual(entries[0]['result']['input_digest'],original['input_digest'])
        self.assertEqual(self.view.pending_saves,[])
        self.assertEqual(self.app.ctrl.ledger.to_list(),events)
        self.assertNotIn('+1.0000',self.app.var_opening_ev.get())

    def test_history_recompute_is_separate_readonly_and_linked(self):
        self.publish(1)
        original = self.view.saved
        path = self.app.ctrl.opening_store.directory/(original['snapshot_id']+'.json')
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch('blackjack_lab.ui.opening_history.OpeningService',ui_fixture.FakeService):
            self.view.show_history()
        self.wait_history()
        history = self.view.history
        self.assertEqual(history.display.cget('state'),'disabled')
        self.assertIn('原数据库事件前缀已核对',history.display.get('1.0','end'))
        current_text = self.app.var_opening_ev.get()
        history.recompute()
        result = synthetic_result(history.attempt['snapshot'],net=-1)
        result['request_id'] = history.attempt['request_id']
        history.service.pending = result
        history.after_cancel(history.poll_id)
        history.poll()
        entries, damaged = self.app.ctrl.opening_store.list()
        self.assertFalse(damaged)
        self.assertEqual(len(entries),2)
        self.assertEqual(entries[-1]['recomputed_from'],original['snapshot_id'])
        self.assertEqual(self.app.var_opening_ev.get(),current_text)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),file_hash)

    def test_history_verification_does_not_block_recording_and_close_cancels_reader(self):
        self.publish(1)
        entered, release = threading.Event(), threading.Event()
        original = self.app.ctrl.opening_store.list
        def slow(**kwargs):
            entered.set()
            release.wait(4)
            return original(**kwargs)
        with patch.object(self.app.ctrl.opening_store,'list',side_effect=slow):
            started=perf_counter()
            self.view.show_history()
            self.assertLess(perf_counter()-started,1)
            history=self.view.history
            try:
                self.assertTrue(entered.wait(1))
                self.assertTrue(history._loading)
                self.app.act_new_round()
                before=len(self.app.ctrl.ledger.events)
                self.app._key_rank('8')
                self.assertEqual(len(self.app.ctrl.ledger.events),before+1)
                self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(),415)
                history.close()
                self.assertTrue(history._load_cancel.is_set())
            finally:
                release.set()
                history._load_thread.join(timeout=2)
            self.assertFalse(history._load_thread.is_alive())
            self.assertFalse(history.winfo_exists())

    def test_recovery_exposes_history_without_restoring_it_as_current(self):
        self.publish(1)
        saved_id = self.view.saved['snapshot_id']
        self.close()
        from blackjack_lab.ui.app import BlackjackLabApp
        self.app = BlackjackLabApp(self.db,auto_analysis=False)
        self.assertIsNone(self.app.opening_estimate.result)
        entries, damaged = self.app.ctrl.opening_store.list()
        self.assertFalse(damaged)
        self.assertEqual(entries[0]['snapshot_id'],saved_id)
        self.assertNotIn('+1.0000',self.app.var_opening_ev.get())


if __name__=='__main__':
    unittest.main()
