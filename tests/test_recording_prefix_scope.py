"""Complete-prefix sharing is confined to one independently validated command."""
import unittest
from unittest.mock import patch

from blackjack_lab.ledger.ledger import LedgerError
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from tests import test_recording_owner as fixture


class RecordingPrefixScopeTests(unittest.TestCase):
    setUp = fixture.TestRecordingOwner.setUp
    close_owner = fixture.TestRecordingOwner.close_owner
    results = fixture.TestRecordingOwner.results

    def test_prefix_first_frame_replays_and_recaptures_on_the_next_frame(self):
        ctrl = self.ctrl
        real_capture = PrefixSnapshot.capture
        with patch.object(PrefixSnapshot, 'capture', wraps=real_capture) as capture:
            with ctrl.read_frame():
                before = ctrl.read_prefix()
                with patch.object(ctrl.ledger, 'replay', wraps=ctrl.ledger.replay) as replay:
                    self.assertIsNotNone(ctrl.state().current)
                    self.assertEqual(replay.call_count, 1)
                self.assertIs(ctrl.read_prefix(), before)
            self.assertEqual(capture.call_count, 1)
            # Same sequence/context token, changed full content. A different
            # callback must capture it afresh, rather than reuse cached bytes.
            ctrl.ledger.events[0].payload['note'] = 'changed between frames'
            with ctrl.read_frame():
                after = ctrl.read_prefix()
                self.assertIsNotNone(ctrl.state().current)
            self.assertEqual(capture.call_count, 2)
        self.assertNotEqual(after.content, before.content)

    def test_queued_inputs_share_before_bytes_without_skipping_durable_validation(self):
        from blackjack_lab.ui.recording_commands import record_input
        observations = []
        real_capture = PrefixSnapshot.capture

        def observed(ctrl, intent):
            original_ledger = ctrl.ledger
            before = ctrl.read_prefix()
            captures = []

            def capture(ledger):
                captures.append(ledger)
                return real_capture(ledger)

            with patch.object(PrefixSnapshot, 'capture', side_effect=capture):
                result = record_input(ctrl, intent)
            observations.append((before, sum(ledger is original_ledger for ledger in captures)))
            return result

        def intent(rank):
            return dict(kind='rank', rank=rank, follow_plan=True, auto_next=False,
                selection=dict(seat='玩家1', hand_id=None, hand_mode='latest', mode='新发牌', suit=None))

        with patch('blackjack_lab.ui.recording_commands.record_input', side_effect=observed):
            self.owner.submit(self.base, 'record_input', intent('8'))
            self.owner.submit(self.base, 'record_input', intent('6'))
            receipts = self.results(2)
        self.assertEqual([r.status for r in receipts], ['committed', 'committed'])
        self.assertEqual([count for _, count in observations], [0, 0])
        self.assertIsNot(observations[0][0], observations[1][0])
        self.assertEqual(receipts[0].before.content, observations[0][0].content)
        self.assertEqual(receipts[1].before.content, receipts[0].after.content)
        self.assertEqual(PrefixSnapshot.capture(self.ctrl.store.load_ledger(self.ctrl.session_id)).content,
                         receipts[-1].after.content)
        self.assertEqual(PrefixSnapshot.capture(self.ctrl.ledger).content, self.base.content)

    def test_in_place_change_after_capture_cannot_be_rebased_or_saved(self):
        ctrl = self.ctrl
        durable = ctrl.store.load_ledger(ctrl.session_id).to_list()
        with ctrl.read_frame():
            before = ctrl.read_prefix()
            ctrl.ledger.events[0].payload['note'] = 'uncommitted in-place change'
            with self.assertRaises((LedgerError, ValueError)):
                ctrl.deal_shown('玩家1', '9')
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), durable)
        self.assertEqual(before.content, self.base.content)


class ReplayShoeIdentityTests(unittest.TestCase):
    def assert_duplicate_rejected(self, first, second):
        from blackjack_lab.analysis.split_contracts import both_initial_das_rules
        from blackjack_lab.ledger.ledger import EventLedger
        ledger = EventLedger('shoe-identity-regression')
        ledger.start_session()
        ledger.create_shoe(both_initial_das_rules(8), shoe_id=first)
        ledger.end_shoe()
        before = ledger.to_list()
        with self.assertRaisesRegex(LedgerError, '唯一身份'):
            ledger.create_shoe(both_initial_das_rules(8), shoe_id=second)
        self.assertEqual(ledger.to_list(), before)

    def test_plain_duplicate_shoe_identity_does_not_append(self):
        self.assert_duplicate_rejected('same-shoe', 'same-shoe')

    def test_unhashable_str_identity_preserves_original_equality_check(self):
        class UnhashableText(str):
            __hash__ = None
        self.assert_duplicate_rejected('same-shoe', UnhashableText('same-shoe'))
        self.assert_duplicate_rejected(UnhashableText('same-shoe'), 'same-shoe')


if __name__ == '__main__':
    unittest.main()
