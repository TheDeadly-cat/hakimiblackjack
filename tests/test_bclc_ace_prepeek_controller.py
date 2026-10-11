"""Controller integration keeps Ace-check evaluation distinct from observed play."""
import copy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.ace_prepeek_contracts import AcePrepeekInput, ACE_PREPEEK_INPUT_SCHEMA
from blackjack_lab.analysis.contracts import InputUnavailable, INPUT_SCHEMA
from blackjack_lab.ledger.ledger import LedgerError
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.table_modes import bclc_rules


class BclcAcePrepeekControllerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.serial = 0

    def prepare(self, *, player=('T', 'A'), dealer='A'):
        self.serial += 1
        ctrl = SessionController(Path(self.tmp.name) / f'prepeek-{self.serial}.db')
        self.addCleanup(ctrl.close)
        rules = replace(bclc_rules(), start_from_new_shoe=True, burn_cards_known=True,
                        initial_burn_count=0)
        ctrl.new_shoe(rules)
        ctrl.start_round(['玩家1'], simple_hole=True)
        ctrl.deal_shown('玩家1', player[0])
        ctrl.deal_shown('庄家', dealer)
        ctrl.deal_shown('玩家1', player[1])
        self.assertTrue(ctrl.entry_plan.initial_complete())
        return ctrl

    def test_natural_21_has_only_prepeek_terminal_evaluation_and_never_adds_peek(self):
        ctrl = self.prepare()
        before, plan = ctrl.ledger.to_list(), copy.deepcopy(ctrl.entry_plan.to_dict())
        snapshot = ctrl.current_decision_input('玩家1')
        self.assertIsInstance(snapshot, AcePrepeekInput)
        snapshot.validate()
        self.assertTrue(snapshot.natural_blackjack)
        self.assertEqual(snapshot.legal_actions, ())
        self.assertFalse(snapshot.peek_negative)
        self.assertIs(snapshot, ctrl.current_decision_input('玩家1'))
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertFalse(ctrl.state().current.table.dealer_hole_checked_negative)
        ctrl.peek_negative()
        with self.assertRaises(InputUnavailable) as refused:
            ctrl.current_decision_input('玩家1')
        self.assertEqual(refused.exception.code, 'TOTAL_21')

    def test_ordinary_21_keeps_the_original_no_action_gate(self):
        ctrl = self.prepare(dealer='6')
        with self.assertRaises(InputUnavailable) as refused:
            ctrl.current_decision_input('玩家1')
        self.assertEqual(refused.exception.code, 'TOTAL_21')

    def test_historical_rebuild_uses_original_prefix_before_actual_negative_check(self):
        ctrl = self.prepare(player=('9', '8'))
        original = ctrl.current_decision_input('玩家1')
        saved = {'result': {'input': original.to_dict()}}
        saved_before = copy.deepcopy(saved)
        ctrl.peek_negative()
        after = ctrl.current_decision_input('玩家1')
        self.assertNotEqual(after.schema, ACE_PREPEEK_INPUT_SCHEMA)
        live_before = ctrl.ledger.to_list()
        rebuilt = ctrl.recompute_input(saved)
        self.assertIsInstance(rebuilt, AcePrepeekInput)
        self.assertEqual(rebuilt.to_dict(), original.to_dict())
        self.assertFalse(rebuilt.peek_negative)
        self.assertEqual(saved, saved_before)
        self.assertEqual(ctrl.ledger.to_list(), live_before)

    def test_recompute_rejects_prefix_mismatch_before_build_and_stage_substitution(self):
        ctrl = self.prepare(player=('9', '8'))
        original = ctrl.current_decision_input('玩家1')
        saved = {'result': {'input': original.to_dict()}}
        forged = copy.deepcopy(saved)
        forged['result']['input']['prefix_digest'] = '0' * 64
        with patch('blackjack_lab.ui.controller.build_input') as builder:
            with self.assertRaisesRegex(LedgerError, '前缀摘要'):
                ctrl.recompute_input(forged)
        builder.assert_not_called()
        with patch('blackjack_lab.ui.controller.build_input',
                   return_value=SimpleNamespace(schema=INPUT_SCHEMA)):
            with self.assertRaisesRegex(LedgerError, '检查后的信息身份'):
                ctrl.recompute_input(saved)


if __name__ == '__main__':
    unittest.main()
