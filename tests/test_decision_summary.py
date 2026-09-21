import copy
import os
import unittest

from blackjack_lab.analysis.decision_summary import summarize_result, format_summary_details
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.information import build_input
from tests.test_analysis_integration import example


def result(actions, legal=None, **fields):
    return dict(status='available', input={'seat': '玩家1', 'hand_id': 'h1', 'player_ranks': ['T', '6'],
                'dealer_up': 6, 'legal_actions': list(legal or actions), 'uncertain_actions': []},
                actions={name: dict(status='available', ev=sum(float(k)*p for k, p in dist.items()),
                                    net_distribution=dist) for name, dist in actions.items()},
                numerical_tolerance=1e-10, partial_comparison=False, **fields)


class TestDecisionSummary(unittest.TestCase):
    def test_lower_profit_probability_can_have_higher_ev(self):
        data = result({'stand': {-1: .55, 1: .45}, 'hit': {-1: .45, 0: .15, 1: .4},
                       'double': {-2: .55, 2: .45}, 'surrender': {-.5: 1}})
        original = copy.deepcopy(data)
        summary = summarize_result(data)
        self.assertEqual([c.action for c in summary.choices], ['hit', 'stand'])
        self.assertLess(summary.choices[0].profit, summary.choices[1].profit)
        self.assertAlmostEqual(summary.ev_gap, .05)
        self.assertIn('相对少亏', summary.message)
        self.assertEqual(len(summary.all_choices), 4)
        self.assertEqual(data, original)

    def test_total_distribution_counts_one_win_one_loss_as_push(self):
        data = result({'stand': {-2: .1, -1: .1, 0: .5, 1: .1, 2: .2},
                       'double': {-4: .05, -2: .1, -1: .05, 0: .4, 1: .1, 2: .1, 4: .2}})
        for action in data['actions'].values():
            action['joint_distribution'] = {'1,-1': .5}  # Must not sum marginal hand wins.
        summary = summarize_result(data)
        stand = next(c for c in summary.all_choices if c.action == 'stand')
        self.assertAlmostEqual(stand.profit, .3)
        self.assertAlmostEqual(stand.push, .5)
        self.assertAlmostEqual(stand.loss, .2)
        self.assertEqual(summary.choices[0].additional, 1)
        self.assertIn('分牌后合并两手', format_summary_details(summary))

    def test_tolerance_tie_and_three_way_tie_do_not_invent_rank(self):
        data = result({'stand': {-1: .5, 1: .5}, 'hit': {-1: .5-1e-11, 1: .5+1e-11},
                       'double': {-2: .5, 2: .5}})
        summary = summarize_result(data)
        self.assertTrue(summary.tied)
        self.assertEqual([c.rank_label for c in summary.choices], ['并列', '并列'])
        self.assertIn('还有并列动作', summary.message)

    def test_partial_comes_from_missing_and_uncertain_even_if_flag_wrong(self):
        data = result({'stand': {-1: .4, 1: .6}, 'hit': {-1: .5, 1: .5}}, legal=['stand', 'hit', 'split'])
        summary = summarize_result(data)
        self.assertTrue(summary.partial)
        self.assertEqual(summary.state, '部分比较')
        self.assertTrue(all('最佳' not in c.rank_label for c in summary.choices))
        self.assertIn('分牌', summary.message)
        data['input']['legal_actions'].remove('split')
        data['input']['uncertain_actions'] = ['split']
        self.assertTrue(summarize_result(data).partial)

    def test_runner_up_tie_is_disclosed_without_hiding_the_unique_best(self):
        data = result({'stand': {-1: .3, 1: .7}, 'hit': {-1: .5, 1: .5}, 'double': {-2: .5, 2: .5}})
        summary = summarize_result(data)
        self.assertEqual(summary.choices[0].rank_label, '最佳')
        self.assertEqual(summary.choices[1].rank_label, '次佳并列')
        self.assertIn('还有并列动作', summary.message)

    def test_single_action_and_forced_flow(self):
        summary = summarize_result(result({'stand': {1: 1}}))
        self.assertEqual(len(summary.choices), 1)
        self.assertEqual(summary.choices[0].rank_label, '唯一合法动作')
        for action, state in [('deal', '等待补牌'), ('complete', '等待庄家')]:
            summary = summarize_result(result({action: {0: 1}}))
            self.assertEqual(summary.state, state)
            self.assertEqual(summary.choices, ())

    def test_unavailable_invalid_missing_distribution_never_fills_zero(self):
        data = result({'stand': {-1: .4, 1: .6}})
        for status in ('timeout', 'failed', 'unsupported', 'stale', 'cancelled', 'pending'):
            item = {**data, 'status': status, 'reason': 'specific cause'}
            self.assertEqual(summarize_result(item).choices, ())
            self.assertEqual(summarize_result(item).message, 'specific cause')
        for dist in ({}, {'1': .5}, {'nan': 1}, {'1': -1}, None):
            item = copy.deepcopy(data)
            item['actions']['stand']['net_distribution'] = dist
            self.assertEqual(summarize_result(item).state, '需核对')
            self.assertEqual(summarize_result(item).choices, ())

    def test_historical_identity_and_split_hand_ordinal(self):
        data = result({'stand': {1: 1}})
        data['input'].update(hand_id='R1-P1-H7', active_hand_id='R1-P1-H7', hands=[
            {'hand_id': 'R1-P1-H5', 'ranks': ['8', '9']}, {'hand_id': 'R1-P1-H7', 'ranks': ['8', '3']}])
        summary = summarize_result(data, historical=True)
        self.assertTrue(summary.state.startswith('历史'))
        self.assertIn('第2手', summary.identity)
        self.assertNotIn('第7手', summary.identity)
        self.assertIn('不代表当前输入', summary.notes[0])
        data['input']['hand_id'] = 'R1-P1-H5'
        summary = summarize_result(data)
        self.assertIn('第2手  8 3', summary.identity)
        self.assertIn('查看第1手', summary.identity)

    @unittest.skipUnless(os.name == 'nt', 'Native split engine requires Windows')
    def test_real_split_and_das_total_probability_matches_all_action_results(self):
        from blackjack_lab.analysis.split_contracts import split_research_rules, das_research_rules
        for rules in (split_research_rules(), das_research_rules()):
            ledger = example(cards=('8', '8'), up='6', rules=rules)
            for stage in ('before', 'second'):
                if stage == 'second':
                    first = build_input(ledger, '玩家1').hand_id
                    ledger.player_action('玩家1', first, '分牌')
                    ledger.deal('玩家1', '9', hand_id=first)
                    ledger.player_action('玩家1', first, '停牌')
                    second = build_input(ledger, '玩家1').active_hand_id
                    ledger.deal('玩家1', '3', hand_id=second)
                data = calculate(build_input(ledger, '玩家1'))
                self.assertEqual(data['status'], 'available', data['reason'])
                summary = summarize_result(data)
                self.assertEqual({c.action for c in summary.all_choices}, set(data['input']['legal_actions']))
                for c in summary.all_choices:
                    raw = data['actions'][c.action]
                    self.assertEqual(c.ev, raw['ev'])
                    self.assertAlmostEqual(c.profit, sum(p for net, p in raw['net_distribution'].items() if float(net) > 0))
                    self.assertEqual(c.additional, raw['additional_investment'])
                from blackjack_lab.ui.analysis_panel import format_result
                self.assertIn('两手联合净收益分布', format_result(data))

    def test_real_single_service_distributions_and_all_legal_actions(self):
        data = calculate(build_input(example(cards=('T', '6'), up='6'), '玩家1'))
        self.assertEqual(data['status'], 'available')
        summary = summarize_result(data)
        available = {a for a in data['input']['legal_actions'] if data['actions'][a]['status'] == 'available'}
        self.assertEqual({c.action for c in summary.all_choices}, available)
        for c in summary.all_choices:
            self.assertAlmostEqual(c.profit + c.push + c.loss, 1)
            self.assertEqual(c.ev, data['actions'][c.action]['ev'])


if __name__ == '__main__':
    unittest.main()
