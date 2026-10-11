"""Explicit stage refusal is recoverable only after a complete no-write proof."""
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from blackjack_lab.core.table import ACTION_STAND, TableError
from blackjack_lab.ui.recording_commands import RecordingInputRejected
from blackjack_lab.ui.recording_owner import RecordingOwner
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from tests import test_recording_owner as fixture


class RecordingInputRejectionTests(unittest.TestCase):
    use_process = False
    close_owner = fixture.TestRecordingOwner.close_owner
    results = fixture.TestRecordingOwner.results

    def setUp(self):
        fixture.TestRecordingOwner.setUp(self)
        self.owner.close()
        self.owner = RecordingOwner(self.path, self.ctrl.recording_source,
                                    use_process=self.use_process)

    def intent(self, kind, value=None):
        data = dict(kind=kind, follow_plan=True, auto_next=None,
                    reveal_event_id=None,
                    selection=dict(seat='玩家1', hand_id=None,
                                   hand_mode='acting', mode='新发牌', suit=None))
        if kind == 'rank':
            data['rank'] = value
        elif kind == 'action':
            data['action'] = value
        return data

    def test_single_explicit_refusal_keeps_owner_usable_and_history_unchanged(self):
        request = self.owner.submit(self.base, 'record_input', self.intent('hole'))
        receipt = self.results(1)[0]
        self.assertEqual((receipt.request_id, receipt.status), (request, 'rejected'))
        self.assertIn('庄家暗牌槽', receipt.error)
        self.assertEqual(receipt.before, self.base)
        self.assertEqual(receipt.after, self.base)
        self.assertEqual(receipt.event_ids, ())
        self.assertIsNotNone(receipt.entry_plan)
        self.assertIsNone(receipt.failure_path)
        self.assertFalse(self.owner.failed)
        self.assertEqual(PrefixSnapshot.capture(
            self.ctrl.store.load_ledger(self.ctrl.session_id)), self.base)
        self.assertFalse(Path(str(self.path) + '.recording-failures').exists())
        if self.use_process:
            self.assertIs(receipt.verified_token, self.owner.verification_token)
        self.owner.submit(self.base, 'record_input', self.intent('rank', '8'))
        corrected = self.results(1)[0]
        self.assertEqual(corrected.status, 'committed')
        self.assertEqual([e.payload['rank'] for e in corrected.ledger.events
                          if e.etype == 'CARD_DEALT'], ['8'])

    def test_existing_success_is_retained_and_later_queued_input_is_not_executed(self):
        first = self.owner.submit(self.base, 'record_input', self.intent('rank', '8'))
        refused = self.owner.submit(self.base, 'record_input', self.intent('hole'))
        later_intent = self.intent('rank', '6')
        later = self.owner.submit(self.base, 'record_input', later_intent)
        receipts = self.results(3)
        self.assertEqual([r.request_id for r in receipts], [first, refused, later])
        self.assertEqual([r.status for r in receipts],
                         ['committed', 'rejected', 'not_executed'])
        self.assertEqual(receipts[1].before, receipts[0].after)
        self.assertEqual(receipts[1].after, receipts[0].after)
        actual = self.ctrl.store.load_ledger(self.ctrl.session_id)
        self.assertEqual([e.payload['rank'] for e in actual.events
                          if e.etype == 'CARD_DEALT'], ['8'])
        self.assertIsNone(receipts[1].failure_path)
        archive = json.loads(Path(receipts[2].failure_path).read_text(encoding='utf-8'))
        self.assertEqual(archive['arguments']['args'][0], later_intent)
        self.assertFalse(archive['automatic_retry'])
        self.assertEqual(archive['status'], 'not_executed')
        self.assertTrue(self.owner.failed)
        with self.assertRaises(RuntimeError):
            self.owner.submit(receipts[0].after, 'record_input', later_intent)

    def test_premature_player_action_is_an_explicit_stage_refusal(self):
        self.owner.submit(self.base, 'record_input', self.intent('rank', '8'))
        self.assertEqual(self.results(1)[0].status, 'committed')
        before = PrefixSnapshot.capture(self.ctrl.store.load_ledger(self.ctrl.session_id))
        self.owner.submit(before, 'record_input', self.intent('action', ACTION_STAND))
        receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'rejected')
        self.assertIn('初始两张牌', receipt.error)
        self.assertEqual(receipt.after, before)

    def test_bclc_point_at_hole_slot_can_be_corrected_with_explicit_hole(self):
        from blackjack_lab.ui.recording_commands import record_input
        from blackjack_lab.ui.table_modes import bclc_rules
        self.ctrl.replace_shoe(bclc_rules(), self.ctrl.context_token)
        self.ctrl.start_round(['玩家1'], simple_hole=False)
        for rank in ('8', '6', '7'):
            record_input(self.ctrl, self.intent('rank', rank))
        before = PrefixSnapshot.capture(self.ctrl.ledger)
        self.owner.submit(before, 'record_input', self.intent('rank', '9'))
        receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'rejected')
        self.assertIn('暗牌 .', receipt.error)
        self.assertEqual(receipt.before, receipt.after)
        self.assertIsNone(receipt.state.current.rules.start_from_new_shoe)
        self.assertIsNone(receipt.state.current.rules.initial_burn_count)
        self.owner.submit(before, 'record_input', self.intent('hole'))
        confirmed = self.results(1)[0]
        self.assertEqual(confirmed.status, 'committed')
        self.assertEqual([e.payload['face_state'] for e in confirmed.ledger.events
                          if e.etype == 'CARD_DEALT'], ['shown', 'shown', 'shown', 'hidden'])
        self.assertIsNone(confirmed.state.current.rules.start_from_new_shoe)
        self.assertIsNone(confirmed.state.current.rules.initial_burn_count)


