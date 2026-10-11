"""Real Tk/temporary storage checks with an explicit mocked numeric worker.

These checks cover identity, publication and presentation, not solver accuracy.
"""
import math
from time import perf_counter, sleep
import unittest
from unittest.mock import patch
import uuid

from blackjack_lab.analysis.ace_prepeek_contracts import AcePrepeekInput, ACE_PREPEEK_RESULT_SCHEMA
from blackjack_lab.analysis.decision_summary import summarize_result
from blackjack_lab.analysis.probability import LABELS, DEALER_LABELS, FiniteModel, total
from blackjack_lab.core.table import ACTION_STAND
from blackjack_lab.ledger.events import PEEK_NEGATIVE
from blackjack_lab.ui.analysis_panel import DISPLAY_RESULT_SCHEMAS, format_result
from blackjack_lab.ui.table_modes import BCLC, bclc_rules
from tests import test_table_modes as fixture


def numeric_worker(counts, player, dealer_up, peek_negative, actions, *args, **kwargs):
    """A normalized conditional fixture; never represented as a real solve."""
    assert dealer_up == 1 and peek_negative is True
    natural = sorted(player) == [1, 10]
    result = {}
    for action in actions:
        dist = {'0': 0.0, '1.5': 1.0} if natural else (
            {'-2': .6, '0': .1, '2': .3} if action == 'double' else
            {'-1': .5, '0': .1, '1': .4})
        result[action] = dict(ev=math.fsum(float(net)*prob for net, prob in dist.items()),
                              net_distribution=dist)
    draw = FiniteModel(1, True).target_draw(counts)
    bust = math.fsum(prob for i, prob in enumerate(draw)
                     if total(sum(player)+i+1, 1 in player or i == 0) > 21)
    return dict(actions=result, next_draw=dict(zip(LABELS, draw)), hit_bust=bust,
                dealer_distribution={label: float(label == '17') for label in DEALER_LABELS})


class PreparedService:
    """Replace process dispatch; keep the real analysis composition and saving."""
    deferred_release = False
    active = None
    cleanup_error = ''
    stopped = True

    def __init__(self):
        self.result = None
        self.requests = []

    def start(self, snapshot, budget_seconds=5):
        from blackjack_lab.analysis.service import calculate
        request_id = uuid.uuid4().hex
        self.requests.append(snapshot)
        self.result = calculate(snapshot, request_id, budget_seconds)
        return request_id

    def poll(self):
        result, self.result = self.result, None
        return result

    def cancel(self, *args):
        self.result = None

    def close(self):
        self.cancel()


