"""Fresh command planning preserves complete replay and durable authority."""
import sqlite3
import unittest
from unittest.mock import patch

from blackjack_lab.ledger.command import CommandCandidate
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ui.recording_commands import record_input
from tests import test_command_append as fixture


class RecordingCommandPlanningTests(unittest.TestCase):
    setUp = fixture.CommandAppendTests.setUp

    def intent(self, rank='9'):
        return dict(kind='rank', rank=rank, follow_plan=True, auto_next=False,
            selection=dict(seat='玩家1', hand_id=None, hand_mode='latest', mode='新发牌', suit=None))

    def assert_saved(self):
        self.assertEqual(self.ctrl.store.load_ledger(self.ctrl.session_id).to_list(),
                         self.ctrl.ledger.to_list())

    def test_complete_input_plans_from_its_independent_command_replay(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        real = EventLedger.replay
        calls = []

        def replay(ledger, *args, **kwargs):
            calls.append((type(ledger).__name__, len(ledger.events)))
            return real(ledger, *args, **kwargs)

        with patch.object(EventLedger, 'replay', replay):
            with ctrl.read_frame():
                result = record_input(ctrl, self.intent())
        # Count the complete callback's replay bodies, including input planning.
        self.assertEqual(calls, [('CommandCandidate', len(before)),
                                 ('CommandCandidate', len(before) + 1)])
        self.assertFalse(result['round_changed'])
        self.assertEqual(ctrl.ledger.to_list()[:-1], before)
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 415)
        self.assert_saved()

    def test_existing_mutable_view_is_not_used_as_command_validation(self):
        ctrl = self.ctrl
        with ctrl.read_frame():
            view = ctrl.state()
            view.current.closed = True
            view.current.shoe.mark_gap('poisoned derived view')
            record_input(ctrl, self.intent())
            current = ctrl.state().current
            self.assertFalse(current.closed)
            self.assertFalse(current.shoe.gap)
            self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['9'])
        self.assert_saved()

    def test_changed_metadata_during_planning_cannot_be_saved_or_discarded(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        real = ctrl.deal_shown
        for field, changed in (('source', 'changed source'), ('evidence', 'changed evidence'),
                               ('observed_at', ctrl.ledger.events[0].observed_at + 1)):
            with self.subTest(field=field):
                event = ctrl.ledger.events[0]
                original = getattr(event, field)

                def mutate(*args, **kwargs):
                    setattr(event, field, changed)
                    return real(*args, **kwargs)

                with patch.object(ctrl, 'deal_shown', side_effect=mutate):
                    with self.assertRaisesRegex(LedgerError, '基线已变化'):
                        record_input(ctrl, self.intent())
                self.assertEqual(getattr(ctrl.ledger.events[0], field), changed)
                self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
                setattr(event, field, original)
        record_input(ctrl, self.intent('8'))
        self.assert_saved()

    def test_context_change_during_planning_cannot_consume_preparation(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        real = ctrl.deal_shown

        def changed(*args, **kwargs):
            ctrl._context_revision += 1
            return real(*args, **kwargs)

        with patch.object(ctrl, 'deal_shown', side_effect=changed):
            with self.assertRaisesRegex(LedgerError, '基线已变化'):
                record_input(ctrl, self.intent())
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)

    def test_SQL_baseline_is_still_checked_after_planning(self):
        ctrl = self.ctrl
        real = ctrl.store.append_validated
        event_id = ctrl.ledger.events[0].event_id

        def changed(candidate):
            with ctrl.store.conn:
                ctrl.store.conn.execute('UPDATE events SET evidence=? WHERE event_id=?',
                                        ('competing durable change', event_id))
            return real(candidate)

        with patch.object(ctrl.store, 'append_validated', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '基线'):
                record_input(ctrl, self.intent())
        saved = ctrl.store.load_ledger(ctrl.session_id).to_list()
        self.assertEqual(len(saved), len(ctrl.ledger.events))
        self.assertEqual(saved[0]['evidence'], 'competing durable change')
        self.assertNotEqual(saved, ctrl.ledger.to_list())

    def test_commit_failure_restores_the_previous_view_and_keeps_original_history(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        with ctrl.read_frame():
            previous = ctrl.state()
            with patch.object(ctrl.store, '_insert', side_effect=sqlite3.OperationalError('write failed')):
                with self.assertRaisesRegex(sqlite3.OperationalError, 'write failed'):
                    record_input(ctrl, self.intent())
            self.assertIs(ctrl.state(), previous)
            self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        record_input(ctrl, self.intent('8'))
        self.assert_saved()

    def test_bad_original_event_cannot_be_hidden_by_an_existing_read_view(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        with ctrl.read_frame():
            ctrl.state()
            event = ctrl.ledger.events[0]
            original = event.observed_at
            event.observed_at = None
            with self.assertRaises((ValueError, LedgerError)):
                record_input(ctrl, self.intent())
            event.observed_at = original
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        record_input(ctrl, self.intent('8'))
        self.assert_saved()

    def test_prepared_view_failure_does_not_strand_the_next_input(self):
        ctrl = self.ctrl
        before = ctrl.ledger.to_list()
        with patch.object(CommandCandidate, 'replay', side_effect=RuntimeError('prepared view unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'prepared view unavailable'):
                record_input(ctrl, self.intent())
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        record_input(ctrl, self.intent('8'))
        self.assert_saved()


if __name__ == '__main__':
    unittest.main()