class ProcessRecordingInputRejectionTests(RecordingInputRejectionTests):
    use_process = True


class RejectionNoWriteProofTests(unittest.TestCase):
    setUp = fixture.TestRecordingOwner.setUp
    close_owner = fixture.TestRecordingOwner.close_owner
    results = fixture.TestRecordingOwner.results
    intent = RecordingInputRejectionTests.intent

    def test_generic_table_error_is_still_a_failure(self):
        with patch('blackjack_lab.ui.recording_commands.record_input',
                   side_effect=TableError('unclassified command error')):
            self.owner.submit(self.base, 'record_input', self.intent('hole'))
            receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'failed_before_commit')
        self.assertIsNotNone(receipt.failure_path)
        self.assertTrue(self.owner.failed)

    def test_custom_refusal_after_write_remains_unknown(self):
        def wrong_refusal(ctrl, intent):
            ctrl.deal_shown('玩家1', '8')
            raise RecordingInputRejected('incorrect refusal after a durable write')
        with patch('blackjack_lab.ui.recording_commands.record_input', side_effect=wrong_refusal):
            self.owner.submit(self.base, 'record_input', self.intent('hole'))
            receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'unknown_commit_outcome')
        self.assertNotEqual(receipt.after, self.base)
        self.assertTrue(self.owner.failed)
        self.assertIsNotNone(receipt.failure_path)

    def test_unavailable_full_readback_cannot_prove_rejection(self):
        from blackjack_lab.storage.database import LocalStore
        load = LocalStore.load_ledger
        calls = 0
        def readback_fails(store, session):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise sqlite3.OperationalError('readback unavailable')
            return load(store, session)
        with patch.object(LocalStore, 'load_ledger', side_effect=readback_fails, autospec=True):
            self.owner.submit(self.base, 'record_input', self.intent('hole'))
            receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'unknown_commit_outcome')
        self.assertTrue(self.owner.failed)
        self.assertIsNotNone(receipt.failure_path)


if __name__ == '__main__':
    unittest.main()