class AcePrepeekUITests(unittest.TestCase):
    def setUp(self):
        service = patch('blackjack_lab.ui.analysis_panel.AnalysisService', PreparedService)
        service.start(); self.addCleanup(service.stop)
        worker = patch('blackjack_lab.analysis.ace_prepeek_service.solve_presplit_native',
                       side_effect=numeric_worker)
        worker.start(); self.addCleanup(worker.stop)
        fixture.TestTableModeUI.setUp(self)
        self.app.deiconify(); self.app.update()

    def pump(self, condition, timeout=4):
        deadline = perf_counter()+timeout
        while perf_counter() < deadline:
            self.app.update()
            if condition():
                return
            sleep(.005)
        self.fail('focused pre-peek UI state did not arrive')

    def initial(self, cards=('9', '7'), known=True):
        app = self.app
        app.select_table_mode(BCLC)
        if known:
            app.act_new_shoe()
        else:
            app.ctrl.replace_shoe(bclc_rules(), app.ctrl.context_token)
        app.var_simple_hole.set(True)
        app.var_auto_next.set(False)
        app.act_new_round()
        app.analysis_panel.auto.set(True)
        for rank in (cards[0], 'A', cards[1]):
            app._key_rank(rank)
        app.update()
        return app.ctrl.ledger.to_list()

    def available(self):
        panel = self.app.analysis_panel
        self.pump(lambda: panel.last_result is not None)
        result = panel.last_result
        self.assertEqual(result['schema'], ACE_PREPEEK_RESULT_SCHEMA)
        self.assertEqual(result['status'], 'available', result.get('reason'))
        self.pump(lambda: panel.saved is not None)
        return result

    def test_auto_A_result_is_saved_before_actual_peek_and_draw_scope_is_explicit(self):
        original = self.initial()
        result = self.available()
        app, panel, view = self.app, self.app.analysis_panel, self.app.compact_panel
        self.assertIsInstance(panel.request_snapshot, AcePrepeekInput)
        self.assertFalse(result['input']['peek_negative'])
        self.assertEqual(view.model.state, '检查前评估')
        self.assertIn('等待庄家检查', view.model.message)
        self.assertIn('含BJ分支', view.model.message)
        self.assertNotIn('已排除BJ', view.identity.get())
        self.assertIn('非BJ后补牌爆牌率', view.rows[0][3].cget('text'))
        choice = view.model.choices[0]
        self.assertAlmostEqual(choice.profit, (1-result['dealer_bj_probability'])*.4)
        self.assertEqual(view.rows[0][4].cget('text'), f'净盈利概率 {choice.profit:.2%}')
        text = panel.text.get('1.0', 'end-1c')
        self.assertIn('未写入非BJ确认', text)
        self.assertIn('非BJ后且实际补牌', text)
        self.assertIn('不代表实际已检查', text)
        self.assertEqual(app.ctrl.ledger.to_list(), original)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), original)
        self.assertFalse(any(event.etype == PEEK_NEGATIVE for event in app.ctrl.ledger.events))
        self.assertFalse(app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertEqual(app.ctrl.state().current.shoe.unrevealed_out, 1)
        stored = app.ctrl.analysis_store.load(panel.saved['snapshot_id'])
        self.assertEqual(stored['result']['input_digest'], result['input_digest'])
        self.assertIn(result['schema'], DISPLAY_RESULT_SCHEMAS)
        self.assertIsInstance(app.ctrl.recompute_input(stored), AcePrepeekInput)
        self.assertEqual(self.errors, [])

    def test_actual_input_and_one_undo_invalidate_then_recompute_prepeek_identity(self):
        original = self.initial()
        first = self.available()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        saved_id = panel.saved['snapshot_id']
        self.app.act_action(ACTION_STAND)
        self.assertIsNone(panel.last_result)
        self.assertFalse(view.model.choices)
        self.assertTrue(self.app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.app.act_undo()
        self.assertFalse(self.app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertEqual(self.app.ctrl.entry_plan.mode, 'peek_wait')
        second = self.available()
        self.assertNotEqual(first['input_digest'], second['input_digest'])
        self.assertNotEqual(saved_id, panel.saved['snapshot_id'])
        self.assertEqual(first['input']['counts'], second['input']['counts'])
        self.assertEqual(view.model.state, '检查前评估')
        self.assertEqual(self.app.ctrl.ledger.to_list()[:len(original)], original)
        self.assertEqual(self.app.ctrl.analysis_store.load(saved_id)['result']['input_digest'], first['input_digest'])
        self.assertEqual(self.errors, [])

    def test_unknown_shoe_observations_hide_old_values_and_never_start_prepeek_solve(self):
        self.initial(); self.available()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        count = len(panel.service.requests)
        self.initial(known=False)
        self.pump(lambda: panel.unavailable_code == 'START_UNKNOWN')
        self.assertIsNone(panel.last_result)
        self.assertFalse(view.model.choices)
        self.assertIsNone(view.model.terminal)
        self.assertEqual(len(panel.service.requests), count)
        self.assertTrue(panel.compute_button.instate(['disabled']))
        self.assertIsNone(self.app.ctrl.state().current.rules.start_from_new_shoe)
        self.assertNotIn('%', view.rows[0][4].cget('text'))
        self.assertEqual(self.errors, [])

    def test_natural_BJ_shows_one_terminal_result_without_fabricated_actions(self):
        original = self.initial(cards=('T', 'A'))
        result = self.available()
        view = self.app.compact_panel
        self.assertEqual(result['actions'], {})
        self.assertFalse(view.model.choices)
        self.assertFalse(view.model.all_choices)
        self.assertIsNotNone(view.model.terminal)
        self.assertIn('自然BJ', view.model.state)
        self.assertEqual(view.rows[0][2].cget('text'), '终局结算')
        self.assertTrue(view.rows[0][0].winfo_ismapped())
        self.assertFalse(view.rows[1][0].winfo_ismapped())
        self.assertIn('自然BJ终局', view.decision_evs.get())
        self.assertAlmostEqual(view.model.terminal.ev, 1.5*(1-result['dealer_bj_probability']))
        self.assertEqual(self.app.ctrl.ledger.to_list(), original)
        self.assertFalse(self.app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertEqual(self.errors, [])

    def test_certain_dealer_BJ_summary_is_terminal_and_has_no_player_strategy(self):
        result = dict(schema=ACE_PREPEEK_RESULT_SCHEMA, evaluation_stage='before_ace_peek',
            status='available', input=dict(seat='玩家1', player_ranks=['9', '7'], dealer_up=1,
                legal_actions=[], uncertain_actions=[]), actions={},
            terminal_evaluation=dict(status='available', reason_code='DEALER_BJ_CERTAIN',
                ev=-1., net_distribution={'-1': 1.}))
        summary = summarize_result(result)
        self.assertIn('BJ确定', summary.state)
        self.assertFalse(summary.choices)
        self.assertFalse(summary.all_choices)
        self.assertEqual(summary.terminal.ev, -1)
        self.assertEqual(summary.terminal.loss, 1)
        self.assertIn('等待庄家实际检查', summary.message)

    def test_pause_clears_prepeek_and_terminal_numbers_and_740_short_screen_keeps_bounds(self):
        self.initial(); self.available()
        app, view = self.app, self.app.compact_panel
        app.geometry('720x740'); app.update()
        app.compact_canvas.yview_moveto(0); app.update()
        for widget in (view.rows[0][0], view.rows[1][0], view.details_button, view.undo_button):
            self.assertLessEqual(widget.winfo_rooty()+widget.winfo_height(), app.winfo_rooty()+app.winfo_height())
            self.assertLessEqual(widget.winfo_rootx()+widget.winfo_width(), app.winfo_rootx()+app.winfo_width())
        app.geometry('720x460'); app.update()
        self.assertGreater(float(app.compact_canvas.cget('scrollregion').split()[-1]), app.winfo_height())
        app.compact_canvas.yview_moveto(1); app.update()
        self.assertAlmostEqual(app.compact_canvas.yview()[1], 1)
        with patch.object(app, 'recording_advice_pause', return_value='录牌待保存，当前建议暂停'):
            view.render(); app.update()
            self.assertFalse(view.model.choices)
            self.assertIsNone(view.model.terminal)
            self.assertEqual(view.decision_evs.get(), '')
            self.assertNotIn('%', view.rows[0][4].cget('text'))
        self.initial(cards=('T', 'A')); self.available()
        with patch.object(app, 'recording_advice_pause', return_value='录牌需要核对，当前建议暂停'):
            view.render(); app.update()
            self.assertIsNone(view.model.terminal)
            self.assertEqual(view.decision_evs.get(), '')
            self.assertNotIn('%', view.rows[0][4].cget('text'))


if __name__ == '__main__':
    unittest.main()
