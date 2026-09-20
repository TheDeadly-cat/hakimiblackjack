"""PR14 full-app regressions adapted from the supplied 8-case proposal.
Only pause-state assertions change from paused (auto queue) to input_paused.
Every test uses temporary databases and actual Windows Tk widgets.
"""
import os
import unittest
from unittest.mock import patch
import tkinter as tk
from tkinter import ttk

from tests import test_ui_workflow as ui_fixture
from tests.test_analysis_integration import example
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import (
    same_value_split_research_rules, same_value_das_research_rules,
)


@unittest.skipUnless(os.name == 'nt', 'Requires complete target Windows/.NET environment')
class TestPR14Windows(unittest.TestCase):
    # Reuse only fixture methods; do not inherit and rerun all old tests.
    setUp = ui_fixture.TestUIWorkflow.setUp
    close_app = ui_fixture.TestUIWorkflow.close_app
    start = ui_fixture.TestUIWorkflow.start

    def _continuation(self):
        self.start()
        for rank in ('9', '8', '6'):
            self.app._key_rank(rank)
        self.app._key_hole()
        self.assertEqual(self.errors, [])

    def test_pause_prevents_durable_numeric_card(self):
        self._continuation()
        before = self.app.ctrl.ledger.to_list()
        self.app._key_pause()
        self.app._key_rank('3')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_continuation_pause_can_resume_without_initial_slot(self):
        self._continuation()
        self.app._key_pause()
        self.app._key_pause()
        self.assertFalse(self.app.ctrl.entry_plan.input_paused)
        self.assertEqual(self.errors, [])

    def test_focused_button_space_only_pauses(self):
        self.start()
        # A real ttk button/class binding, not binder.on_press(FakeEvent).
        button = ttk.Button(self.app, text='synthetic card button',
                            command=lambda: self.app.act_card('2'))
        button.place(x=2, y=2)
        self.app.update()
        button.focus_force()
        self.app.update()
        before = self.app.ctrl.ledger.to_list()
        button.event_generate('<KeyPress-space>')
        self.app.update()
        button.event_generate('<KeyRelease-space>')
        self.app.update()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertTrue(self.app.ctrl.entry_plan.input_paused)

    def test_navigation_visits_each_split_hand(self):
        self.app.act_research_template(split=True, same_value=True)
        self.app.act_new_shoe()
        self.app.act_new_round()
        for rank in ('T', '6', 'T'):
            self.app._key_rank(rank)
        self.app._key_hole()
        from blackjack_lab.core.table import ACTION_SPLIT
        self.app.act_action(ACTION_SPLIT)
        table = self.app.ctrl.state().current.table
        first, second = table.players['玩家1'].hands
        self.assertEqual(self.app._selected_hand_id(self.app.ctrl.state().current), first.hand_id)
        before = self.app.ctrl.ledger.to_list()
        self.app._key_navigate(1)
        self.assertEqual(self.app.var_target.get(), '玩家1')
        self.assertEqual(self.app._selected_hand_id(self.app.ctrl.state().current), second.hand_id)
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_committed_card_with_failed_plan_write_is_not_reentered_after_recover(self):
        self.start()
        session = self.app.ctrl.session_id
        with patch.object(self.app.ctrl, 'save_entry_plan', side_effect=OSError('injected plan failure')):
            self.app._key_rank('9')
        self.assertEqual(self.app.ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['9'])
        recovered = SessionController.recover(self.db, session)
        self.addCleanup(recovered.close)
        plan = recovered.entry_plan
        # Unambiguously reconciled cursor or explicitly paused; never silently s1 again.
        safe = plan.paused or plan.mode == MODE_UNALIGNED
        if not safe:
            safe = bool(plan.slot() and plan.slot().slot_id != 's1' and plan.filled_slots.get('s1'))
        self.assertTrue(safe, plan.to_dict())

    def test_listener_failure_cannot_leave_automatic_cursor_on_saved_slot(self):
        self.start()
        def fail_listener():
            raise RuntimeError('injected context listener failure')
        self.app.ctrl.add_context_listener(fail_listener)
        try:
            self.app._key_rank('9')
        finally:
            self.app.ctrl.remove_context_listener(fail_listener)
        plan = self.app.ctrl.entry_plan
        self.assertEqual(self.app.ctrl.state().current.table.players['玩家1'].hands[0].ranks, ['9'])
        self.assertTrue(plan.paused or plan.mode == MODE_UNALIGNED or
                        (plan.slot() is not None and plan.slot().slot_id != 's1'), plan.to_dict())

    def test_bad_plan_is_preserved_and_does_not_prevent_loading_healthy_ledger(self):
        self.start()
        session = self.app.ctrl.session_id
        path = self.app.ctrl._entry_plan_path()
        path.write_text('[]', encoding='utf-8')
        recovered = SessionController.recover(self.db, session)
        self.addCleanup(recovered.close)
        self.assertTrue(recovered.entry_plan.paused)
        self.assertEqual(recovered.ledger.to_list(), self.app.ctrl.ledger.to_list())
        self.assertEqual(path.read_text(encoding='utf-8'), '[]')

    def test_same_value_variants_reach_real_solver_and_same_value_ev(self):
        for factory in (same_value_split_research_rules, same_value_das_research_rules):
            reference = None
            for cards in (('T','T'), ('K','Q'), ('10','J')):
                with self.subTest(profile=factory.__name__,cards=cards):
                    snapshot=build_input(example(cards=cards,up='6',rules=factory()),'玩家1')
                    result=calculate(snapshot)
                    self.assertEqual(result['status'],'available',result.get('reason'))
                    self.assertEqual(result['actions']['split']['status'],'available')
                    evs={a:v['ev'] for a,v in result['actions'].items() if v.get('status')=='available'}
                    if reference is not None:
                        self.assertEqual(set(evs),set(reference))
                        for action in evs:
                            self.assertAlmostEqual(evs[action],reference[action],delta=1e-10)
                    reference=evs


