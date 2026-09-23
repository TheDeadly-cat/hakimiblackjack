import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules, same_value_das_research_rules
from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.native_backend import solve_presplit_native, solve_native
from blackjack_lab.analysis.opening_service import estimate_counts
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_CONTINUATION, MODE_PEEK_WAIT
from blackjack_lab.ui.ev_display import ev_sign, decision_evs
from tests.analysis_reference import reference
from tests.das_split_reference import das_split_reference


class TestAceOnlyPeek(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ctrl = SessionController(Path(temp.name) / 'peek.db')
        self.addCleanup(self.ctrl.close)

    def initial(self, up='T', old=False):
        c = self.ctrl
        c.new_shoe((same_value_das_research_rules if old else ace_peek_das_research_rules)(8))
        c.start_round(['玩家1'], simple_hole=True)
        c.deal_shown('玩家1', '8')
        c.deal_shown('庄家', up)
        c.deal_shown('玩家1', '8')

    def test_ten_does_not_fabricate_negative_peek_and_preserves_bj_settlement(self):
        self.initial()
        c = self.ctrl
        snapshot = c.current_decision_input('玩家1')
        snapshot.validate()
        self.assertEqual(c.entry_plan.mode, MODE_CONTINUATION)
        self.assertFalse(snapshot.peek_negative)
        self.assertIn('double', snapshot.legal_actions)
        self.assertIn('split', snapshot.legal_actions)
        self.assertNotIn('surrender', snapshot.legal_actions)
        self.assertFalse(any(e.etype == 'PEEK_NEGATIVE' for e in c.ledger.events))
        c.player_action('玩家1', snapshot.hand_id, '加倍')
        c.deal_shown('玩家1', '2')
        hole = c.simple_dealer_route('庄家')
        c.reveal(hole, 'A')
        _, results = c.end_round()
        self.assertEqual(results[0]['net_units'], -2)
        with self.assertRaisesRegex(ValueError, '晚投降'):
            replace(snapshot, legal_actions=(*snapshot.legal_actions, 'surrender')).validate()

    def test_ace_still_waits_for_actual_check(self):
        self.initial('A')
        self.assertEqual(self.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        with self.assertRaises(InputUnavailable):
            self.ctrl.current_decision_input('玩家1')
        self.ctrl.peek_negative()
        self.ctrl.current_decision_input('玩家1').validate()
        self.assertEqual(self.ctrl.entry_plan.mode, MODE_CONTINUATION)

    def test_legacy_ten_peek_rule_keeps_its_original_requirement(self):
        self.initial(old=True)
        self.assertEqual(self.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        with self.assertRaises(InputUnavailable):
            self.ctrl.current_decision_input('玩家1')

    def test_all_decisions_show_sign_and_unavailable_is_not_zero(self):
        self.assertEqual([ev_sign(x) for x in (1, -1, 0, 1e-12)], ['正EV', '负EV', '零EV', '零EV'])
        result = dict(status='available', input={'seat': '玩家2'}, actions={
            'stand': dict(status='available', ev=.125), 'hit': dict(status='available', ev=-.123),
            'double': dict(status='available', ev=0), 'split': dict(status='unsupported'),
            'surrender': dict(status='inapplicable')})
        text = decision_evs(result)
        for expected in ('玩家2', '停牌 正EV +0.1250', '补牌 负EV -0.1230', '加倍 零EV +0.0000',
                         '分牌 未支持', '投降 不适用'):
            self.assertIn(expected, text)


class TestNoTenPeekMath(unittest.TestCase):
    def test_single_actions_match_independent_fraction_oracle_without_peek(self):
        cards = (1, 2, 8, 9, 10, 10, 10)
        counts = tuple(cards.count(v) for v in range(1, 11))
        for hand in ((10, 6), (1, 6), (8, 8), (1, 10)):
            actions = ('stand',) if hand == (1, 10) else ('stand', 'hit', 'double')
            exact = reference(cards, hand, 10, False, actions)
            for actual in (solve_counts(counts, hand, 10, False, actions),
                           solve_presplit_native(counts, hand, 10, False, actions)):
                self.assertAlmostEqual(actual['dealer_distribution']['blackjack'], 1/7)
                for action in actions:
                    self.assertAlmostEqual(actual['actions'][action]['ev'], float(exact['actions'][action]['ev']), delta=1e-10)

    def test_split_das_without_peek_matches_shared_shoe_fraction_oracle(self):
        cards = (1, 1, 8, 9, 10, 10, 10)
        counts = tuple(cards.count(v) for v in range(1, 11))
        for hands, options in [(((8,), (8,)), {}), (((8, 2), (8,)), {}),
                               (((8, 2, 10), (8, 3)), dict(active=1, stakes=(2, 1)))]:
            exact = das_split_reference(cards, hands, 10, peek=False, **options)
            actual = solve_native(counts, hands, 10, False, allow_das=True, **options)
            for action, value in exact.items():
                self.assertAlmostEqual(actual['actions'][action]['ev'], float(value['ev']), delta=1e-10)
                for pair, probability in value['joint_distribution'].items():
                    key = ','.join(map(str, pair))
                    self.assertAlmostEqual(actual['actions'][action]['joint_distribution'][key], float(probability), delta=1e-10)

    def test_opening_no_peek_retains_natural_payouts_and_uses_rule_in_input(self):
        # With only one ace, this declared replacement policy splits TT vs T.
        # Enumerate the ace's 32 equally likely positions independently:
        # player natural (2 positions) +1.5; dealer up -1; dealer hole -2;
        # either split draw (2 positions) +1; all later positions push.
        exact = (2 * 1.5 - 1 - 2 + 2 * 1) / 32
        result = estimate_counts((1, 0, 0, 0, 0, 0, 0, 0, 0, 31), peek_ten=False, samples=200_000)
        self.assertLess(result['interval'][0], exact)
        self.assertGreater(result['interval'][1], exact)
        self.assertEqual({i for i, n in enumerate(result['histogram']) if n}, {4, 6, 8, 10, 11})
        # With only the four initial cards there is no room for this split policy.
        with self.assertRaisesRegex(RuntimeError, 'INSUFFICIENT_CARDS'):
            estimate_counts((1, 0, 0, 0, 0, 0, 0, 0, 0, 3), peek_ten=False, samples=100)
        with self.assertRaises(ValueError):
            estimate_counts((32,) * 9 + (128,), peek_ten=1)


if __name__ == '__main__':
    unittest.main()
