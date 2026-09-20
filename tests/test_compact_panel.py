import unittest
from unittest.mock import patch

from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_SPLIT
from tests import test_analysis_ui as fixture


class TestCompactPanel(unittest.TestCase):
    setUp = fixture.TestAnalysisUI.setUp
    close = fixture.TestAnalysisUI.close
    start = fixture.TestAnalysisUI.start
    wait_result = fixture.TestAnalysisUI.wait_result

    def calculated(self):
        self.start(cards=('T', '6'), up='6')
        self.app.analysis_panel.calculate_current()
        result = self.wait_result()
        self.assertEqual(result['status'], 'available')
        self.assertEqual(self.errors, [])
        return result

    def test_default_summary_and_two_choices_fit_without_scrolling(self):
        self.calculated()
        view = self.app.compact_panel
        self.assertTrue(view.winfo_ismapped())
        self.assertFalse(self.app.workbench.winfo_ismapped())
        self.assertFalse(self.app.lst_timeline.winfo_ismapped())
        self.assertIsNone(view.detail_window)
        self.assertEqual(len(view.model.choices), 2)
        for size in ('720x500', '660x460'):
            self.app.geometry(size)
            self.app.update()
            for row in view.rows:
                for widget in row:
                    self.assertTrue(widget.winfo_ismapped())
                    self.assertLessEqual(widget.winfo_rooty() + widget.winfo_height(), self.app.winfo_rooty() + self.app.winfo_height())
                    self.assertLessEqual(widget.winfo_rootx() + widget.winfo_width(), self.app.winfo_rootx() + self.app.winfo_width())
            self.assertTrue(view.details_button.winfo_ismapped())

    def test_expand_and_workbench_share_one_result_no_extra_request(self):
        result = self.calculated()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        request, saved = panel.request_id, panel.saved
        with patch.object(panel.service, 'start', wraps=panel.service.start) as start:
            view.show_details()
            self.app.show_workbench()
            self.app.show_compact()
            view.show_details()
        self.assertEqual(start.call_count, 0)
        self.assertIs(panel.last_result, result)
        self.assertEqual(panel.request_id, request)
        self.assertEqual(panel.saved, saved)
        self.assertEqual(view.detail_text.get('1.0', 'end-1c'), panel.text.get('1.0', 'end-1c'))

    def test_commit_invalidates_both_views_before_general_redraw(self):
        self.calculated()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        view.show_details()
        before = self.app.ctrl.store.event_count()
        self.app.ctrl.deal_shown('玩家1', '2')
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.assertIsNone(panel.last_result)
        self.assertFalse(view.model.choices)
        self.assertNotIn('净盈利 ', view.detail_text.get('1.0', 'end'))
        self.assertFalse(view.rows[0][0].winfo_ismapped())

    def test_cancel_timeout_and_switch_hand_do_not_reuse_recommendation(self):
        self.calculated()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        panel.cancel()
        self.assertEqual(view.model.state, '已取消')
        self.assertFalse(view.model.choices)
        panel.start(self.app.ctrl.analysis_input('玩家1'), budget_seconds=.001)
        self.assertEqual(self.wait_result()['status'], 'timeout')
        self.assertIn('超时', view.model.state)
        self.assertFalse(view.model.choices)
        panel.calculate_current()
        self.app.var_analysis_target.set('玩家2')
        self.assertIsNone(panel.service.active)
        self.assertFalse(view.model.choices)

    def test_historical_recompute_clearly_separate_then_return_current(self):
        self.calculated()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        saved = panel.saved
        self.app._key_rank('2')
        panel.start(self.app.ctrl.recompute_input(saved), saved['snapshot_id'])
        self.wait_result()
        self.assertTrue(view.model.historical)
        self.assertIn('历史', view.model.state)
        self.assertIn('T 6 ', view.model.identity)
        view.current_button.invoke()
        self.assertFalse(view.model.historical)
        self.assertFalse(view.model.choices)
        self.assertIn('T 6 2', view.model.identity)

    def test_recording_drawer_does_not_change_ledger_or_request(self):
        self.calculated()
        before = self.app.ctrl.ledger.to_list()
        panel, view = self.app.analysis_panel, self.app.compact_panel
        request = panel.request_id
        view.toggle_recording()
        self.app.update()
        self.assertTrue(view.record_prompt.winfo_ismapped())
        view.toggle_recording()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(panel.request_id, request)

    def test_recording_controls_stay_in_place_when_numbers_disappear(self):
        self.calculated()
        view = self.app.compact_panel
        view.toggle_recording()
        self.app.update()
        before = view.action_buttons[0].winfo_rooty()
        self.app._key_rank('2')
        self.app.update()
        self.assertFalse(view.model.choices)
        self.assertLessEqual(abs(view.action_buttons[0].winfo_rooty() - before), 5)

    def test_correction_invalidates_current_numbers_without_deleting_history(self):
        self.calculated()
        saved = self.app.analysis_panel.saved
        event = next(e for e in reversed(self.app.ctrl.ledger.events) if e.etype == 'CARD_DEALT' and e.payload.get('rank') == '6')
        before = self.app.ctrl.store.event_count()
        self.app.ctrl.correct(event.event_id, {'rank': '7'}, '自建纠错验收')
        self.assertEqual(self.app.ctrl.store.event_count(), before + 1)
        self.assertFalse(self.app.compact_panel.model.choices)
        self.assertIsNone(self.app.analysis_panel.last_result)
        self.assertEqual(self.app.ctrl.analysis_store.list()[0][0]['snapshot_id'], saved['snapshot_id'])

    def test_split_das_waiting_and_complete_hide_recommendations(self):
        app = self.app
        app.act_research_template(das=True)
        app.act_new_shoe()
        app.act_new_round()
        for rank in ('8', '6', '8'):
            app._key_rank(rank)
        app._key_hole()
        app.act_action(ACTION_SPLIT)
        self.assertEqual(app.compact_panel.model.state, '等待补牌')
        app._key_rank('9')
        app._key_stand()
        app._key_rank('3')
        app.analysis_panel.calculate_current()
        result = self.wait_result()
        self.assertEqual(result['status'], 'available')
        self.assertIn('第2手', app.compact_panel.model.identity)
        app.act_action(ACTION_DOUBLE)
        self.assertEqual(app.compact_panel.model.state, '等待补牌')
        self.assertFalse(app.compact_panel.model.choices)
        app._key_rank('T')
        self.assertEqual(app.compact_panel.model.state, '等待庄家')
        self.assertFalse(app.compact_panel.model.choices)
        app.ctrl.mark_gap('测试缺口不能被流程提示覆盖')
        app.refresh_all()
        self.assertIn('观察缺口', app.compact_panel.model.message)

    def test_double_wait_then_closed_hands_never_recommend_double_again(self):
        self.start(cards=('5', '6'), up='6')
        self.app.act_action(ACTION_DOUBLE)
        view = self.app.compact_panel
        self.assertEqual(view.model.state, '等待补牌')
        self.assertFalse(view.model.choices)
        self.app._key_rank('T')
        self.assertEqual(view.model.state, '等待庄家')
        self.assertFalse(view.model.choices)

    def test_missing_cards_rules_and_gap_are_explicit(self):
        view = self.app.compact_panel
        self.assertFalse(view.model.choices)
        self.start(cards=('T', '6'), up='6')
        self.app.ctrl.mark_gap('测试漏牌')
        self.app.refresh_all()
        self.assertFalse(view.model.choices)
        self.assertIn('观察缺口', view.model.message)

    def test_tie_and_single_action_labels_are_not_fabricated(self):
        from tests.test_decision_summary import result
        panel, view = self.app.analysis_panel, self.app.compact_panel
        panel.last_result = result({'stand': {-1: .5, 1: .5}, 'hit': {-1: .5, 1: .5}})
        view.render()
        self.app.update()
        self.assertEqual([row[1].cget('text') for row in view.rows], ['并列', '并列'])
        panel.last_result = result({'stand': {1: 1}})
        view.render()
        self.app.update()
        self.assertEqual(view.rows[0][1].cget('text'), '唯一合法动作')
        self.assertFalse(view.rows[1][0].winfo_ismapped())


if __name__ == '__main__':
    unittest.main()
