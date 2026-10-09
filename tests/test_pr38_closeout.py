"""Real buttons and the default spawned owner at the pending-input boundary."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.core.table import ACTION_SPLIT
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.table_modes import BCLC, SETTINGS_SCHEMA, TableModeStore, default_settings
from scripts.tk_lifecycle import close_app
from tests import test_recording_background_ui as fixture


class RecordingCloseoutTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved
    deals = fixture.RecordingBackgroundUITests.deals

    def click(self, rank):
        button = next(b for b in self.app.workbench_card_buttons if b.cget('text') == rank)
        self.assertFalse(button.instate(['disabled']))
        button.invoke()

    def result(self, unsaved=False):
        for rank in ('9', '6', '2'):
            self.app._key_rank(rank)
        self.wait_saved()
        self.app.analysis_panel.calculate_current()
        if unsaved:
            with patch.object(self.app.ctrl.analysis_store, 'save', side_effect=OSError('controlled disk full')):
                self.pump(lambda: self.app.analysis_panel.last_result is not None)
        else:
            self.pump(lambda: self.app.analysis_panel.last_result is not None)
        result = self.app.analysis_panel.last_result
        self.assertEqual(result['status'], 'available', result.get('reason'))
        return copy.deepcopy(result)

    def assert_paused(self):
        app = self.app
        self.assertIn('当前建议暂停', app.compact_panel.message.get())
        self.assertFalse(app.compact_panel.model.choices)
        self.assertEqual(app.compact_panel.decision_evs.get(), '')
        self.assertIn('暂停', app.var_opening_ev.get())
        self.assertIn('暂停', app.analysis_panel.text.get('1.0', 'end-1c'))

    def test_fast_workbench_mouse_and_equal_cards_follow_committed_plan(self):
        self.click('8'); self.click('8'); self.click('8')
        self.assertTrue(self.app.recording_busy)
        self.wait_saved()
        self.assertEqual(self.deals()[:3], [('玩家1', '8'), ('庄家', '8'), ('玩家1', '8')])

    def test_mixed_compact_workbench_and_keyboard_share_one_fifo_plan(self):
        self.app.compact_panel.card_buttons[7].invoke()  # 8
        self.click('6'); self.app._key_rank('2')
        self.wait_saved()
        self.assertEqual(self.deals()[:3], [('玩家1', '8'), ('庄家', '6'), ('玩家1', '2')])

    def test_reverse_seven_seats_and_split_both_initial_use_mouse(self):
        app = self.app
        app.select_table_mode(BCLC); app.act_new_shoe()
        for var in app.var_participants.values():
            var.set(True)
        app.act_new_round()
        for rank in ['9'] * 7 + ['6'] + ['2'] * 7:
            self.click(rank)
        app._key_hole(); self.wait_saved()
        expected = [f'玩家{i}' for i in range(7, 0, -1)] + ['庄家']
        self.assertEqual([seat for seat, _ in self.deals()], expected * 2)
        app.act_new_shoe()
        for seat, var in app.var_participants.items():
            var.set(seat == '玩家1')
        app.act_new_round()
        self.click('8'); self.click('6'); self.click('8'); app._key_hole()
        app.act_action(ACTION_SPLIT); self.click('3'); self.click('9')
        self.wait_saved()
        hands = app.ctrl.state().current.table.players['玩家1'].hands
        self.assertEqual([h.ranks for h in hands], [['8', '3'], ['8', '9']])
        self.assertEqual(app.ctrl.state().current.table.split_order_violations, [])

    def test_mouse_reveal_and_automatic_next_follow_new_round(self):
        app = self.app; app.var_auto_next.set(True)
        self.click('9'); self.click('6'); self.click('8'); app._key_stand()
        self.click('K'); self.click('A'); self.click('5')
        self.wait_saved()
        current = app.ctrl.state().current
        self.assertEqual(current.table.round_no, 2)
        self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['5'])
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype == 'CARD_REVEALED']), 1)

    def test_manual_selection_is_explicit_and_pending_manual_input_is_rejected(self):
        app = self.app
        app.var_target.set('庄家'); app._on_recording_seat_clicked()
        self.click('6')
        count = len(app._recording_inputs)
        self.click('8')
        self.assertEqual(len(app._recording_inputs), count)
        self.assertIn('未接收', app.var_recording_save.get())
        self.wait_saved()
        self.assertEqual(self.deals(), [('庄家', '6')])
        app.var_target.set('玩家1'); app._on_recording_seat_clicked()
        self.click('8'); self.wait_saved()
        self.assertEqual(self.deals(), [('庄家', '6'), ('玩家1', '8')])

    def test_existing_unsaved_result_is_paused_immediately_through_slow_write(self):
        app = self.app; original = self.result(unsaved=True)
        pending = {k: copy.deepcopy(v['result']) for k, v in app.pending_analysis.items()}
        events = app.ctrl.ledger.to_list()
        blocker = sqlite3.connect(self.db)
        blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('5'); self.assert_paused()
            self.assertEqual(app.ctrl.ledger.to_list(), events)
            for _ in range(3):
                app.update(); self.assert_paused()
            self.assertEqual({k: v['result'] for k, v in app.pending_analysis.items()}, pending)
            self.assertEqual(app.analysis_panel.last_result, original)
        finally:
            blocker.rollback(); blocker.close()
        self.wait_saved()
        self.assertEqual(app.pending_analysis[original['request_id']]['result'], original)
        self.assertFalse(app.compact_panel.model.choices)
        app.analysis_panel.calculate_current()
        self.pump(lambda: app.analysis_panel.last_result is not None)
        latest = app.analysis_panel.last_result
        self.assertEqual(latest['status'], 'available')
        self.assertEqual(latest['input']['prefix_digest'], app.ctrl.read_prefix().prefix_digest)
        self.assertNotEqual(latest['input_digest'], original['input_digest'])
        self.assertTrue(app.compact_panel.model.choices)

    def test_computing_result_cannot_become_current_during_batch(self):
        self.result()
        app = self.app
        app.analysis_panel.calculate_current()
        old_digest = app.analysis_panel.request_digest
        self.click('3'); self.click('2')
        self.assert_paused()
        app.analysis_panel._poll(); self.assert_paused()
        self.wait_saved()
        self.assertFalse(app.compact_panel.model.choices)
        app.analysis_panel.calculate_current()
        self.pump(lambda: app.analysis_panel.last_result is not None)
        self.assertNotEqual(app.analysis_panel.last_result['input_digest'], old_digest)

    def test_failed_input_and_exit_return_keep_current_advice_paused(self):
        self.result()
        app = self.app
        app._queue_recording('rank', 'Z'); self.assert_paused()
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assert_paused()
        app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        flow = app.exit_flow; flow.return_to_app()
        self.assertFalse(app._closing)
        self.assertEqual(flow.phase, 'returned')
        self.assert_paused()
        self.assertTrue(all(Path(f['failure_path']).is_file() for f in app._recording_faults))

    def test_unknown_commit_outcome_keeps_old_current_result_hidden(self):
        self.result()
        app = self.app
        blocker = sqlite3.connect(self.db)
        blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('5'); self.assert_paused()
            app._recording_owner.thread.terminate()
        finally:
            blocker.rollback(); blocker.close()
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertTrue(app._recording_faults)
        self.assert_paused()
        prefix = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app.act_reconcile_recording()
        self.assertEqual(app.ctrl.ledger.to_list(), prefix)
        self.assertFalse(app.compact_panel.model.choices)

    def test_explicit_reveal_is_not_accepted_into_automatic_pending_batch(self):
        app = self.app
        self.click('8')
        accepted = tuple(app._recording_inputs)
        app.var_mode.set('揭示'); self.click('6')
        self.assertEqual(tuple(app._recording_inputs), accepted)
        self.assertIn('手动输入未接收', app.var_recording_save.get())
        app.var_mode.set('新发牌'); self.wait_saved()
        self.assertEqual(self.deals(), [('玩家1', '8')])

    def test_pending_advice_pause_does_not_rebuild_live_probabilities(self):
        app = self.app
        with patch.object(app.compact_panel, 'live_identity', side_effect=AssertionError('pending redraw rebuilt probabilities')):
            self.click('8'); self.assert_paused()
            app.analysis_panel._poll(); app.opening_estimate.poll()
            app.compact_panel.render(); self.assert_paused()
        self.wait_saved()

    def test_bclc_workbench_label_uses_no_das_rule_field(self):
        app = self.app; app.select_table_mode(BCLC); app.act_new_shoe()
        self.assertIn('两手先补齐无DAS', app.var_topinfo.get())


class ModeFormatCloseoutTests(unittest.TestCase):
    def test_roots_and_nested_shapes_raise_controlled_error_without_rewriting(self):
        valid = {'schema': SETTINGS_SCHEMA, 'selected': 'pragmatic', 'modes': {}}
        setting = default_settings(BCLC).to_dict()
        cases = [[], None, 'text', dict(valid, modes=[]), dict(valid, selected=[]),
                 dict(valid, modes={BCLC: []})]
        for rules in ([], None, 'text', {}, {'unknown_field': True}):
            cases.append(dict(valid, modes={BCLC: dict(setting, rules=rules)}))
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / 'shape.db'; path = Path(str(db) + '.table-modes.json')
            for value in cases:
                with self.subTest(value=value):
                    raw = json.dumps(value).encode('utf-8'); path.write_bytes(raw)
                    with self.assertRaises(ValueError):
                        TableModeStore(db)
                    self.assertEqual(path.read_bytes(), raw)
            path.write_text(json.dumps(valid), encoding='utf-8')
            self.assertEqual(TableModeStore(db).modes, {})


class DamagedModeRecoveryTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved
    click = RecordingCloseoutTests.click
    def test_bad_mode_file_still_recovers_healthy_database_and_locked_rules(self):
        app = self.app; self.click('8'); self.wait_saved()
        events = app.ctrl.ledger.to_list(); rules = app.ctrl.state().current.rules.to_json()
        close_app(app, discard_fixture_results=True); self.app = None
        path = Path(str(self.db) + '.table-modes.json'); raw = b'null'; path.write_bytes(raw)
        self.app = BlackjackLabApp(self.db, auto_analysis=False, background_recording=True, recording_process=True)
        self.assertIsNone(self.app.table_mode_store)
        self.assertEqual(self.app.ctrl.ledger.to_list(), events)
        self.assertEqual(self.app.ctrl.state().current.rules.to_json(), rules)
        self.app.select_table_mode(BCLC)
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(self.app.ctrl.ledger.to_list(), events)
