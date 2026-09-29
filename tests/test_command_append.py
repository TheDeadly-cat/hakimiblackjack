"""Suffix persistence identity/atomicity and private preparation boundaries."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
from blackjack_lab.ledger.command import CommandCandidate
from blackjack_lab.ledger.events import Event, CARD_DEALT
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.read_snapshot import PrefixSnapshot


class CommandAppendTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / 'local.db'
        self.ctrl = SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(ace_peek_das_research_rules(8))
        self.ctrl.start_round(['玩家1'], my_seat='玩家1')

    def candidate(self):
        c = self.ctrl
        return CommandCandidate(c.ledger, PrefixSnapshot.capture(c.ledger))

    def two_cards(self):
        candidate = self.candidate()
        candidate.deal('玩家1', '9', suit='H')
        candidate.deal('庄家', '7', suit='S')
        return candidate

    def test_only_new_events_are_inserted_and_exact_batch_retry_is_idempotent(self):
        c = self.ctrl
        candidate = self.two_cards()
        with patch.object(c.store, '_insert', wraps=c.store._insert) as insert:
            self.assertEqual(c.store.append_validated(candidate), 2)
            self.assertEqual(insert.call_count, 2)
            self.assertEqual(c.store.append_validated(candidate), 0)
            self.assertEqual(insert.call_count, 2)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), candidate.to_list())

    def test_wrong_baseline_same_seq_content_is_rejected(self):
        c = self.ctrl
        candidate = self.two_cards()
        with c.store.conn:
            c.store.conn.execute('UPDATE events SET payload_json=? WHERE event_id=?',
                (json.dumps({'note': 'same-sequence changed content'}), c.ledger.events[0].event_id))
        with self.assertRaisesRegex(ValueError, '基线'):
            c.store.append_validated(candidate)
        self.assertEqual(c.store.event_count(), len(c.ledger.events))

    def test_stale_baseline_with_other_committed_command_is_rejected(self):
        c = self.ctrl
        candidate = self.two_cards()
        c.deal_shown('玩家1', '8')
        before = c.store.load_ledger(c.session_id).to_list()
        with self.assertRaisesRegex(ValueError, '基线'):
            c.store.append_validated(candidate)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)

    def test_partial_existing_batch_is_conflict_not_retry(self):
        c = self.ctrl
        candidate = self.two_cards()
        c.store.save_event(candidate.validated_suffix()[0])
        with self.assertRaisesRegex(ValueError, '基线'):
            c.store.append_validated(candidate)
        self.assertEqual(c.store.event_count(), len(c.ledger.events) + 1)

    def test_begin_immediate_covers_the_baseline_read_and_suffix_write(self):
        c = self.ctrl
        candidate = self.two_cards()
        competing = sqlite3.connect(self.db, timeout=0)
        self.addCleanup(competing.close)
        load = c.store.load_events
        checked = []
        def while_locked(session_id):
            self.assertTrue(c.store.conn.in_transaction)
            with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                competing.execute('UPDATE events SET source=source WHERE session_id=?', (session_id,))
            competing.rollback()
            checked.append(True)
            return load(session_id)
        with patch.object(c.store, 'load_events', side_effect=while_locked):
            c.store.append_validated(candidate)
        self.assertEqual(checked, [True])

    def test_cross_session_global_event_id_collision_rolls_back(self):
        c = self.ctrl
        candidate = self.two_cards()
        foreign = EventLedger('different-session')
        event = foreign.start_session()
        event.event_id = candidate.events[-1].event_id
        c.store.save_ledger(foreign)
        with self.assertRaisesRegex(ValueError, '事件ID'):
            c.store.append_validated(candidate)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), c.ledger.to_list())
        self.assertEqual(c.store.load_ledger(foreign.session_id).to_list(), foreign.to_list())

    def test_same_event_id_changed_metadata_is_not_idempotent(self):
        c = self.ctrl
        candidate = self.two_cards()
        c.store.append_validated(candidate)
        before = c.store.load_ledger(c.session_id).to_list()
        candidate.events[-1].evidence = 'different evidence'
        with self.assertRaisesRegex(ValueError, '基线'):
            c.store.append_validated(candidate)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)

    def test_post_validation_rank_session_or_sequence_edit_is_rejected(self):
        for field in ('rank', 'session_id', 'seq'):
            with self.subTest(field=field):
                candidate = self.two_cards()
                if field == 'rank':
                    candidate.events[-1].payload[field] = '8'
                else:
                    setattr(candidate.events[-1], field, 99 if field == 'seq' else 'other')
                with self.assertRaisesRegex(LedgerError, '预演后'):
                    self.ctrl.store.append_validated(candidate)
        self.assertEqual(self.ctrl.store.event_count(), len(self.ctrl.ledger.events))

    def test_prior_event_mutation_is_rejected_even_when_final_state_is_legal(self):
        candidate = self.two_cards()
        candidate.events[0].payload['note'] = 'not the saved prefix'
        with self.assertRaisesRegex(LedgerError, '既有事件'):
            self.ctrl.store.append_validated(candidate)

    def test_candidate_validates_intermediate_states_and_stays_failed(self):
        candidate = self.two_cards()
        candidate.deal('玩家1', '8')
        candidate.deal('庄家', hidden=True)
        hand = candidate.replay().current.table.players['玩家1'].hands[0].hand_id
        candidate.player_action('玩家1', hand, '停牌')
        with self.assertRaises(Exception):
            candidate.append(Event(CARD_DEALT, dict(seat='玩家1', hand_id=hand, rank='2', face_state='shown')))
        with self.assertRaisesRegex(LedgerError, '失败'):
            candidate.undo_last()
        with self.assertRaisesRegex(LedgerError, '预演'):
            self.ctrl.store.append_validated(candidate)

    def test_historical_replay_does_not_reuse_final_prepared_state(self):
        candidate = self.two_cards()
        before = self.ctrl.ledger.events[-1].seq
        self.assertEqual(candidate.replay(before).current.table.players['玩家1'].hands, [])
        self.assertEqual(candidate.replay().current.table.players['玩家1'].hands[0].ranks, ['9'])

    def test_external_import_still_rejects_rewritten_prefix(self):
        c = self.ctrl
        ledger = copy.deepcopy(c.ledger)
        ledger.events[0].payload['note'] = 'altered import'
        with self.assertRaisesRegex(ValueError, '历史冲突'):
            c.store.save_ledger(ledger)
        with self.assertRaises(TypeError):
            c.store.append_validated(ledger)

    def test_postcommit_prefix_shared_in_callback_then_discarded(self):
        c = self.ctrl
        captures = []
        c.add_context_listener(lambda: captures.append(c.read_prefix()))
        with c.read_frame():
            first = c.read_prefix()
            c.deal_shown('玩家1', '9')
            committed = c.read_prefix()
            self.assertIs(captures[-1], committed)
            self.assertEqual(c.entry_plan.ledger_digest, committed.prefix_digest)
            self.assertIsNot(first, committed)
            self.assertIs(type(c.ledger), EventLedger)
        self.assertIsNone(c._read_prefix)
        self.assertIsNone(c._read_snapshot)
        c.ledger.events[-1].payload['suit'] = 'D'
        with c.read_frame():
            self.assertNotEqual(c.read_prefix().prefix_digest, committed.prefix_digest)

    def test_session_change_inside_frame_cannot_reuse_previous_prefix_or_state(self):
        c = self.ctrl
        other = SessionController(self.db)
        self.addCleanup(other.close)
        other.new_shoe(ace_peek_das_research_rules(6))
        with c.read_frame():
            first = c.read_prefix()
            c.load_session(other.session_id)
            second = c.read_prefix()
            self.assertEqual(second.session_id, other.session_id)
            self.assertEqual(c.state().current.shoe.physical_remaining(), 312)
            self.assertNotEqual(first.prefix_digest, second.prefix_digest)
            c.load_session(first.session_id)
            self.assertEqual(c.read_prefix().prefix_digest, first.prefix_digest)
            self.assertEqual(c.state().current.shoe.physical_remaining(), 416)


if __name__ == '__main__':
    unittest.main()
