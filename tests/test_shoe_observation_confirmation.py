"""A current shoe's missing observations can be confirmed without editing rules."""
import copy
from dataclasses import asdict
import json
import unittest
import tempfile
from pathlib import Path

from blackjack_lab.ledger.events import CORRECTION, SHOE_CREATED
from blackjack_lab.ledger.ledger import EventLedger, LedgerError
from blackjack_lab.ui.table_modes import bclc_rules


def frozen_event(event):
    return json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


class ShoeObservationConfirmationTests(unittest.TestCase):
    def test_controller_confirmation_preserves_recording_position_and_next_card_undo(self):
        from blackjack_lab.ui.controller import SessionController
        with tempfile.TemporaryDirectory() as tmp:
            ctrl = SessionController(Path(tmp) / 'fixture.db')
            try:
                ctrl.new_shoe(bclc_rules())
                ctrl.start_round(['玩家1'], my_seat='玩家1', simple_hole=True)
                for rank in ('8', '6', '7'):
                    slot = ctrl.entry_plan.slot()
                    ctrl.deal_shown(slot.seat, rank, initial_slot_id=slot.slot_id)
                before = ctrl.ledger.to_list()
                plan = ctrl.entry_plan.to_dict()
                ctrl.confirm_complete_unburned_shoe('用户确认完整8副新靴且零烧牌', ctrl.context_token)
                self.assertEqual(ctrl.ledger.to_list()[:-1], before)
                after = ctrl.entry_plan.to_dict()
                for field in plan.keys() - {'ledger_seq', 'ledger_digest'}:
                    self.assertEqual(after[field], plan[field])
                self.assertFalse(ctrl.entry_plan.paused)
                self.assertFalse(ctrl.entry_plan.input_paused)
                self.assertTrue(ctrl.current_rules().start_from_new_shoe)
                self.assertEqual(ctrl.current_rules().initial_burn_count, 0)
                ctrl.deal_shown('玩家1', '2')
                ctrl.undo_last()
                self.assertEqual(ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['8', '7'])
                self.assertTrue(ctrl.current_rules().start_from_new_shoe)
            finally:
                ctrl.close()

    def ledger(self, **observations):
        rules = bclc_rules()
        for name, value in observations.items():
            setattr(rules, name, value)
        ledger = EventLedger('confirmed-observation-fixture')
        ledger.start_session('synthetic shoe observation fixture')
        created = ledger.create_shoe(rules)
        ledger.start_round(['玩家1'])
        ledger.deal('玩家1', '8')
        ledger.deal('庄家', '6')
        ledger.deal('玩家1', '7')
        ledger.deal('庄家', hidden=True)
        return ledger, created

    def snapshot(self, ledger):
        rules = ledger.replay().current.rules.snapshot()
        rules.start_from_new_shoe = True
        rules.burn_cards_known = True
        rules.initial_burn_count = 0
        return rules.to_json()

    def test_confirmation_changes_only_missing_observations_and_preserves_original_bytes(self):
        ledger, created = self.ledger()
        before = ledger.to_list()
        original = frozen_event(created)
        state = ledger.replay().current
        rules_before = asdict(state.rules)
        remaining, physical = state.shoe.remaining.copy(), state.shoe.physical_remaining()
        correction = ledger.correct(created.event_id, {'rules_snapshot': self.snapshot(ledger)},
                                    '用户明确确认该靴完整8副，初始零烧牌')
        self.assertEqual(correction.etype, CORRECTION)
        self.assertEqual(correction.payload['target_etype'], SHOE_CREATED)
        self.assertEqual(set(correction.payload['payload_fix']), {'rules_snapshot'})
        self.assertEqual(ledger.to_list()[:len(before)], before)
        self.assertEqual(frozen_event(ledger._find(created.event_id)), original)
        restored = EventLedger.from_list(ledger.session_id, ledger.to_list())
        current = restored.replay().current
        expected = dict(rules_before, start_from_new_shoe=True, burn_cards_known=True,
                        initial_burn_count=0)
        self.assertEqual(asdict(current.rules), expected)
        self.assertEqual(current.shoe.remaining, remaining)
        self.assertEqual(current.shoe.physical_remaining(), physical)
        self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['8', '7'])
        earlier = restored.replay(through_seq=correction.seq - 1).current
        self.assertIsNone(earlier.rules.start_from_new_shoe)
        self.assertIsNone(earlier.rules.burn_cards_known)
        self.assertIsNone(earlier.rules.initial_burn_count)
        self.assertEqual(frozen_event(restored._find(created.event_id)), original)

    def test_table_rules_decks_and_conflicting_observations_are_rejected_on_append_and_import(self):
        ledger, created = self.ledger()
        before = ledger.to_list()
        confirmed = json.loads(self.snapshot(ledger))
        template_ledger = EventLedger.from_list(ledger.session_id, before)
        template = template_ledger.correct(created.event_id, {'rules_snapshot': json.dumps(confirmed)},
                                           '用户确认完整8副零烧牌').to_dict()
        changes = (
            ('n_decks', 6), ('dealer_soft17', 'H17'), ('double_after_split', True),
            ('blackjack_payout', [6, 5]), ('max_split_hands', 4), ('version', 3),
            ('profile_id', 'changed-profile'), ('rule_source', 'changed rule source'),
            ('confirm_status', '未确认'), ('start_from_new_shoe', False),
            ('initial_burn_count', 1),
        )
        for name, value in changes:
            with self.subTest(field=name):
                data = dict(confirmed, **{name: value})
                text = json.dumps(data, ensure_ascii=False)
                with self.assertRaises(LedgerError):
                    ledger.correct(created.event_id, {'rules_snapshot': text}, 'attempted change')
                forged = copy.deepcopy(template)
                forged['payload']['payload_fix']['rules_snapshot'] = text
                with self.assertRaises(LedgerError):
                    EventLedger.from_list(ledger.session_id, before + [forged])
                self.assertEqual(ledger.to_list(), before)
        for fix in ({'n_decks': 8}, {'rules_snapshot': self.snapshot(ledger), 'shoe_id': 'other'}):
            with self.subTest(fix=fix), self.assertRaises(LedgerError):
                ledger.correct(created.event_id, fix, 'not an observation-only confirmation')
        missing_field = dict(confirmed); del missing_field['cut_shuffle_note']
        with self.assertRaises(LedgerError):
            ledger.correct(created.event_id, {'rules_snapshot': json.dumps(missing_field)}, 'incomplete rules')
        for observations in ({'start_from_new_shoe': False},
                             {'burn_cards_known': False},
                             {'burn_cards_known': True, 'initial_burn_count': 2}):
            with self.subTest(existing=observations):
                other, shoe = self.ledger(**observations)
                with self.assertRaises(LedgerError):
                    other.correct(shoe.event_id, {'rules_snapshot': self.snapshot(other)}, 'conflicting statement')
        with self.assertRaises(LedgerError):
            ledger.correct(created.event_id, {'rules_snapshot': self.snapshot(ledger)})
        ledger.correct(created.event_id, {'rules_snapshot': self.snapshot(ledger)}, 'explicit confirmation')
        with self.assertRaises(LedgerError):
            ledger.correct(created.event_id, {'rules_snapshot': self.snapshot(ledger)}, 'duplicate confirmation')
        closed, old_shoe = self.ledger()
        closed.end_round(settle=False, reason='synthetic unclosed cards', observation_status='unknown')
        closed.end_shoe()
        with self.assertRaises(LedgerError):
            closed.correct(old_shoe.event_id, {'rules_snapshot': self.snapshot(closed)}, 'closed shoe statement')
        closed.create_shoe(bclc_rules())
        with self.assertRaises(LedgerError):
            closed.correct(old_shoe.event_id, {'rules_snapshot': self.snapshot(closed)}, 'another shoe statement')

    def test_undo_restores_unknown_observations_without_rewriting_shoe_or_cards(self):
        ledger, created = self.ledger()
        before = ledger.to_list()
        original = frozen_event(created)
        remaining = ledger.replay().current.shoe.remaining.copy()
        correction = ledger.correct(created.event_id, {'rules_snapshot': self.snapshot(ledger)},
                                    '用户明确确认完整8副零烧牌')
        undo = ledger.undo_last('撤回观察确认')
        self.assertEqual(undo.payload['target_event_id'], correction.event_id)
        self.assertEqual(ledger.to_list()[:len(before)], before)
        restored = EventLedger.from_list(ledger.session_id, ledger.to_list())
        current = restored.replay().current
        self.assertIsNone(current.rules.start_from_new_shoe)
        self.assertIsNone(current.rules.burn_cards_known)
        self.assertIsNone(current.rules.initial_burn_count)
        self.assertEqual(current.shoe.remaining, remaining)
        self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['8', '7'])
        self.assertEqual(frozen_event(restored._find(created.event_id)), original)
        confirmed_prefix = restored.replay(through_seq=correction.seq).current
        self.assertTrue(confirmed_prefix.rules.start_from_new_shoe)
        self.assertTrue(confirmed_prefix.rules.burn_cards_known)
        self.assertEqual(confirmed_prefix.rules.initial_burn_count, 0)


if __name__ == '__main__':
    unittest.main()
