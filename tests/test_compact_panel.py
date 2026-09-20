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

    def deal_practice_round(self):
        app = self.app
        app.act_research_template()
        app.act_new_shoe()
        app.act_new_round()
        for rank in ('T', '6', '6'):
            app._key_rank(rank)
        app._key_hole()
        app._key_stand()

    def test_dealer_reveal_draw_undo_and_correction_update_top_cards(self):
        self.deal_practice_round()
        app, view = self.app, self.app.compact_panel
        self.assertIn('庄家 6 暗牌 · 点数待确认', view.identity.get())
        self.assertIn('当前发牌给：庄家', view.heading.get())
        app.var_mode.set('揭示')
        app._key_rank('9')
        self.assertIn('庄家 6 9 · 15点', view.identity.get())
        app.var_mode.set('新发牌')
        app._key_rank('2')
        self.assertIn('庄家 6 9 2 · 17点', view.identity.get())
        app.act_undo()
        self.assertIn('庄家 6 9 · 15点', view.identity.get())
        app.act_undo()
        self.assertIn('庄家 6 暗牌 · 点数待确认', view.identity.get())
        app.var_mode.set('揭示')
        app._key_rank('9')
        reveal = app.ctrl.ledger.events[-1]
        app.ctrl.correct(reveal.event_id, {'rank': 'A'}, '练习纠错')
        app.refresh_all()
        self.assertIn('庄家 6 A · 软17点', view.identity.get())
        self.assertEqual(self.errors, [])

    def test_settlement_recovery_undo_and_new_round_clear_stale_target(self):
        self.deal_practice_round()
        app = self.app
        app.var_mode.set('揭示')
        app._key_rank('9')
        app.var_mode.set('新发牌')
        app._key_rank('2')
        with patch('blackjack_lab.ui.app.messagebox.showinfo',
                   side_effect=lambda *args: self.assertNotIn('下一张给', app.var_entry_prompt.get())):
            app.act_end_round()
        ledger = app.ctrl.ledger.to_list()
        from blackjack_lab.ui.app import BlackjackLabApp
        self.close()
        self.app = app = BlackjackLabApp(self.db)
        app.update()
        self.assertEqual(app.ctrl.ledger.to_list(), ledger)
        self.assertIn('本轮已结算', app.var_entry_prompt.get())
        self.assertNotIn('下一张给', app.var_entry_prompt.get())
        self.assertNotIn('录入 庄家', app.compact_panel.recording_hint.get())
        self.assertIn('本轮已结算', app.compact_panel.heading.get())
        self.assertIn('庄家 6 9 2 · 17点', app.compact_panel.identity.get())
        app.act_undo()
        self.assertIn('下一张给：庄家', app.var_entry_prompt.get())
        self.assertIn('当前发牌给：庄家', app.compact_panel.heading.get())
        app.act_end_round()
        app.act_new_round()
        self.assertIn('下一张给：玩家1', app.var_entry_prompt.get())
        self.assertIn('庄家 · 尚未录牌', app.compact_panel.identity.get())
        self.assertEqual(self.errors, [])

    def test_unsettled_end_and_closed_shoe_do_not_offer_next_card(self):
        self.deal_practice_round()
        self.app.ctrl.end_round_unsettled('练习未揭牌', 'unknown')
        self.app.refresh_all()
        self.assertIn('本轮已结束未结算', self.app.var_entry_prompt.get())
        self.assertNotIn('下一张给', self.app.var_entry_prompt.get())
        self.app.act_end_shoe()
        self.assertIn('牌靴已结束', self.app.var_entry_prompt.get())
        self.assertIn('新建牌靴', self.app.compact_panel.recording_hint.get())
        self.assertEqual(self.errors, [])

    def test_historical_result_does_not_use_later_dealer_cards(self):
        self.calculated()
        app, panel, view = self.app, self.app.analysis_panel, self.app.compact_panel
        saved = panel.saved
        hole = next(e for e in app.ctrl.ledger.events if e.payload.get('face_state') == 'hidden')
        app.ctrl.reveal(hole.event_id, '9')
        app.refresh_all()
        self.assertIn('庄家 6 9 · 15点', view.identity.get())
        panel.start(app.ctrl.recompute_input(saved), saved['snapshot_id'])
        self.wait_result()
        self.assertTrue(view.model.historical)
        self.assertIn('庄家明牌 6', view.identity.get())
        self.assertNotIn('6 9', view.identity.get())
        panel.return_to_current()
        self.assertIn('庄家 6 9 · 15点', view.identity.get())

    def test_eight_deck_default_and_real_keys_cycle_seats_without_recording(self):
        app, view = self.app, self.app.compact_panel
        self.assertEqual(app.var_decks.get(), 8)
        app.act_research_template()
        app.act_new_shoe()
        self.assertEqual(app.ctrl.state().current.rules.n_decks, 8)
        for variable in app.var_participants.values():
            variable.set(True)
        app.act_new_round()
        before = app.ctrl.ledger.to_list()
        app.focus_force()
        app.update()
        for key, state, seat in [('Tab', 0, f'玩家{i}') for i in range(2, 8)] + [
                ('Tab', 0, '庄家'), ('Tab', 0, '玩家1'), ('Tab', 1, '庄家'),
                ('2', 4, '玩家2'), ('3', 4, '玩家3'), ('0', 4, '庄家')]:
            app.event_generate('<KeyPress>', keysym=key, state=state)
            app.update()
            app.event_generate('<KeyRelease>', keysym=key, state=state)
            app.update()
            self.assertEqual(app.var_target.get(), seat)
            self.assertIn('当前发牌给：' + seat, view.heading.get())
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual(app.var_analysis_target.get(), '玩家1')
        self.assertEqual(self.errors, [])

    def test_continuation_navigation_wraps_and_skips_empty_seats(self):
        app = self.app
        app.act_research_template()
        app.act_new_shoe()
        app.var_participants['玩家3'].set(True)
        app.act_new_round()
        for rank in ('T', '9', '6', '6', '7'):
            app._key_rank(rank)
        app._key_hole()
        before = app.ctrl.ledger.to_list()
        for seat in ('玩家3', '庄家', '玩家1'):
            app._key_navigate(1)
            self.assertEqual(app.var_target.get(), seat)
            self.assertIn('当前发牌给：' + seat, app.compact_panel.heading.get())
        app._key_navigate(-1)
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
