from pathlib import Path
import tempfile
import threading
from time import monotonic, sleep
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from blackjack_lab.ui.recording_owner import RecordingOwner


class TestRecordingOwner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'owner.db'
        self.ctrl = SessionController(self.path, recording_source=SOURCE_SIMULATOR)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(both_initial_das_rules(8))
        self.ctrl.start_round(['玩家1'], simple_hole=True)
        self.base = PrefixSnapshot.capture(self.ctrl.ledger)
        self.owner = RecordingOwner(self.path, SOURCE_SIMULATOR)
        self.addCleanup(self.close_owner)

    def close_owner(self):
        self.owner.close()
        deadline = monotonic()+5
        while not self.owner.stopped and monotonic()<deadline:
            sleep(.01)
        self.assertTrue(self.owner.stopped, 'SQLite owner did not drain and stop')

    def results(self, count):
        output=[]; deadline=monotonic()+5
        while len(output)<count and monotonic()<deadline:
            output.extend(self.owner.poll()); sleep(.01)
        self.assertEqual(len(output), count)
        return output

    def test_queued_commands_keep_fifo_order_and_commit_before_receipts(self):
        first = self.owner.submit(self.base, 'deal_shown', '玩家1', '8')
        second = self.owner.submit(self.base, 'deal_shown', '庄家', '6')
        receipts = self.results(2)
        self.assertEqual([r.request_id for r in receipts], [first, second])
        self.assertEqual([r.status for r in receipts], ['committed', 'committed'])
        self.assertEqual(receipts[0].after.content, receipts[1].before.content)
        actual = self.ctrl.store.load_ledger(self.ctrl.session_id)
        self.assertEqual(PrefixSnapshot.capture(actual).content, receipts[-1].after.content)
        self.assertEqual([e.payload['seat'] for e in actual.events if e.etype == 'CARD_DEALT'], ['玩家1','庄家'])
        self.assertEqual(PrefixSnapshot.capture(self.ctrl.ledger).content, self.base.content)

    def test_a_failed_command_stops_later_inputs_without_retrying(self):
        self.owner.submit(self.base, 'deal_shown', '玩家1', 'Z')
        self.owner.submit(self.base, 'deal_shown', '庄家', '6')
        receipts = self.results(2)
        self.assertEqual([r.status for r in receipts], ['failed_before_commit', 'not_executed'])
        self.assertEqual(PrefixSnapshot.capture(self.ctrl.store.load_ledger(self.ctrl.session_id)).content, self.base.content)
        with self.assertRaisesRegex(RuntimeError, '失败'):
            self.owner.submit(self.base, 'deal_shown', '玩家1', '8')

    def test_duplicate_requests_are_not_accepted_twice(self):
        self.owner.submit(self.base, 'deal_shown', '玩家1', '8', request_id='same-request')
        with self.assertRaisesRegex(ValueError, '重复'):
            self.owner.submit(self.base, 'deal_shown', '玩家1', '8', request_id='same-request')
        self.assertEqual(self.results(1)[0].status, 'committed')

    def test_a_changed_durable_prefix_cannot_be_rebased_silently(self):
        self.ctrl.deal_shown('玩家1', '9')
        self.owner.submit(self.base, 'deal_shown', '庄家', '6')
        receipt = self.results(1)[0]
        self.assertNotEqual(receipt.status, 'committed')
        actual = self.ctrl.store.load_ledger(self.ctrl.session_id)
        self.assertEqual([e.payload['rank'] for e in actual.events if e.etype == 'CARD_DEALT'], ['9'])

    def test_exception_after_durable_write_preserves_unknown_without_repeating(self):
        from blackjack_lab.storage.database import LocalStore
        original = LocalStore.append_validated
        def fail_after_write(store, candidate):
            original(store, candidate)
            raise RuntimeError('injected failure after durable write')
        with patch.object(LocalStore, 'append_validated', fail_after_write):
            self.owner.submit(self.base, 'deal_shown', '玩家1', '8')
            self.owner.submit(self.base, 'deal_shown', '庄家', '6')
            receipts = self.results(2)
        self.assertEqual([r.status for r in receipts], ['unknown_commit_outcome', 'not_executed'])
        actual = self.ctrl.store.load_ledger(self.ctrl.session_id)
        self.assertEqual([e.payload['rank'] for e in actual.events if e.etype == 'CARD_DEALT'], ['8'])
        self.assertNotEqual(receipts[0].after.content, self.base.content)

    def test_worker_start_failure_accepts_no_input_and_changes_no_history(self):
        with patch.object(threading.Thread, 'start', side_effect=OSError('injected worker start failure')):
            with self.assertRaises(OSError):
                self.owner.submit(self.base, 'deal_shown', '玩家1', '8')
        self.assertEqual(self.owner.pending_count, 0)
        self.assertTrue(self.owner.queue.empty())
        self.assertEqual(PrefixSnapshot.capture(self.ctrl.store.load_ledger(self.ctrl.session_id)).content, self.base.content)

    def test_full_queue_rejects_new_input_without_replacing_accepted_card(self):
        from blackjack_lab.storage.database import LocalStore
        self.owner.close()
        self.owner = RecordingOwner(self.path, SOURCE_SIMULATOR, capacity=1)
        entered, release = threading.Event(), threading.Event()
        original = LocalStore.append_validated
        def held(store, candidate):
            entered.set(); release.wait(5)
            return original(store, candidate)
        try:
            with patch.object(LocalStore, 'append_validated', held):
                self.owner.submit(self.base, 'deal_shown', '玩家1', '8')
                self.assertTrue(entered.wait(2))
                with self.assertRaisesRegex(RuntimeError, '未接收'):
                    self.owner.submit(self.base, 'deal_shown', '庄家', '6')
                self.assertEqual(self.owner.pending_count, 1)
                release.set()
                self.assertEqual(self.results(1)[0].status, 'committed')
        finally:
            release.set()
        actual = self.ctrl.store.load_ledger(self.ctrl.session_id)
        self.assertEqual([e.payload['rank'] for e in actual.events if e.etype == 'CARD_DEALT'], ['8'])


if __name__ == '__main__':
    unittest.main()
