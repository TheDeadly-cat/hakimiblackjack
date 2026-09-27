import threading
import unittest
from unittest.mock import patch

from tests import test_sidebet_history as fixture


class HistoryLifecycleTests(unittest.TestCase):
    setUp=fixture.SidebetUITests.setUp
    close=fixture.SidebetUITests.close
    start=fixture.SidebetUITests.start
    forecast=fixture.SidebetUITests.forecast
    pump=fixture.SidebetUITests.pump

    def ready(self):
        self.start();self.forecast();self.view.show_history()
        h=self.view.history
        self.pump(lambda:h.verified is not None and not h.loading)
        return h

    def test_queued_reload_survives_real_tk_selection_while_worker_busy(self):
        h=self.ready();entered=threading.Event();release=threading.Event()
        def held():entered.set();release.wait(5);return {'kind':'controlled-old-task'}
        h.worker.submit('history',held,'prior-active')
        try:
            self.assertTrue(entered.wait(1))
            with patch.object(self.view.store,'list',wraps=self.view.store.list) as loads:
                for _ in range(3):
                    h.reload();required=h.expected['load']
                    h.listing.selection_clear(0,'end');h.listing.selection_set(0)
                    h.listing.event_generate('<<ListboxSelect>>');self.app.update()
                    self.assertTrue(h.loading)
                    self.assertIn(required,[item[0] for item in h.worker.pending.values()])
                release.set()
                self.pump(lambda:not h.loading and h.verified is not None and h.worker.active is None)
                self.assertEqual(loads.call_count,1)
            self.assertFalse(h.recompute_button.instate(['disabled']))
            self.assertFalse(h.worker.pending)
        finally:release.set()

    def test_explicit_recompute_cannot_be_replaced_by_reload_or_selection(self):
        h=self.ready();original=h.selected();path=self.view.store.directory/(original['snapshot_id']+'.json')
        original_bytes=path.read_bytes();before=len(list(self.view.store.directory.glob('*.json')))
        entered=threading.Event();release=threading.Event();verify=self.view.store.verified_input
        def slow(*args):entered.set();release.wait(5);return verify(*args)
        try:
            with patch.object(self.view.store,'verified_input',side_effect=slow):
                h.recompute();self.assertTrue(entered.wait(1));request=h.expected['recompute']
                h.reload();h.listing.event_generate('<<ListboxSelect>>');self.app.update()
                self.assertTrue(h.recomputing);self.assertEqual(h.expected['recompute'],request)
                release.set()
                self.pump(lambda:not h.recomputing and not h.loading and h.verified is not None)
            self.assertEqual(len(list(self.view.store.directory.glob('*.json'))),before+1)
            self.assertEqual(h.selected()['recomputed_from'],original['snapshot_id'])
            self.assertEqual(path.read_bytes(),original_bytes)
        finally:release.set()

    def test_failed_verify_is_terminal_and_next_selection_can_retry(self):
        h=self.ready()
        with patch.object(self.view.store,'verified_input',side_effect=OSError('controlled read failure')):
            h.select();self.pump(lambda:'controlled read failure' in h.status.get())
        self.assertFalse(h.loading);self.assertFalse(h.recomputing);self.assertIsNone(h.verified)
        h.select();self.pump(lambda:h.verified is not None)
        self.assertFalse(h.recompute_button.instate(['disabled']))
