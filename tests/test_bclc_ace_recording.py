"""The user's BCLC Ace-result convention is one durable input and one undo."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.table import ACTION_STAND, ACTION_SPLIT, ACTION_DOUBLE
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.recording_commands import RecordingInputRejected
from blackjack_lab.ui.table_modes import bclc_rules


class BclcAceRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.serial = 0

    def prepare(self, *, cards=None, seats=('玩家1',), simple=True):
        self.serial += 1
        ctrl = SessionController(Path(self.tmp.name) / f'ace-{self.serial}.db')
        self.addCleanup(ctrl.close)
        ctrl.new_shoe(bclc_rules())
        ctrl.start_round(list(seats), my_seat=seats[-1], deal_direction='reverse', simple_hole=simple)
        cards = cards or {'玩家1': ('8', '8')}
        while not ctrl.entry_plan.initial_complete():
            slot = ctrl.entry_plan.slot()
            if slot.seat == '庄家':
                if slot.expected_face == 'hidden':
                    ctrl.deal_hidden('庄家')
                else:
                    ctrl.deal_shown('庄家', 'A', initial_slot_id=slot.slot_id)
            else:
                ctrl.deal_shown(slot.seat, cards[slot.seat][slot.ordinal - 1], initial_slot_id=slot.slot_id)
        self.assertTrue(ctrl.bclc_ace_input_ready())
        return ctrl

    @staticmethod
    def next_options(ctrl):
        plan = ctrl.entry_plan
        return dict(participants=list(plan.participating_seats), my_seat=plan.my_seat,
                    deal_direction=plan.deal_direction, simple_hole=plan.simple_hole)

    def test_ten_ranks_reveal_original_bj_and_keep_automatic_round_group_undo(self):
        for rank in ('T', '10', 'J', 'Q', 'K'):
            with self.subTest(rank=rank):
                ctrl = self.prepare()
                hole = ctrl.entry_plan.filled_slots[ctrl.entry_plan.hole_slot().slot_id]
                before, plan = ctrl.ledger.to_list(), copy.deepcopy(ctrl.entry_plan.to_dict())
                remaining = ctrl.state().current.shoe.physical_remaining()
                result = ctrl.record_bclc_ace_input('rank', rank=rank, auto_next=self.next_options(ctrl))
                self.assertTrue(result['round_changed'])
                self.assertEqual(result['seat'], '庄家')
                self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
                reveal, ended, started = ctrl.ledger.events[-3:]
                self.assertEqual([reveal.etype, ended.etype, started.etype],
                                 ['CARD_REVEALED', 'ROUND_ENDED', 'ROUND_STARTED'])
                self.assertEqual(reveal.payload['target_event_id'], hole)
                self.assertEqual(reveal.payload['rank'], rank)
                self.assertEqual(ctrl.state().current.shoe.physical_remaining(), remaining)
                self.assertFalse(any(event.etype == 'PEEK_NEGATIVE' for event in ctrl.ledger.events))
                ctrl.undo_last()
                restored = ctrl.entry_plan.to_dict()
                restored['ledger_seq'], restored['ledger_digest'] = plan['ledger_seq'], plan['ledger_digest']
                self.assertEqual(restored, plan)
                self.assertTrue(ctrl.bclc_ace_input_ready())
                self.assertIn(hole, ctrl.state().current.unresolved)
        # An already recorded manual round keeps its original hole protocol;
        # even with automatic next-round disabled, undo returns to Ace waiting.
        ctrl = self.prepare(simple=False)
        hole = ctrl.entry_plan.filled_slots[ctrl.entry_plan.hole_slot().slot_id]
        ctrl.record_bclc_ace_input('rank', rank='T')
        ctrl.undo_last()
        self.assertTrue(ctrl.bclc_ace_input_ready())
        self.assertIn(hole, ctrl.state().current.unresolved)

    def test_non_ten_is_saved_for_first_effective_player_with_actual_negative_peek(self):
        ctrl = self.prepare(seats=('玩家7', '玩家3', '玩家1'),
                            cards={'玩家7': ('T', 'A'), '玩家3': ('5', '6'), '玩家1': ('9', '8')})
        before = ctrl.ledger.to_list()
        result = ctrl.record_bclc_ace_input('rank', rank='2')
        self.assertEqual(result['seat'], '玩家3')  # The first seat already has natural 21.
        self.assertFalse(result['round_changed'])
        self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
        peek, card = ctrl.ledger.events[-2:]
        self.assertEqual([peek.etype, card.etype], ['PEEK_NEGATIVE', 'CARD_DEALT'])
        self.assertEqual(card.payload['seat'], '玩家3')
        receipt = json.loads(card.evidence)['bclc_ace_input_v1']
        self.assertEqual(receipt['peek_event_id'], peek.event_id)
        self.assertEqual(ctrl.state().current.table.players['玩家3'].hands[0].ranks, ['5', '6', '2'])
        self.assertEqual(ctrl.state().current.table.players['玩家7'].hands[0].ranks, ['T', 'A'])
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertFalse(any(event.etype == 'CARD_REVEALED' for event in ctrl.ledger.events))
        self.assertFalse(ctrl.bclc_ace_input_ready())

    def test_legal_stand_split_and_double_share_one_peek_operation_transaction(self):
        for action in (ACTION_STAND, ACTION_SPLIT, ACTION_DOUBLE):
            with self.subTest(action=action):
                ctrl = self.prepare()
                before = ctrl.ledger.to_list()
                append = ctrl.store.append_validated
                with patch.object(ctrl.store, 'append_validated', wraps=append) as committed:
                    result = ctrl.record_bclc_ace_input('action', action=action)
                self.assertEqual(committed.call_count, 1)
                self.assertEqual(result['seat'], '玩家1')
                self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
                self.assertEqual([event.etype for event in ctrl.ledger.events[-2:]], ['PEEK_NEGATIVE', 'PLAYER_ACTION'])
                self.assertEqual(ctrl.ledger.events[-1].payload['action'], action)
                self.assertTrue(ctrl.state().current.table.dealer_hole_checked_negative)
                self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
                self.assertIsNotNone(ctrl._bclc_ace_undo_group())

    def test_illegal_preview_rejects_input_without_publishing_negative_peek(self):
        ctrl = self.prepare(cards={'玩家1': ('9', '8')})
        before, plan, token = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict(), ctrl.context_token
        with patch.object(ctrl.store, 'append_validated', wraps=ctrl.store.append_validated) as committed:
            with self.assertRaises(RecordingInputRejected):
                ctrl.record_bclc_ace_input('action', action=ACTION_SPLIT)
        self.assertEqual(committed.call_count, 0)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(ctrl.context_token, token)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertFalse(ctrl.state().current.table.dealer_hole_checked_negative)

    def test_second_insert_failure_rolls_back_pair_and_is_never_a_rejected_key(self):
        ctrl = self.prepare()
        before, plan, token = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict(), ctrl.context_token
        original = ctrl.store._insert
        inserted = []
        def failing(event):
            if event.seq > before[-1]['seq']:
                inserted.append(event.event_id)
                if len(inserted) == 2:
                    raise sqlite3.OperationalError('synthetic second insert failure')
            return original(event)
        with patch.object(ctrl.store, '_insert', side_effect=failing):
            with self.assertRaisesRegex(sqlite3.OperationalError, 'second insert'):
                ctrl.record_bclc_ace_input('rank', rank='2')
        self.assertEqual(len(inserted), 2)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(ctrl.context_token, token)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertTrue(ctrl.bclc_ace_input_ready())

    def test_one_undo_restores_peek_wait_and_keeps_original_history_across_restart(self):
        for action in (None, ACTION_SPLIT):
            with self.subTest(action=action):
                ctrl = self.prepare()
                before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
                if action:
                    ctrl.record_bclc_ace_input('action', action=action)
                else:
                    ctrl.record_bclc_ace_input('rank', rank='2')
                peek, event = ctrl.ledger.events[-2:]
                combined = ctrl.ledger.to_list()
                ctrl.undo_last()
                self.assertEqual(ctrl.ledger.to_list()[:-2], combined)
                self.assertEqual([undo.payload['target_event_id'] for undo in ctrl.ledger.events[-2:]],
                                 [event.event_id, peek.event_id])
                restored = ctrl.entry_plan.to_dict()
                restored['ledger_seq'], restored['ledger_digest'] = plan['ledger_seq'], plan['ledger_digest']
                self.assertEqual(restored, plan)
                self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
                self.assertFalse(ctrl.state().current.table.dealer_hole_checked_negative)
                self.assertTrue(ctrl.bclc_ace_input_ready())
                recovered = SessionController.recover(ctrl.store.db_path, ctrl.session_id)
                try:
                    self.assertEqual(recovered.ledger.to_list(), ctrl.ledger.to_list())
                    self.assertEqual(recovered.entry_plan.to_dict(), ctrl.entry_plan.to_dict())
                    self.assertTrue(recovered.bclc_ace_input_ready())
                finally:
                    recovered.close()


if __name__ == '__main__':
    unittest.main()
