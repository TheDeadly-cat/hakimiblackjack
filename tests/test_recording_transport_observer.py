"""The component probe observes both wire formats without changing the receipt."""
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from blackjack_lab.ui.recording_owner import RecordingReceipt, RecordingTask
from blackjack_lab.ui.recording_transport import ReceiptPrefixSender, ReceiptPrefixReceiver
from scripts.sidebet_closeout_measure import receipt_metadata


class TransportObserverTests(unittest.TestCase):
    def setUp(self):
        ledger = EventLedger('synthetic-observer')
        ledger.start_session()
        before = PrefixSnapshot.capture(ledger)
        event = ledger.create_shoe(both_initial_das_rules(8))
        after = PrefixSnapshot.capture(ledger)
        self.receipt = RecordingReceipt('request', 'chain', 'committed', before, after,
            ledger, ledger.replay(), None, (event.event_id,),
            submitted_at=1.0, started_at=2.0, finished_at=3.0)
        self.task = RecordingTask('request', 'chain', before, 'deal_shown', '{}', 1.0)

    def test_original_wire_receipt_keeps_identity_and_exact_sequence(self):
        metadata, seq = receipt_metadata(self.receipt)
        self.assertIs(metadata, self.receipt)
        self.assertEqual(seq, self.receipt.after.through_seq)

    def test_delta_wire_receipt_is_observed_without_reconstruction_or_extra_validation(self):
        packet = ReceiptPrefixSender().encode(self.receipt, self.task)
        with patch.object(ReceiptPrefixReceiver, 'decode', side_effect=AssertionError('observer decoded')):
            with patch.object(PrefixSnapshot, 'capture', side_effect=AssertionError('observer captured')):
                metadata, seq = receipt_metadata(packet)
        self.assertIs(metadata, packet.receipt)
        self.assertIsNone(metadata.after)
        self.assertEqual(seq, self.receipt.after.through_seq)
        self.assertEqual((metadata.request_id, metadata.finished_at), ('request', 3.0))

    def test_shutdown_and_input_frames_are_not_miscounted_as_result_receipts(self):
        self.assertEqual(receipt_metadata(None), (None, None))
        self.assertEqual(receipt_metadata(('request', 'chain', '{}')), (None, None))


if __name__ == '__main__':
    unittest.main()
