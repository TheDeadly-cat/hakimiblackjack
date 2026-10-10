"""Owned prefix transport keeps full validation, order, and unknown outcomes."""
from dataclasses import replace
import hashlib
import json
from queue import Queue
import unittest

from blackjack_lab.ui.recording_owner import RecordingOwner, RecordingReceipt, RecordingTask
from blackjack_lab.ui.recording_transport import (
    PrefixDeltaReceipt, ReceiptPrefixReceiver, ReceiptPrefixSender)
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from tests import test_recording_owner as fixture


class RecordingTransportTests(unittest.TestCase):
    setUp = fixture.TestRecordingOwner.setUp
    close_owner = fixture.TestRecordingOwner.close_owner

    def receipts(self):
        output = []
        for request, seat, rank in (('first', '玩家1', '8'), ('second', '庄家', '6')):
            with self.ctrl.read_frame():
                before = self.ctrl.read_prefix()
                event = self.ctrl.deal_shown(seat, rank)
                after = self.ctrl.read_prefix()
                receipt = RecordingReceipt(request, 'chain', 'committed', before, after,
                    self.ctrl.ledger, self.ctrl.state(), self.ctrl.entry_plan, (event.event_id,))
            task = RecordingTask(request, 'chain', self.base, 'deal_shown', '{}', 0.0)
            output.append((receipt, task))
        return output

    def accept(self, receiver, packet, task):
        receipt = receiver.decode(packet, task)
        self.assertEqual(PrefixSnapshot.capture(receipt.ledger), receipt.after)
        receiver.accepted(receipt, task)
        return receipt

    def test_two_fifo_receipts_reconstruct_all_original_bytes_and_keep_full_objects(self):
        sender, receiver = ReceiptPrefixSender(), ReceiptPrefixReceiver()
        pairs = self.receipts()
        for original, task in pairs:
            packet = sender.encode(original, task)
            self.assertIsInstance(packet, PrefixDeltaReceipt)
            self.assertIsNone(packet.receipt.before)
            self.assertIsNone(packet.receipt.after)
            restored = self.accept(receiver, packet, task)
            self.assertEqual(restored, original)
            self.assertIs(restored.ledger, original.ledger)
            self.assertIs(restored.state, original.state)
            self.assertIs(restored.entry_plan, original.entry_plan)
        self.assertIs(receiver.prefix, restored.after)

    def test_corrupt_suffix_and_forged_metadata_are_rejected_before_publication(self):
        original, task = self.receipts()[0]
        packet = ReceiptPrefixSender().encode(original, task)
        bad_packets = (
            replace(packet, suffix=packet.suffix + b' '),
            replace(packet, before_identity=('another-session', *packet.before_identity[1:])),
            replace(packet, after_identity=(task.base.session_id, True, packet.after_identity[2])),
            replace(packet, after_identity=(task.base.session_id, original.after.through_seq, '0' * 64)),
            replace(packet, receipt=replace(packet.receipt, request_id='other-request')),
        )
        for bad in bad_packets:
            with self.subTest(packet=bad):
                receiver = ReceiptPrefixReceiver()
                with self.assertRaises(ValueError):
                    receiver.decode(bad, task)
                self.assertIsNone(receiver.prefix)

    def test_skipping_an_intermediate_receipt_cannot_rebase_the_parent(self):
        pairs = self.receipts()
        sender = ReceiptPrefixSender()
        sender.encode(*pairs[0])
        second = sender.encode(*pairs[1])
        with self.assertRaises(ValueError):
            ReceiptPrefixReceiver().decode(second, pairs[1][1])

    def test_nonappend_change_uses_original_full_receipt_and_still_fails_parent_check(self):
        original, task = self.receipts()[0]
        changed = json.loads(original.after.content)
        changed[0]['payload']['note'] = 'same-sequence change'
        from blackjack_lab.analysis.contracts import canonical
        content = canonical(changed).encode('utf-8')
        modified = replace(original, after=replace(original.after, content=content,
            prefix_digest=hashlib.sha256(content).hexdigest()))
        packet = ReceiptPrefixSender().encode(modified, task)
        self.assertIs(packet, modified)
        self.assertNotEqual(PrefixSnapshot.capture(packet.ledger), packet.after)

    def test_invalid_delta_preserves_unknown_input_and_disallows_automatic_retry(self):
        original, task = self.receipts()[0]
        packet = ReceiptPrefixSender().encode(original, task)
        packet = replace(packet, suffix=packet.suffix + b'bad')
        owner = RecordingOwner(self.path, self.ctrl.recording_source, use_process=True)
        owner.pending_tasks = {task.request_id: task}
        owner.process_results = Queue()
        owner.process_results.put(packet)
        owner.process_results.put(None)
        owner._receive_process()
        result = owner.results.get_nowait()
        self.assertEqual(result.status, 'unknown_commit_outcome')
        self.assertIsNone(result.verified_token)
        self.assertIsNone(result.ledger)
        self.assertEqual(result.before, self.base)
        self.assertEqual(result.after, self.base)
        self.assertIsNotNone(result.failure_path)
        from pathlib import Path
        archive = json.loads(Path(result.failure_path).read_text(encoding='utf-8'))
        self.assertFalse(archive['automatic_retry'])
        self.assertEqual(archive['request_id'], task.request_id)
        with self.assertRaisesRegex(RuntimeError, '不能自动重试'):
            owner.submit(self.base, 'deal_shown', '玩家1', '8')

    def test_independent_ledger_check_rejects_self_consistent_but_wrong_delta(self):
        original, task = self.receipts()[0]
        packet = ReceiptPrefixSender().encode(original, task)
        suffix = packet.suffix.replace(b'"rank":"8"', b'"rank":"9"')
        self.assertNotEqual(suffix, packet.suffix)
        content = self.base.content[:-1] + suffix + b']'
        forged = replace(packet, suffix=suffix,
            after_identity=(task.base.session_id, original.after.through_seq,
                hashlib.sha256(content).hexdigest()))
        owner = RecordingOwner(self.path, self.ctrl.recording_source, use_process=True)
        owner.pending_tasks = {task.request_id: task}
        owner.process_results = Queue()
        owner.process_results.put(forged)
        owner.process_results.put(None)
        owner._receive_process()
        result = owner.results.get_nowait()
        self.assertEqual(result.status, 'unknown_commit_outcome')
        self.assertIsNone(result.verified_token)
        self.assertIn('完整账本内容不符', result.error)


if __name__ == '__main__':
    unittest.main()
