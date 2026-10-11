"""Focused daily recording checks against real Tk and temporary SQLite files."""
import json
from dataclasses import replace
from pathlib import Path
import threading
from time import perf_counter, sleep
import unittest
from unittest.mock import patch

from blackjack_lab.ledger.events import CARD_DEALT
from blackjack_lab.ui.deal_entry import MODE_PEEK_WAIT
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from blackjack_lab.ui.table_modes import bclc_rules
from tests import test_recording_background_ui as fixture


class RecordingWorkflowUITests(unittest.TestCase):
    use_process = False
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved

    def key(self, widget, keysym, state=0):
        widget.event_generate('<KeyPress>', keysym=keysym, state=state, when='tail')
        widget.event_generate('<KeyRelease>', keysym=keysym, state=state, when='tail')
        self.app.update()

    def live_deals(self):
        ctrl = self.app.ctrl
        live = set(ctrl.live_card_event_ids())
        return [(e.payload['seat'], e.payload['rank']) for e in ctrl.ledger.events
                if e.etype == CARD_DEALT and e.event_id in live]

    def record(self, *ranks):
        for rank in ranks:
            self.app._key_rank(rank)
        self.wait_saved()

    def test_readonly_analysis_focus_keeps_backspace_and_control_z_working(self):
        app = self.app
        selectors = ((app.compact_panel.seat, app.show_compact),
                     (app.compact_panel.hand, app.show_compact),
                     (app.analysis_panel.cmb_analysis_target, app.show_workbench),
                     (app.analysis_panel.cmb_analysis_hand, app.show_workbench),
                     (app.cmb_hand, app.show_workbench))
        for selector, show in selectors:
            with self.subTest(selector=str(selector)):
                show()
                for rank, key, state in (('8', 'BackSpace', 0), ('9', 'z', 0x0004)):
                    self.record(rank)
                    original = app.ctrl.ledger.to_list()
                    selector.focus_force(); app.update()
                    self.assertEqual(str(app.tk.call('focus')), str(selector))
                    self.key(selector, key, state); self.wait_saved()
                    self.assertEqual(self.live_deals(), [])
                    self.assertEqual(app.ctrl.ledger.to_list()[:len(original)], original)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(),
                         app.ctrl.ledger.to_list())
        self.assertEqual(self.errors, [])

    def test_finished_analysis_selection_returns_keyboard_to_T_recording(self):
        app = self.app
        selectors = ((app.compact_panel.seat, app.show_compact),
                     (app.compact_panel.hand, app.show_compact),
                     (app.analysis_panel.cmb_analysis_target, app.show_workbench),
                     (app.analysis_panel.cmb_analysis_hand, app.show_workbench),
                     (app.cmb_hand, app.show_workbench))
        for index, (selector, show) in enumerate(selectors):
            with self.subTest(selector=str(selector)):
                show(); selector.focus_force(); app.update()
                selector.event_generate('<<ComboboxSelected>>', when='tail')
                app.update()
                self.assertEqual(str(app.tk.call('focus')), str(app))
                self.key(app, 'T' if index % 2 else 't'); self.wait_saved()
                self.assertEqual(self.live_deals(), [('玩家1', 'T')])
                app.act_undo(); self.wait_saved()
        self.assertEqual(self.errors, [])

    def test_busy_recording_hand_selection_returns_focus_without_losing_next_T(self):
        from blackjack_lab.storage.database import LocalStore
        app = self.app
        app.show_workbench()
        entered, release = threading.Event(), threading.Event()
        append = LocalStore.append_validated
        def held(store, candidate):
            entered.set(); release.wait(5)
            return append(store, candidate)
        try:
            with patch.object(LocalStore, 'append_validated', held):
                app._key_rank('8'); self.assertTrue(entered.wait(2))
                app.cmb_hand.focus_force(); app.update()
                app.cmb_hand.event_generate('<<ComboboxSelected>>', when='tail')
                app.update()
                self.assertEqual(str(app.tk.call('focus')), str(app))
                self.key(app, 't')
                self.assertEqual(len(app._recording_inputs), 2)
                release.set(); self.wait_saved()
        finally:
            release.set()
        self.assertEqual(self.live_deals(), [('玩家1', '8'), ('庄家', 'T')])
        self.assertEqual(self.errors, [])

    def test_bclc_hole_phase_guides_point_mispress_and_accepts_explicit_confirmation(self):
        app = self.app
        app.select_table_mode('bclc')
        # This owned legacy fixture leaves shoe observations unknown and
        # explicitly uses manual hole confirmation, rather than the daily
        # New Shoe entry's declared complete-shoe protocol.
        app.ctrl.replace_shoe(bclc_rules(), app.ctrl.context_token)
        app.var_simple_hole.set(False)
        app.act_new_round()
        app.var_auto_next.set(False)
        self.record('8', '6', '7')
        before = PrefixSnapshot.capture(app.ctrl.ledger)
        button = app.compact_panel.flow_primary
        self.assertEqual(button.cget('text'), '底牌已发 .')
        self.assertEqual(button.winfo_manager(), 'grid')
        app._key_rank('9')
        self.assertFalse(app.recording_busy)
        self.assertFalse(app._recording_faults)
        self.assertEqual(PrefixSnapshot.capture(app.ctrl.ledger), before)
        self.assertIn('底牌已发 .', app.var_recording_save.get())
        button.invoke(); self.wait_saved()
        self.assertEqual(self.live_deals(), [('玩家1', '8'), ('庄家', '6'),
                                            ('玩家1', '7'), ('庄家', None)])
        current = app.ctrl.state().current
        self.assertIsNone(current.rules.start_from_new_shoe)
        self.assertIsNone(current.rules.initial_burn_count)
        self.assertFalse(app.compact_panel.recording_review.winfo_manager())
        self.assertEqual(self.errors, [])

    def test_peek_stage_worker_refusal_is_inline_then_correct_input_continues(self):
        app = self.app
        self.record('8', 'A', '7')
        self.assertEqual(app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        owner = app._recording_owner
        self.assertEqual(owner.use_process, self.use_process)
        before = PrefixSnapshot.capture(app.ctrl.ledger)
        receipts = []
        poll = owner.poll
        def capture():
            result = poll(); receipts.extend(result); return result
        with patch.object(owner, 'poll', side_effect=capture):
            app._key_stand(); self.wait_saved()
        self.assertEqual([r.status for r in receipts], ['rejected'])
        self.assertEqual(receipts[0].before, before)
        self.assertEqual(receipts[0].after, before)
        if self.use_process:
            self.assertIs(receipts[0].verified_token, owner.verification_token)
        self.assertIn('等待实际庄家检查结果', app.var_recording_save.get())
        self.assertFalse(app._recording_faults)
        self.assertFalse(owner.failed)
        self.assertEqual(PrefixSnapshot.capture(app.ctrl.store.load_ledger(app.ctrl.session_id)), before)
        app.compact_panel.flow_primary.invoke(); self.wait_saved()
        self.assertTrue(app.ctrl.state().current.table.dealer_hole_checked_negative)
        app._key_stand(); self.wait_saved()
        self.record('9')
        self.assertEqual(app.ctrl.state().current.table.dealer.hands[0].ranks, ['A', '9'])
        self.assertEqual(self.errors, [])

    def test_commits_and_refusal_keep_unexecuted_successor_original_without_retry(self):
        app = self.app
        app._key_rank('8'); app._key_hole(); app._key_rank('6')
        # Deliberately coalesce these real owner receipts into one UI callback,
        # so a prior successful commit and refused successor share this path.
        if app._recording_after is not None:
            app.after_cancel(app._recording_after)
            app._recording_after = None
        owner = app._recording_owner
        receipts = []
        deadline = perf_counter() + 8
        while len(receipts) < 3 and perf_counter() < deadline:
            receipts.extend(owner.poll()); sleep(.005)
        self.assertEqual([receipt.status for receipt in receipts],
                         ['committed', 'rejected', 'not_executed'])
        with patch.object(owner, 'poll', return_value=receipts):
            app._poll_recording()
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual(self.live_deals(), [('玩家1', '8')])
        self.assertEqual([fault['status'] for fault in app._recording_faults], ['not_executed'])
        fault = app._recording_faults[0]
        path = Path(fault['failure_path'])
        original = path.read_bytes()
        archive = json.loads(original)
        self.assertEqual(archive['arguments']['args'][0]['rank'], '6')
        self.assertFalse(archive['automatic_retry'])
        durable = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app._key_rank('9')
        self.assertFalse(app.recording_busy)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), durable)
        app.act_reconcile_recording()
        self.assertFalse(app._recording_faults)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.live_deals(), [('玩家1', '8')])
        self.record('6')
        self.assertEqual(self.live_deals(), [('玩家1', '8'), ('庄家', '6')])

    def test_rejection_cannot_hide_an_in_place_mutation_of_UI_prefix(self):
        from blackjack_lab.ui.recording_commands import record_input
        app = self.app
        entered, release = threading.Event(), threading.Event()
        def held(ctrl, intent):
            entered.set(); release.wait(5)
            return record_input(ctrl, intent)
        try:
            with patch('blackjack_lab.ui.recording_commands.record_input', side_effect=held):
                app._key_hole()
                self.assertTrue(entered.wait(2))
                app.ctrl.ledger.events[0].payload['note'] = 'temporary UI prefix mutation'
                release.set()
                self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        finally:
            release.set()
        self.assertEqual([fault['status'] for fault in app._recording_faults],
                         ['unknown_commit_outcome'])
        app.act_reconcile_recording()
        self.assertNotEqual(app.ctrl.ledger.events[0].payload['note'], 'temporary UI prefix mutation')
        self.assertEqual(self.live_deals(), [])

    def test_rejection_cannot_accept_a_changed_controller_context_identity(self):
        from blackjack_lab.ui.recording_commands import record_input
        app = self.app
        entered, release = threading.Event(), threading.Event()
        def held(ctrl, intent):
            entered.set(); release.wait(5)
            return record_input(ctrl, intent)
        try:
            with patch('blackjack_lab.ui.recording_commands.record_input', side_effect=held):
                app._key_hole()
                self.assertTrue(entered.wait(2))
                app.ctrl._context_revision += 1
                release.set()
                self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        finally:
            release.set()
        self.assertEqual([fault['status'] for fault in app._recording_faults],
                         ['unknown_commit_outcome'])
        self.assertEqual(self.live_deals(), [])

    def test_malformed_refusal_proof_becomes_unknown_without_Tk_callback_exception(self):
        app = self.app
        self.record('8')
        owner = app._recording_owner
        poll = owner.poll
        callbacks = []
        app.report_callback_exception = lambda *args: callbacks.append(args)
        def malformed():
            return [replace(receipt, ledger=object()) if receipt.status == 'rejected' else receipt
                    for receipt in poll()]
        with patch.object(owner, 'poll', side_effect=malformed):
            app._key_hole()
            self.pump(lambda: not app.recording_busy and owner.stopped)
        self.assertEqual(callbacks, [])
        self.assertEqual([fault['status'] for fault in app._recording_faults],
                         ['unknown_commit_outcome'])
        self.assertEqual(self.live_deals(), [('玩家1', '8')])

    def test_single_refusal_restores_same_prefix_analysis_and_compute_buttons(self):
        app = self.app
        self.record('9', '6', '2')
        panel = app.analysis_panel
        panel.calculate_current()
        self.pump(lambda: panel.last_result is not None and panel.saved is not None, timeout=12)
        result, saved, status = panel.last_result, panel.saved, panel.status.get()
        self.assertEqual(result['status'], 'available', result.get('reason'))
        before = PrefixSnapshot.capture(app.ctrl.ledger)
        app._key_hole()  # No initial hole slot remains; worker refuses before any write.
        self.assertTrue(panel.compute_button.instate(['disabled']))
        self.wait_saved()
        self.assertEqual(PrefixSnapshot.capture(app.ctrl.ledger), before)
        self.assertIs(panel.last_result, result)
        self.assertEqual(panel.saved, saved)
        self.assertEqual(panel.status.get(), status)
        self.assertFalse(panel.compute_button.instate(['disabled']))
        self.assertFalse(app.compact_panel.compute.instate(['disabled']))
        self.assertNotIn('待保存', app.var_opening_ev.get())
        self.assertNotEqual(app.compact_panel.model.state, '建议暂停')

    def refusal_while_changing_analysis(self, change):
        from blackjack_lab.ui.recording_commands import record_input
        app, panel = self.app, self.app.analysis_panel
        self.record('9', '6', '2')
        panel.auto.set(True)
        self.assertIsNotNone(panel._auto_id)
        entered, release = threading.Event(), threading.Event()
        def held(ctrl, intent):
            entered.set(); release.wait(5)
            return record_input(ctrl, intent)
        try:
            with patch.object(panel, 'calculate_current', wraps=panel.calculate_current) as calculate:
                with patch('blackjack_lab.ui.recording_commands.record_input', side_effect=held):
                    app._key_hole()
                    self.assertTrue(entered.wait(2))
                    change(panel)
                    release.set(); self.wait_saved()
                elapsed = []
                app.after(350, lambda: elapsed.append(True))
                self.pump(lambda: bool(elapsed))
                self.assertEqual(calculate.call_count, 0)
        finally:
            release.set()
        self.assertIsNone(panel._auto_id)
        self.assertIsNone(panel.request_id)
        self.assertFalse(app._recording_faults)

    def test_turning_auto_off_during_refusal_does_not_rearm_automatic_calculation(self):
        self.refusal_while_changing_analysis(lambda panel: panel.auto.set(False))
        self.assertFalse(self.app.analysis_panel.auto.get())
        self.assertNotIn('待保存', self.app.analysis_panel.status.get())

    def test_explicit_cancel_during_refusal_stays_cancelled_without_auto_restart(self):
        def cancel_then_poll_again(panel):
            panel.cancel()
            panel.pause_recording()  # A poll may repaint while the refused input is still pending.
        self.refusal_while_changing_analysis(cancel_then_poll_again)
        self.assertTrue(self.app.analysis_panel.auto.get())
        self.assertEqual(self.app.analysis_panel._auto_suppressed_key,
                         self.app.analysis_panel._live_key())
        self.assertIn('已取消', self.app.analysis_panel.status.get())
        self.assertIn('已取消', self.app.var_opening_ev.get())
        self.assertFalse(self.app.analysis_panel.compute_button.instate(['disabled']))
        self.assertFalse(self.app.compact_panel.compute.instate(['disabled']))


class ProcessRecordingWorkflowUITests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved
    record = RecordingWorkflowUITests.record
    live_deals = RecordingWorkflowUITests.live_deals

    test_peek_stage_worker_refusal_is_inline_then_correct_input_continues = (
        RecordingWorkflowUITests.test_peek_stage_worker_refusal_is_inline_then_correct_input_continues)
    test_commits_and_refusal_keep_unexecuted_successor_original_without_retry = (
        RecordingWorkflowUITests.test_commits_and_refusal_keep_unexecuted_successor_original_without_retry)

    def test_rejected_process_receipt_without_owner_token_remains_unknown(self):
        app = self.app
        self.record('8', 'A', '7')
        owner = app._recording_owner
        poll = owner.poll
        def altered():
            return [replace(receipt, verified_token=None) if receipt.status == 'rejected' else receipt
                    for receipt in poll()]
        with patch.object(owner, 'poll', side_effect=altered):
            app._key_stand()
            self.pump(lambda: not app.recording_busy and owner.stopped)
        self.assertEqual([fault['status'] for fault in app._recording_faults],
                         ['unknown_commit_outcome'])
        self.assertFalse(app.ctrl.state().current.table.dealer_hole_checked_negative)


if __name__ == '__main__':
    unittest.main()
