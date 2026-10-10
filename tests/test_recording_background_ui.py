"""Actual Tk FIFO recording, fault reconciliation and cooperative close."""
from pathlib import Path
import tempfile
import threading
from time import perf_counter, sleep
import tkinter as tk
import unittest
from unittest.mock import patch

from blackjack_lab.ledger.events import CARD_DEALT, SOURCE_SIMULATOR
from blackjack_lab.core.rules import CONFIRM_UNKNOWN
from blackjack_lab.core.table import ACTION_SPLIT, ACTION_DOUBLE
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.deal_entry import MODE_UNALIGNED
from scripts.tk_lifecycle import close_app


class RecordingBackgroundUITests(unittest.TestCase):
    use_process = False
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'background-ui.db'
        self.errors = []
        for name, effect in [('askyesno', lambda *a, **k: True), ('showinfo', lambda *a, **k: None),
                             ('showerror', lambda *a, **k: self.errors.append(a))]:
            context = patch('blackjack_lab.ui.app.messagebox.' + name, side_effect=effect)
            context.start(); self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db, recording_source=SOURCE_SIMULATOR,
                                   auto_analysis=False, background_recording=True, recording_process=self.use_process,
                                   sidebet_research=getattr(self, 'sidebet_research', False))
        self.addCleanup(self.close)
        self.app.var_auto_next.set(False)
        self.app.act_new_shoe()
        self.app.act_new_round()
        self.app.update()

    def pump(self, predicate, timeout=8):
        end = perf_counter() + timeout
        while perf_counter() < end:
            try:
                self.app.update()
            except tk.TclError:
                if predicate():
                    return
                raise
            if predicate():
                return
            sleep(.005)
        self.fail('Tk recording did not reach expected state')

    def close(self):
        if self.app:
            self.pump(lambda: not self.app.recording_busy)
            if self.app._recording_faults:
                if self.app._recording_owner:
                    self.pump(lambda: self.app._recording_owner.stopped)
                self.app.act_reconcile_recording()
            close_app(self.app, discard_fixture_results=True)
            self.app = None

    def wait_saved(self):
        self.pump(lambda: not self.app.recording_busy)
        self.assertFalse(self.app._recording_faults, self.app.recording_fault_message())

    def deals(self, ledger=None):
        return [(e.payload['seat'], e.payload['rank']) for e in (ledger or self.app.ctrl.ledger).events
                if e.etype == CARD_DEALT]

    def test_released_equal_keys_and_held_repeat_keep_fifo_seat_order(self):
        app = self.app
        for i, var in enumerate(app.var_participants.values()):
            var.set(i < 3)
        app.act_undo(); self.wait_saved()  # Undo empty round, then freeze three seats.
        app.act_new_round()
        app.focus_force(); app.update()
        for _ in range(4):
            app.event_generate('<KeyPress-2>', when='tail')
        app.event_generate('<KeyRelease-2>', when='tail')
        for key in ('2', '2', '6', '9', '9', '9'):
            app.event_generate('<KeyPress-' + key + '>', when='tail')
            app.event_generate('<KeyRelease-' + key + '>', when='tail')
        app.update()
        self.wait_saved()
        self.assertEqual(self.deals(), [('玩家1', '2'), ('玩家2', '2'), ('玩家3', '2'), ('庄家', '6'),
                                       ('玩家1', '9'), ('玩家2', '9'), ('玩家3', '9'), ('庄家', None)])
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertEqual(self.errors, [])

    def test_pending_write_keeps_tk_alive_and_blocks_context_changes(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        original = LocalStore.append_validated
        owners = []
        def held(store, candidate):
            owners.append(threading.current_thread().name)
            entered.set(); release.wait(5)
            return original(store, candidate)
        try:
            with patch.object(LocalStore, 'append_validated', held):
                app._key_rank('8'); app._key_rank('6')
                self.assertTrue(entered.wait(2))
                saved = app.ctrl.ledger.to_list()
                start = perf_counter()
                app.select_table_mode('bclc'); app.act_new_shoe(); app.act_recover(); app._key_jump(0)
                app.compact_panel.open_correction()
                self.assertLess(perf_counter() - start, .15)
                self.assertEqual(app.var_table_mode.get(), 'pragmatic')
                self.assertEqual(app.ctrl.ledger.to_list(), saved)
                self.assertFalse(app.compact_panel.editor_open)
                beats = []
                for _ in range(15):
                    app.after(0, lambda: beats.append(perf_counter())); app.update(); sleep(.01)
                self.assertEqual(len(beats), 15)
                release.set(); self.wait_saved()
        finally:
            release.set()
        self.assertEqual(self.deals(), [('玩家1', '8'), ('庄家', '6')])
        self.assertEqual(set(owners), {'blackjack-recording-owner'})

    def test_failed_input_and_later_unexecuted_input_are_retained_without_retry(self):
        app = self.app
        app._key_rank('8'); app._key_rank('Z'); app._key_rank('6')
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        faults = list(app._recording_faults)
        self.assertEqual([f['status'] for f in faults], ['failed_before_commit', 'not_executed'])
        self.assertEqual(self.deals(), [('玩家1', '8')])
        self.assertTrue(all(Path(f['failure_path']).is_file() for f in faults))
        app._key_rank('9')
        self.assertFalse(app.recording_busy)
        app.act_reconcile_recording()
        self.assertFalse(app._recording_faults)
        self.assertEqual(self.deals(), [('玩家1', '8')])
        self.assertEqual(len(app._recording_fault_history), 2)
        app._key_rank('6'); self.wait_saved()
        self.assertEqual(self.deals(), [('玩家1', '8'), ('庄家', '6')])

    def test_exception_after_write_requires_readback_and_never_repeats_card(self):
        app = self.app; original = LocalStore.append_validated
        def after_write(store, candidate):
            original(store, candidate)
            raise OSError('injected after commit')
        with patch.object(LocalStore, 'append_validated', after_write):
            app._key_rank('8'); app._key_rank('6')
            self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual([f['status'] for f in app._recording_faults], ['unknown_commit_outcome', 'not_executed'])
        self.assertEqual(self.deals(), [])  # No successful UI receipt claimed.
        actual = app.ctrl.store.load_ledger(app.ctrl.session_id)
        self.assertEqual(self.deals(actual), [('玩家1', '8')])
        app.act_reconcile_recording()
        self.assertEqual(self.deals(), [('玩家1', '8')])
        self.assertEqual(app.ctrl.entry_plan.mode, MODE_UNALIGNED)
        self.assertTrue(app.ctrl.entry_plan.input_paused)

    def test_close_drains_accepted_cards_before_database_and_window_close(self):
        app = self.app; session = app.ctrl.session_id
        original = LocalStore.append_validated
        entered, release = threading.Event(), threading.Event()
        def held(store, candidate):
            entered.set(); release.wait(5)
            return original(store, candidate)
        try:
            with patch.object(LocalStore, 'append_validated', held):
                app._key_rank('8'); app._key_rank('6')
                self.assertTrue(entered.wait(2))
                owner = app._recording_owner
                start = perf_counter(); app.on_close()
                self.assertLess(perf_counter() - start, .15)
                app._key_rank('9')
                self.assertTrue(app._closing)
                for _ in range(15):
                    app.update(); sleep(.01)
                self.assertTrue(app.winfo_exists())
                self.assertFalse(owner.stopped)
                release.set()
                self.pump(lambda: app.exit_flow.phase == 'finished')
        finally:
            release.set()
        self.assertTrue(owner.stopped)
        self.app = None
        recovered = SessionController.recover(self.db, session)
        try:
            self.assertEqual(self.deals(recovered.ledger), [('玩家1', '8'), ('庄家', '6')])
            self.assertEqual(recovered.entry_plan.next_card_text(), '下一张给：玩家1，第2张')
        finally:
            recovered.close()

    def test_close_with_failure_waits_for_explicit_retained_input_decision(self):
        app = self.app
        app._key_rank('Z'); app._key_rank('6'); app.on_close()
        self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        self.assertTrue(app.winfo_exists())
        self.assertTrue(app._recording_owner.stopped)
        self.assertFalse(app.exit_flow.recording_acknowledged)
        paths = [Path(f['failure_path']) for f in app._recording_faults]
        app.exit_flow.discard()
        self.pump(lambda: app.exit_flow.phase == 'finished')
        self.assertTrue(app.exit_flow.recording_acknowledged)
        self.assertTrue(all(p.is_file() for p in paths))
        self.app = None

    def test_bclc_initial_flow_requires_explicit_hole_and_keeps_unknown_rules(self):
        app = self.app
        app.select_table_mode('bclc')
        from blackjack_lab.ui.table_modes import legacy_bclc_draft_rules
        app._set_rule_form(legacy_bclc_draft_rules()); app.act_new_shoe()
        for var in app.var_participants.values():
            var.set(True)
        app.act_new_round()
        for rank in ['9'] * 7 + ['6'] + ['2'] * 7:
            app._key_rank(rank)
        app._key_hole(); self.wait_saved()
        self.assertEqual([seat for seat, rank in self.deals()],
                         [f'玩家{i}' for i in range(7, 0, -1)] + ['庄家'] +
                         [f'玩家{i}' for i in range(7, 0, -1)] + ['庄家'])
        state = app.ctrl.state().current
        self.assertEqual(state.shoe.physical_remaining(), 400)
        self.assertEqual(state.rules.confirm_status, CONFIRM_UNKNOWN)
        self.assertIsNone(state.rules.check_bj_when)
        self.assertIsNone(state.rules.burn_cards_known)
        self.assertFalse(state.table.dealer_hole_checked_negative)

    def test_split_double_dealer_reveal_and_automatic_next_share_ordered_plan(self):
        app = self.app
        app.var_auto_next.set(True)
        for rank in ('8', '6', '8'):
            app._key_rank(rank)
        app.act_action(ACTION_SPLIT)
        app._key_rank('3'); app._key_rank('9')
        app.act_action(ACTION_DOUBLE)
        app._key_rank('T'); app._key_stand()
        app._key_rank('K'); app._key_rank('A'); app._key_rank('5')
        self.wait_saved()
        state = app.ctrl.state().current
        self.assertEqual(state.table.round_no, 2)
        self.assertEqual(state.table.players['玩家1'].hands[0].ranks, ['5'])
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(state.table.split_order_violations, [])
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype == 'CARD_REVEALED']), 1)
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype == 'ROUND_ENDED']), 1)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())
        app.act_undo(); self.wait_saved()
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)
        app.act_undo(); self.wait_saved()
        self.assertEqual(app.ctrl.state().current.table.round_no, 1)
        self.assertEqual(app.var_target.get(), '庄家')

    def test_bclc_actual_hole_reveal_does_not_invent_a_third_card_or_soft17_rule(self):
        app = self.app
        from blackjack_lab.ui.table_modes import legacy_bclc_draft_rules
        app.select_table_mode('bclc'); app._set_rule_form(legacy_bclc_draft_rules())
        app.act_new_shoe(); app.act_new_round()
        for rank in ('9', 'A', '8'):
            app._key_rank(rank)
        app._key_hole(); app._key_stand(); app._key_rank('6')
        self.wait_saved()
        state = app.ctrl.state().current
        self.assertEqual(state.table.dealer.hands[0].ranks, ['A', '6'])
        self.assertEqual(state.shoe.physical_remaining(), 412)
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype == 'CARD_REVEALED']), 1)
        self.assertIsNone(state.rules.dealer_soft17)
        self.assertFalse(state.table.dealer_hole_checked_negative)
        self.assertEqual(app.dealer_recording_finished(), '')
        app._key_rank('2'); self.wait_saved()
        self.assertEqual(app.ctrl.state().current.table.dealer.hands[0].ranks, ['A', '6', '2'])
        self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), 411)

    def test_pending_mode_switch_keeps_current_flow_and_stops_automatic_next(self):
        app = self.app
        for rank in ('9', '6', '8'):
            app._key_rank(rank)
        app._key_stand(); self.wait_saved()
        locked = app.ctrl.state().current.rules.to_json()
        app.select_table_mode('bclc')
        self.assertTrue(app.var_auto_next.get())
        app._key_rank('K'); app._key_rank('A'); self.wait_saved()
        state = app.ctrl.state().current
        self.assertEqual(state.table.round_no, 1)
        self.assertEqual(state.rules.to_json(), locked)
        self.assertEqual(state.table.dealer.hands[0].ranks, ['6', 'K', 'A'])
        before = app.ctrl.ledger.to_list()
        app.act_complete_and_next(state.round_id)
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertIn('先新建牌靴', self.errors[-1][1])

    def test_in_place_ui_prefix_change_cannot_publish_a_success_receipt(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        original = LocalStore.append_validated
        def held(store, candidate):
            entered.set(); release.wait(5)
            return original(store, candidate)
        try:
            with patch.object(LocalStore, 'append_validated', held):
                app._key_rank('8')
                self.assertTrue(entered.wait(2))
                app.ctrl.ledger.events[0].payload['note'] = 'injected in-place mutation'
                release.set()
                self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        finally:
            release.set()
        self.assertEqual(app._recording_faults[0]['status'], 'unknown_commit_outcome')
        self.assertEqual(self.deals(), [])
        app.act_reconcile_recording()
        self.assertEqual(self.deals(), [('玩家1', '8')])
        self.assertNotEqual(app.ctrl.ledger.events[0].payload['note'], 'injected in-place mutation')

    def test_unreviewed_failure_is_loaded_on_restart_without_automatic_retry(self):
        app = self.app
        entered, release = threading.Event(), threading.Event()
        original = SessionController.recover
        def held(*args, **kwargs):
            entered.set(); release.wait(5)
            return original(*args, **kwargs)
        try:
            with patch.object(SessionController, 'recover', side_effect=held):
                app._key_rank('Z'); self.assertTrue(entered.wait(2))
                app._key_rank('6')
                self.assertEqual(len(app._recording_inputs), 2)
                release.set(); app.on_close()
                self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        finally:
            release.set()
        app.exit_flow.discard()
        self.pump(lambda: app.exit_flow.phase == 'finished')
        self.app = BlackjackLabApp(self.db, recording_source=SOURCE_SIMULATOR,
                                   auto_analysis=False, background_recording=True, recording_process=self.use_process)
        app = self.app
        self.assertEqual([f['status'] for f in app._recording_faults], ['failed_before_commit', 'not_executed'])
        self.assertEqual(self.deals(), [])
        app._key_rank('8'); self.assertFalse(app.recording_busy)
        app.act_reconcile_recording()
        self.assertFalse(app._recording_faults)
        close_app(app, discard_fixture_results=True)
        self.app = BlackjackLabApp(self.db, recording_source=SOURCE_SIMULATOR,
                                   auto_analysis=False, background_recording=True, recording_process=self.use_process)
        self.assertFalse(self.app._recording_faults)
        self.assertEqual(self.deals(), [])


if __name__ == '__main__':
    unittest.main()
