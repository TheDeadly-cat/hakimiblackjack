"""Recording command contracts, frozen before PERF-3 implementation.

Inject failures at the actual insertion boundary, independently of which
controller persistence entry point a command selects.
"""
import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
from blackjack_lab.ledger.events import Event, CARD_DEALT
from blackjack_lab.ui.controller import SessionController


class RecordingTransactionContracts(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.db = Path(folder.name) / 'independent.db'
        self.ctrl = SessionController(self.db)
        self.addCleanup(self.ctrl.close)
        self.rules = ace_peek_das_research_rules(8)
        self.ctrl.new_shoe(self.rules)
        self.ctrl.start_round(['玩家1'], my_seat='玩家1', simple_hole=True)

    def initial(self):
        c = self.ctrl
        c.deal_shown('玩家1', '9', suit='S')
        c.deal_shown('庄家', 'T', suit='D')
        c.deal_shown('玩家1', '7', suit='H')

    def terminal(self):
        self.initial()
        c = self.ctrl
        c.player_action('玩家1', c.state().current.table.players['玩家1'].hands[0].hand_id, '停牌')

    def reveal_next(self):
        c = self.ctrl
        hole = c.simple_dealer_route('庄家')
        return c.reveal(hole, '7', suit='C', auto_next=dict(
            participants=['玩家1'], my_seat='玩家1', deal_direction='forward', simple_hole=True))

    def assert_atomic_failure(self, command, ordinal):
        c = self.ctrl
        before, plan = c.ledger.to_list(), copy.deepcopy(c.entry_plan.to_dict())
        revision, token = c.commit_revision, c.context_token
        called = []
        c.add_context_listener(lambda: called.append(True))
        insert = c.store._insert
        pending = []
        def failing(event):
            if event.seq > before[-1]['seq']:
                pending.append(event.event_id)
                if len(pending) == ordinal:
                    raise sqlite3.OperationalError('PERF-3 injected insert failure')
            return insert(event)
        with patch.object(c.store, '_insert', side_effect=failing):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'PERF-3'):
                command()
        self.assertEqual(len(pending), ordinal)
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)
        self.assertEqual(c.entry_plan.to_dict(), plan)
        self.assertEqual((c.commit_revision, c.context_token), (revision, token))
        self.assertEqual(called, [])

    def test_single_event_failure_preserves_plan_and_publication(self):
        self.assert_atomic_failure(lambda: self.ctrl.deal_shown('玩家1', '9'), 1)

    def test_simple_hole_second_insert_rolls_back_visible_card(self):
        self.ctrl.deal_shown('玩家1', '9')
        self.ctrl.deal_shown('庄家', 'T')
        self.assert_atomic_failure(lambda: self.ctrl.deal_shown('玩家1', '7'), 2)

    def test_reveal_next_second_insert_rolls_back_hole_reveal(self):
        self.terminal()
        self.assert_atomic_failure(self.reveal_next, 2)

    def test_reveal_next_third_insert_rolls_back_settlement(self):
        self.terminal()
        self.assert_atomic_failure(self.reveal_next, 3)

    def test_shoe_replacement_third_insert_preserves_old_shoe(self):
        self.initial()
        self.assert_atomic_failure(lambda: self.ctrl.replace_shoe(self.rules, self.ctrl.context_token), 3)

    def test_grouped_undo_third_insert_preserves_completed_round(self):
        self.terminal()
        self.reveal_next()
        self.assert_atomic_failure(self.ctrl.undo_last, 3)

    def test_public_append_rejects_illegal_intermediate_before_later_undo(self):
        c = self.ctrl
        candidate = copy.deepcopy(c.ledger)
        before = candidate.to_list()
        # A later undo could hide an invalid card in a final-state-only check.
        with self.assertRaises(Exception):
            candidate.append(Event(CARD_DEALT, dict(seat='玩家1', rank='invalid', face_state='shown')))
        self.assertEqual(candidate.to_list(), before)
        self.assertEqual(c.ledger.to_list(), before)

    def test_plan_failure_keeps_one_durable_card_and_pauses(self):
        c = self.ctrl
        before = len(c.ledger.events)
        with patch.object(c, 'save_entry_plan', side_effect=OSError('plan sidecar unavailable')):
            event = c.deal_shown('玩家1', '9')
        self.assertEqual(len(c.ledger.events), before + 1)
        self.assertEqual(c.store.load_events(c.session_id)[-1].event_id, event.event_id)
        self.assertTrue(c.entry_plan.paused)
        recovered = SessionController.recover(self.db, c.session_id)
        self.addCleanup(recovered.close)
        self.assertEqual(recovered.ledger.to_list(), c.ledger.to_list())
        self.assertTrue(recovered.entry_plan.paused)

    def test_postcommit_listener_failure_does_not_repeat_or_restore_old_cursor(self):
        c = self.ctrl
        before = len(c.ledger.events)
        def fail():
            raise RuntimeError('paint failed after commit')
        c.add_context_listener(fail)
        event = c.deal_shown('玩家1', '9')
        self.assertEqual(len(c.ledger.events), before + 1)
        self.assertIn('记录已保存', c.context_warning)
        self.assertEqual(c.entry_plan.slot().seat, '庄家')
        self.assertEqual(c.store.load_events(c.session_id)[-1].event_id, event.event_id)

    def test_idempotent_event_does_not_alias_previous_published_ledger(self):
        c = self.ctrl
        event = c._apply('deal', '玩家1', '9', event_id='fixed-id')
        prior = c.ledger
        before = prior.to_list()
        again = c._apply('deal', '玩家1', '9', event_id='fixed-id')
        self.assertEqual(again.to_dict(), event.to_dict())
        again.payload['rank'] = '8'
        self.assertEqual(prior.to_list(), before)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)

    def test_same_id_different_card_rejected_without_state_change(self):
        c = self.ctrl
        c._apply('deal', '玩家1', '9', event_id='fixed-id')
        before = c.ledger.to_list()
        with self.assertRaises(Exception):
            c._apply('deal', '玩家1', '8', event_id='fixed-id')
        self.assertEqual(c.ledger.to_list(), before)
        self.assertEqual(c.store.load_ledger(c.session_id).to_list(), before)

    def test_new_read_frame_detects_same_sequence_suit_change(self):
        c = self.ctrl
        self.initial()
        with c.read_frame():
            first = c.read_prefix()
        seq = c.ledger.events[-1].seq
        c.ledger.events[-2].payload['suit'] = 'C'
        with c.read_frame():
            second = c.read_prefix()
        self.assertEqual(c.ledger.events[-1].seq, seq)
        self.assertNotEqual(first.prefix_digest, second.prefix_digest)


if __name__ == '__main__':
    unittest.main()
