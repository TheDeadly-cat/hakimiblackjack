"""Real Windows native solver and durable history for the two same-value profiles."""
import json
import os
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import (same_value_split_research_rules,
    same_value_das_research_rules, split_research_rules)
from blackjack_lab.core.table import ACTION_SPLIT, ACTION_DOUBLE, ACTION_STAND, TableError
from blackjack_lab.ui.controller import SessionController
from tests.test_analysis_integration import example


@unittest.skipUnless(os.name == 'nt', 'Real Windows native engine acceptance')
class TestSameValueAcceptance(unittest.TestCase):
    def assert_native_result(self, snapshot, result):
        self.assertEqual(result['status'], 'available', result.get('reason'))
        self.assertEqual(result['backend'], 'windows-dotnet-framework-exact')
        self.assertEqual(len(result['backend_source_sha256']), 64)
        self.assertEqual(len(result['backend_binary_sha256']), 64)
        for action in snapshot.legal_actions:
            item = result['actions'][action]
            self.assertEqual(item['status'], 'available', (action, item))
            dist = item['net_distribution']
            self.assertAlmostEqual(sum(dist.values()), 1, delta=1e-10)
            self.assertAlmostEqual(sum(float(k) * p for k, p in dist.items()), item['ev'], delta=1e-10)
        return result

    def assert_same_numbers(self, left, right):
        self.assertEqual(set(left['actions']), set(right['actions']))
        for action, item in left['actions'].items():
            other = right['actions'][action]
            self.assertEqual(item['status'], other['status'])
            if item['status'] != 'available':
                continue
            self.assertAlmostEqual(item['ev'], other['ev'], delta=1e-10)
            for field in ('net_distribution', 'joint_distribution'):
                self.assertEqual(set(item.get(field, {})), set(other.get(field, {})))
                for outcome, probability in item.get(field, {}).items():
                    self.assertAlmostEqual(probability, other[field][outcome], delta=1e-10)

    def test_18_native_variants_and_18_immutable_history_recomputations(self):
        for n in (6, 7, 8):
            for factory in (same_value_split_research_rules, same_value_das_research_rules):
                reference = None
                for cards in (('T', 'T'), ('K', 'Q'), ('10', 'J')):
                    with self.subTest(decks=n, profile=factory.__name__, cards=cards), tempfile.TemporaryDirectory() as folder:
                        rules = factory(n)
                        ledger = example(n=n, cards=cards, up='6', rules=rules)
                        snapshot = build_input(ledger, '玩家1')
                        self.assertEqual(snapshot.n_decks, n)
                        self.assertEqual(json.loads(snapshot.rules_json)['profile_id'], rules.profile_id)
                        result = self.assert_native_result(snapshot, calculate(snapshot))
                        self.assertEqual(len(result['actions']['split']['net_distribution']),
                                         9 if rules.double_after_split else 5)
                        self.assertEqual(len(result['actions']['split']['joint_distribution']),
                                         25 if rules.double_after_split else 9)
                        if reference is not None:
                            self.assert_same_numbers(reference, result)
                        reference = result
                        db = Path(folder) / 'acceptance.db'
                        ctrl = SessionController(db)
                        ctrl.store.save_ledger(ledger)
                        saved = ctrl.analysis_store.save(result)
                        path = ctrl.analysis_store.directory / (saved['snapshot_id'] + '.json')
                        original = path.read_bytes()
                        ctrl.close()
                        recovered = SessionController.recover(db, ledger.session_id)
                        try:
                            loaded = recovered.analysis_store.load(saved['snapshot_id'])
                            rebuilt = recovered.recompute_input(loaded)
                            self.assertEqual(rebuilt.rules_json, snapshot.rules_json)
                            self.assertEqual(rebuilt.rules_digest, snapshot.rules_digest)
                            recomputed = self.assert_native_result(rebuilt, calculate(rebuilt))
                            self.assert_same_numbers(result, recomputed)
                            newer = recovered.analysis_store.save(recomputed, saved['snapshot_id'])
                            self.assertNotEqual(saved['snapshot_id'], newer['snapshot_id'])
                            self.assertEqual(newer['recomputed_from'], saved['snapshot_id'])
                            self.assertEqual(path.read_bytes(), original)
                        finally:
                            recovered.close()

    def test_six_split_rounds_draw_ownership_double_one_card_and_settlement(self):
        for n in (6, 7, 8):
            for factory in (same_value_split_research_rules, same_value_das_research_rules):
                with self.subTest(decks=n, profile=factory.__name__), tempfile.TemporaryDirectory() as folder:
                    ctrl = SessionController(Path(folder) / 'round.db')
                    try:
                        rules = factory(n)
                        ctrl.new_shoe(rules)
                        ctrl.start_round(['玩家1'])
                        for seat, rank in (('玩家1', 'K'), ('庄家', '6'), ('玩家1', 'Q')):
                            ctrl.deal_shown(seat, rank)
                        hidden = ctrl.deal_hidden('庄家')
                        first = ctrl.state().current.table.players['玩家1'].hands[0].hand_id
                        ctrl.player_action('玩家1', first, ACTION_SPLIT)
                        hands = ctrl.state().current.table.players['玩家1'].hands
                        second = hands[1].hand_id
                        self.assertEqual([h.ranks for h in hands], [['K'], ['Q']])
                        ctrl.deal_shown('玩家1', '2', first)
                        if rules.double_after_split:
                            ctrl.player_action('玩家1', first, ACTION_DOUBLE)
                            pending = build_input(ctrl.ledger, '玩家1', first)
                            self.assertEqual(pending.legal_actions, ('deal',))
                            computed = self.assert_native_result(pending, calculate(pending))
                            self.assertEqual(computed['current_investment'], 3)
                            ctrl.deal_shown('玩家1', '9', first)
                            before = ctrl.ledger.to_list()
                            with self.assertRaises(TableError):
                                ctrl.deal_shown('玩家1', '3', first)
                            self.assertEqual(ctrl.ledger.to_list(), before)
                        else:
                            with self.assertRaises(TableError):
                                ctrl.player_action('玩家1', first, ACTION_DOUBLE)
                            ctrl.player_action('玩家1', first, ACTION_STAND)
                        self.assertEqual(ctrl.entry_plan.continuation_hand_id, second)
                        ctrl.deal_shown('玩家1', '8', second)
                        ctrl.player_action('玩家1', second, ACTION_STAND)
                        before = ctrl.state().current.shoe.physical_remaining()
                        ctrl.reveal(hidden.event_id, 'T')
                        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), before)
                        ctrl.deal_shown('庄家', '5')
                        _, settlements = ctrl.end_round()
                        self.assertEqual(sum(r['net_units'] for r in settlements),
                                         -1 if rules.double_after_split else -2)
                        self.assertEqual(sum(h.bet_units for h in ctrl.state().current.table.players['玩家1'].hands),
                                         3 if rules.double_after_split else 2)
                        self.assertEqual(ctrl.current_rules().profile_id, rules.profile_id)
                        ctrl.start_round(['玩家1'])
                        self.assertEqual(ctrl.entry_plan.cursor_slot_id, 's1')
                    finally:
                        ctrl.close()

    def test_original_same_rank_pair_policy_is_preserved(self):
        rules = split_research_rules()
        for cards in (('T', 'T'), ('K', 'Q'), ('10', 'J')):
            ledger = example(cards=cards, up='6', rules=rules)
            table = ledger.replay().current.table
            hand = table.players['玩家1'].hands[0]
            self.assertFalse(table.action_states('玩家1', hand.hand_id)[ACTION_SPLIT].allowed)
        ledger = example(cards=('K', 'K'), up='6', rules=rules)
        snapshot = build_input(ledger, '玩家1')
        self.assertIn('split', snapshot.legal_actions)
        self.assertEqual(json.loads(snapshot.rules_json)['split_match'], 'same_rank')
