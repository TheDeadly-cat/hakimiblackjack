import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from blackjack_lab.analysis.contracts import InputUnavailable, canonical, research_rules
from blackjack_lab.analysis.seat_scenario import MODEL, NOTE
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import same_value_das_research_rules
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.analysis_panel import format_result


class TestSeatScenario(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctrl = SessionController(Path(self.tmp.name) / 'seats.db')
        self.addCleanup(self.ctrl.close)

    def start(self, split=False):
        self.ctrl.new_shoe(same_value_das_research_rules(8) if split else research_rules(8))
        self.ctrl.start_round(['玩家1', '玩家2', '玩家3'])
        for seat, ranks in [('玩家1', ('8', '8') if split else ('T', '6')),
                            ('玩家2', ('T', '7')), ('玩家3', ('T', '9'))]:
            for rank in ranks:
                self.ctrl.deal_shown(seat, rank)
        self.ctrl.deal_shown('庄家', '6')
        self.ctrl.deal_hidden('庄家')

    def test_explicit_scope_keeps_every_known_card_and_original_ledger(self):
        self.start()
        before = self.ctrl.ledger.to_list()
        with self.assertRaises(InputUnavailable) as rejected:
            self.ctrl.analysis_input('玩家1')
        self.assertEqual(rejected.exception.code, 'SINGLE_PLAYER_ONLY')
        snapshots = [self.ctrl.analysis_input(f'玩家{i}', other_players_stand=True) for i in (1, 2, 3)]
        expected = [32] * 9 + [128]
        for index in (9, 5, 9, 6, 9, 8, 5):
            expected[index] -= 1
        for snapshot in snapshots:
            snapshot.validate()
            self.assertEqual(snapshot.counts, tuple(expected))
            self.assertEqual(snapshot.physical_remaining, 408)
            self.assertIn(MODEL, snapshot.support_scope)
            info = json.loads(snapshot.information_json)['seat_scenario']
            self.assertEqual(info['other_future_draws'], 0)
            self.assertEqual(len(info['others']), 2)
        self.assertEqual(len({s.input_digest for s in snapshots}), 3)
        self.assertEqual(self.ctrl.ledger.to_list(), before)

    def test_same_dealer_distribution_different_players_and_historical_recompute(self):
        self.start()
        results = [calculate(self.ctrl.analysis_input(f'玩家{i}', other_players_stand=True)) for i in (1, 2, 3)]
        self.assertTrue(all(r['status'] == 'available' for r in results))
        dealers = [r['probabilities']['dealer_terminal_if_stand_now'] for r in results]
        self.assertEqual(dealers[0], dealers[1])
        self.assertEqual(dealers[1], dealers[2])
        profits = [sum(p for net, p in r['actions']['stand']['net_distribution'].items() if float(net) > 0) for r in results]
        self.assertLess(profits[0], profits[2])
        saved = self.ctrl.analysis_store.save(results[0])
        original = self.ctrl.recompute_input(saved)
        self.ctrl.deal_shown('玩家1', '2')
        current = self.ctrl.analysis_input('玩家2', other_players_stand=True)
        self.assertEqual(current.counts[1], original.counts[1] - 1)
        self.assertNotEqual(current.prefix_digest, original.prefix_digest)
        self.assertEqual(self.ctrl.recompute_input(saved).input_digest, original.input_digest)
        self.assertIn(NOTE, format_result(results[0], historical=True))

    def test_unknown_or_mandatory_other_draw_is_not_assumed_away(self):
        self.start()
        hand = self.ctrl.state().current.table.players['玩家2'].hands[0]
        self.ctrl.player_action('玩家2', hand.hand_id, '加倍')
        with self.assertRaises(InputUnavailable) as pending:
            self.ctrl.analysis_input('玩家1', other_players_stand=True)
        self.assertEqual(pending.exception.code, 'OTHER_DRAW_PENDING')
        self.ctrl.deal_shown('玩家2', '2')
        self.ctrl.analysis_input('玩家1', other_players_stand=True).validate()
        self.ctrl.mark_gap('未确认漏牌')
        with self.assertRaises(InputUnavailable) as gap:
            self.ctrl.analysis_input('玩家1', other_players_stand=True)
        self.assertEqual(gap.exception.code, 'RECORD_GAP')

    def test_scenario_identity_cannot_be_silently_relabelled_as_single_player(self):
        self.start()
        snapshot = self.ctrl.analysis_input('玩家1', other_players_stand=True)
        with self.assertRaises(ValueError):
            replace(snapshot, support_scope=snapshot.support_scope.replace(MODEL, 'single-player')).validate()
        info = json.loads(snapshot.information_json)
        info['seat_scenario']['other_future_draws'] = 1
        with self.assertRaises(ValueError):
            replace(snapshot, information_json=canonical(info)).validate()
        info = json.loads(snapshot.information_json)
        info['seat_scenario']['others'][0]['hands'][0]['cards'][0]['rank'] = '?'
        with self.assertRaises(ValueError):
            replace(snapshot, information_json=canonical(info)).validate()

    def test_split_target_preserves_sequential_das_model_and_conditional_scope(self):
        self.start(split=True)
        snapshot = self.ctrl.analysis_input('玩家1', other_players_stand=True)
        snapshot.validate()
        self.assertIn(MODEL, snapshot.support_scope)
        self.assertIn('DAS-non-ace', snapshot.support_scope)
        self.assertEqual(json.loads(snapshot.single_input().information_json)['seat_scenario']['model'], MODEL)


if __name__ == '__main__':
    unittest.main()