class TestReliabilityBoundaries(unittest.TestCase):
    setUp = ui_fixture.TestUIWorkflow.setUp
    close_app = ui_fixture.TestUIWorkflow.close_app
    start = ui_fixture.TestUIWorkflow.start

    def recover(self):
        recovered = SessionController.recover(self.db, self.app.ctrl.session_id)
        self.addCleanup(recovered.close)
        return recovered

    def press(self, widget, key, repeats=1):
        widget.focus_force()
        self.app.update()
        self.last_key_events = []
        tag = 'PR14KeyProbe' + str(id(self))
        original_tags = widget.bindtags()
        def observe(event):
            self.last_key_events.append((str(event.type), event.keysym, event.keycode, event.state))
        bindings = [(sequence, self.app.bind_class(tag, sequence, observe))
                    for sequence in ('<KeyPress>', '<KeyRelease>')]
        widget.bindtags((tag, *original_tags))
        try:
            for _ in range(repeats):
                widget.event_generate('<KeyPress-' + key + '>')
                self.app.update()
            widget.event_generate('<KeyRelease-' + key + '>')
            self.app.update()
        finally:
            widget.bindtags(original_tags)
            for sequence, command in bindings:
                self.app.unbind_class(tag, sequence)
                self.app.deletecommand(command)

    def continuation(self, participants=('玩家1',), reverse=False):
        self.app.act_research_template(split=True, same_value=True)
        self.app.act_new_shoe()
        for seat, var in self.app.var_participants.items():
            var.set(seat in participants)
        self.app.var_deal_direction.set('reverse' if reverse else 'forward')
        self.app.act_new_round()
        for rank in ['T'] * len(participants) + ['6'] + ['T'] * len(participants):
            self.app._key_rank(rank)
        self.app._key_hole()
        self.assertEqual(self.errors, [])

    def test_initial_pause_buttons_keys_and_hole_remain_idle_then_resume(self):
        self.start()
        plan = self.app.ctrl.entry_plan
        cursor = plan.cursor_slot_id
        before = self.app.ctrl.ledger.to_list()
        self.app._key_pause()
        self.assertEqual(plan.mode, 'initial_auto')
        for operation in (lambda: self.app._key_rank('T'), lambda: self.app.act_card('3'),
                          self.app._key_hole, self.app.act_hidden_card, self.app.act_unknown_card):
            operation()
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(plan.cursor_slot_id, cursor)
        recovered = self.recover()
        self.assertTrue(recovered.entry_plan.input_paused)
        self.app._key_pause()
        self.app._key_rank('T')
        self.assertEqual(len(self.app.ctrl.ledger.events), len(before) + 1)
        self.assertEqual(self.app.var_target.get(), '庄家')

    def test_real_enter_tab_do_not_invoke_focused_button(self):
        self.continuation()
        calls = []
        button = ttk.Button(self.app, command=lambda: calls.append('invoked'))
        button.place(x=2, y=2)
        before = self.app.ctrl.ledger.to_list()
        for key in ('Return', 'Tab'):
            self.press(button, key)
        self.assertEqual(calls, [])
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_real_repeat_and_two_distinct_zero_presses(self):
        self.start()
        self.press(self.app, '0', repeats=4)
        self.assertEqual(len(self.app.ctrl.entry_plan.filled_slots), 1)
        self.press(self.app, '0')
        self.assertEqual(len(self.app.ctrl.entry_plan.filled_slots), 2)
        table = self.app.ctrl.state().current.table
        self.assertEqual(table.players['玩家1'].hands[0].ranks, ['T'])
        self.assertEqual(table.dealer.hands[0].ranks, ['T'])

    def test_entry_text_combo_and_modal_keep_their_own_keys(self):
        self.start()
        before = self.app.ctrl.ledger.to_list()
        widgets = [ttk.Entry(self.app), tk.Text(self.app, height=1), ttk.Combobox(self.app)]
        for index, widget in enumerate(widgets):
            widget.place(x=3, y=3 + index * 24)
            self.press(widget, '0')
            text = widget.get('1.0', 'end').strip() if isinstance(widget, tk.Text) else widget.get()
            self.assertEqual(text, '0')
        dialog = tk.Toplevel(self.app)
        calls = []
        button = ttk.Button(dialog, command=lambda: calls.append('dialog'))
        button.pack()
        dialog.grab_set()
        self.press(button, 'space')
        dialog.grab_release()
        dialog.destroy()
        self.assertEqual(calls, ['dialog'])
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_save_failure_neither_commits_nor_changes_plan(self):
        self.start()
        before = self.app.ctrl.ledger.to_list()
        plan = self.app.ctrl.entry_plan.to_dict()
        with patch.object(self.app.ctrl.store, 'save_event', side_effect=OSError('injected db failure')):
            self.app._key_rank('9')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertEqual(self.app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(self.recover().ledger.to_list(), before)

    def test_process_exit_after_sqlite_commit_before_plan_sync(self):
        import subprocess, sys
        self.start()
        session = self.app.ctrl.session_id
        script = ("import os,sys; from blackjack_lab.ui.controller import SessionController; "
                  "c=SessionController.recover(sys.argv[1],sys.argv[2]); "
                  "c._sync_entry_event=lambda event:os._exit(73); c.deal_shown('玩家1','9')")
        result = subprocess.run([sys.executable, '-c', script, str(self.db), session],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 73, result.stderr)
        recovered = self.recover()
        self.assertEqual(recovered.state().current.table.players['玩家1'].hands[0].ranks, ['9'])
        self.assertTrue(recovered.entry_plan.input_paused)
        self.assertEqual(recovered.entry_plan.mode, MODE_UNALIGNED)

    def test_all_bad_sidecars_preserve_bytes_and_healthy_events(self):
        import json
        self.start()
        path = self.app.ctrl._entry_plan_path()
        valid = self.app.ctrl.entry_plan.to_dict()
        variants = ['{', '[]', 'null', json.dumps({k: v for k, v in valid.items() if k != 'input_paused'}),
                    json.dumps({**valid, 'version': 99}),
                    json.dumps({**valid, 'schema': 'hakimi-round-entry-plan-v1', 'version': 1}),
                    json.dumps({**valid, 'shoe_id': 'different'}),
                    json.dumps({**valid, 'ledger_digest': '0' * 64})]
        before = self.app.ctrl.ledger.to_list()
        for bad in variants:
            with self.subTest(sidecar=bad[:70]):
                path.write_text(bad, encoding='utf-8')
                recovered = self.recover()
                self.assertEqual(recovered.ledger.to_list(), before)
                self.assertTrue(recovered.entry_plan.input_paused)
                self.assertEqual(path.read_text(encoding='utf-8'), bad)
        path.unlink()
        self.assertTrue(self.recover().entry_plan.input_paused)

    def test_listener_does_not_stop_other_listeners_and_receipt(self):
        self.start()
        observed = []
        def fail():
            raise RuntimeError('injected listener')
        self.app.ctrl.add_context_listener(fail)
        self.app.ctrl.add_context_listener(lambda: observed.append(self.app.ctrl.entry_plan.cursor_slot_id))
        self.app._key_rank('9')
        self.assertEqual(observed, ['s2'])
        self.assertEqual(self.recover().entry_plan.cursor_slot_id, 's2')
        self.assertIn('已保存', self.app.var_status.get())
        self.assertEqual(self.errors, [])

    def test_import_switches_session_and_never_reuses_previous_plan(self):
        from blackjack_lab.storage.export import export_json
        self.start()
        other = example(cards=('9', '7'), up='6')
        file = self.db.with_suffix('.json')
        export_json(other, file)
        self.app.ctrl.import_file(file)
        self.assertEqual(self.app.ctrl.entry_plan.session_id, other.session_id)
        self.assertEqual(self.app.ctrl.entry_plan.mode, MODE_UNALIGNED)
        self.assertTrue(self.app.ctrl.entry_plan.input_paused)
        self.assertEqual(self.app.ctrl.ledger.to_list(), other.to_list())

    def test_split_navigation_focus_prompt_recovery_and_action_position(self):
        from blackjack_lab.core.table import ACTION_SPLIT
        self.continuation(('玩家1', '玩家2'))
        self.app.act_action(ACTION_SPLIT)
        hands = self.app.ctrl.state().current.table.players['玩家1'].hands
        before = self.app.ctrl.ledger.to_list()
        self.app._key_navigate(1)
        self.assertEqual(self.app._selected_hand_id(self.app._current_seg()), hands[1].hand_id)
        self.assertIn('下一张给：玩家1／第2手', self.app.var_entry_prompt.get())
        self.assertEqual(self.recover().entry_plan.continuation_hand_id, hands[1].hand_id)
        self.app._key_rank('3')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertIn('实际行动位置', self.errors[-1])
        self.app._key_navigate(1)
        self.assertEqual(self.app.var_target.get(), '玩家2')
        self.assertIn('下一张给：玩家2／第1手', self.app.var_entry_prompt.get())
        self.assertEqual(self.app.var_analysis_target.get(), '玩家1')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_reverse_initial_continuation_and_navigation_agree(self):
        self.continuation(('玩家1', '玩家3', '玩家5'), reverse=True)
        plan = self.app.ctrl.entry_plan
        self.assertEqual(plan.participating_seats, ('玩家5', '玩家3', '玩家1'))
        self.assertEqual(tuple(self.app.ctrl.state().current.table.participants), plan.participating_seats)
        self.assertEqual(self.app.var_target.get(), '玩家5')
        self.app._key_navigate(1)
        self.assertEqual(self.app.var_target.get(), '玩家3')
        self.assertEqual(self.recover().entry_plan.continuation_seat, '玩家3')

    def test_three_player_round_reveal_next_round_and_recover(self):
        self.app.var_decks.set(6)  # This historical scenario asserts a 312-card shoe.
        self.app.act_research_template()
        self.app.act_new_shoe()
        for name, var in self.app.var_participants.items():
            var.set(name in ('玩家1', '玩家2', '玩家3'))
        self.app.act_new_round()
        for key in ('0', '7', '1', '6', '0', '0', '9', 'period'):
            before = len(self.app.ctrl.ledger.events)
            self.press(self.app, key)
            self.assertEqual(len(self.app.ctrl.ledger.events), before + 1,
                             {'key': key, 'events': self.last_key_events,
                              'errors': self.errors, 'plan': self.app.ctrl.entry_plan.to_dict()})
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 312 - 8)
        old_path = self.app.ctrl._entry_plan_path()
        for _ in range(3):
            self.app._key_stand()
        self.assertEqual(self.app.var_target.get(), '庄家')
        self.app.var_mode.set('揭示')
        self.press(self.app, '9')
        self.assertEqual(self.app.ctrl.state().current.shoe.physical_remaining(), 312 - 8)
        self.app.var_mode.set('新发牌')
        self.press(self.app, '2')
        self.app.act_end_round()
        prior_plan = old_path.read_bytes()
        self.app.act_new_round()
        self.assertEqual(self.app.ctrl.entry_plan.cursor_slot_id, 's1')
        self.assertEqual(old_path.read_bytes(), prior_plan)
        self.assertEqual(self.recover().entry_plan.to_dict(), self.app.ctrl.entry_plan.to_dict())
        self.assertEqual(self.errors, [])


    def test_navigation_write_failure_blocks_more_cards(self):
        self.continuation()
        before = self.app.ctrl.ledger.to_list()
        with patch('blackjack_lab.ui.controller.atomic_write', side_effect=OSError('disk')):
            self.app._key_navigate(1)
        self.assertTrue(self.app.ctrl.entry_plan.input_paused)
        self.app._key_rank('3')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)

    def test_damaged_hand_identity_does_not_break_ledger_recovery(self):
        import json
        self.continuation()
        path = self.app.ctrl._entry_plan_path()
        data = self.app.ctrl.entry_plan.to_dict()
        data['continuation_hand_id'] = 'not-a-real-hand'
        path.write_text(json.dumps(data), encoding='utf-8')
        recovered = self.recover()
        self.assertTrue(recovered.entry_plan.input_paused)
        self.assertEqual(recovered.ledger.to_list(), self.app.ctrl.ledger.to_list())

    def test_bad_plan_manual_recovery_preserves_original_and_focus(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        self.start()
        self.app._key_rank('9')
        path = self.app.ctrl._entry_plan_path()
        path.write_text('[]', encoding='utf-8')
        self.close_app()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(path.read_text(encoding='utf-8'), '[]')
        before = self.app.ctrl.ledger.to_list()
        self.app._key_rank('6')
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.app.act_manual_alignment()
        preserved = list(path.parent.glob(path.stem + '.preserved-*.json'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(preserved[0].read_text(encoding='utf-8'), '[]')
        self.app._key_jump(0)
        self.app._key_rank('6')
        self.assertEqual(self.app.ctrl.state().current.table.dealer.hands[0].ranks, ['6'])
        self.assertEqual(self.recover().entry_plan.continuation_seat, '庄家')
        self.assertEqual(self.recover().entry_plan.mode, 'manual_override')

    def test_reopen_app_restores_specific_split_hand_and_prompt(self):
        from blackjack_lab.core.table import ACTION_SPLIT
        from blackjack_lab.ui.app import BlackjackLabApp
        self.continuation()
        self.app.act_action(ACTION_SPLIT)
        self.app._key_navigate(1)
        second = self.app._selected_hand_id(self.app._current_seg())
        before = self.app.ctrl.ledger.to_list()
        self.close_app()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.app.update()
        self.assertEqual(self.app._selected_hand_id(self.app._current_seg()), second)
        self.assertIn('下一张给：玩家1／第2手', self.app.var_entry_prompt.get())
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)


    def test_swapped_same_seat_event_ids_require_recovery_review(self):
        import json
        self.start()
        for rank in ('9', '6', '7'):
            self.app._key_rank(rank)
        data = self.app.ctrl.entry_plan.to_dict()
        data['filled_slots']['s1'], data['filled_slots']['s3'] = data['filled_slots']['s3'], data['filled_slots']['s1']
        self.app.ctrl._entry_plan_path().write_text(json.dumps(data), encoding='utf-8')
        recovered = self.recover()
        self.assertTrue(recovered.entry_plan.input_paused)
        self.assertEqual(recovered.ledger.to_list(), self.app.ctrl.ledger.to_list())
