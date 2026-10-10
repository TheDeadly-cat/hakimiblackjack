"""Actual side-bet windows on the normal spawned recording path."""
import copy
from dataclasses import replace
import itertools
import json
from pathlib import Path
import sqlite3
from time import sleep
import unittest
from unittest.mock import patch

from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, bclc_rules
from scripts.tk_lifecycle import close_app
from tests import test_recording_background_ui as fixture


def post_commit_failure_process(db_path, source, inputs, outputs):
    from blackjack_lab.storage.database import LocalStore
    from blackjack_lab.ui.recording_process import run_recording_process
    original = LocalStore.append_validated
    def after_commit(store, candidate):
        original(store, candidate)
        raise OSError('controlled exception after actual SQLite commit')
    with patch.object(LocalStore, 'append_validated', after_commit):
        run_recording_process(db_path, source, inputs, outputs)


class SidebetRecordingStateTests(unittest.TestCase):
    use_process = True
    def setUp(self):
        self.sidebet_research=True
        fixture.RecordingBackgroundUITests.setUp(self)
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved

    def close(self):
        if not self.app:
            return
        self.pump(lambda: not self.app.recording_busy)
        if self.app._recording_faults:
            if self.app._recording_owner:
                self.pump(lambda: self.app._recording_owner.stopped)
            self.app.act_reconcile_recording()
        close_app(self.app, discard_fixture_results=True)
        self.app = None

    def click(self, rank):
        next(b for b in self.app.workbench_card_buttons if b.cget('text') == rank).invoke()

    def forecast(self, open_details=True):
        view = self.app.sidebets
        view.enabled.set(True); view.refresh()
        self.pump(lambda: '玩家1' in view.forecasts)
        value = view.forecasts['玩家1']
        self.assertEqual(value['result']['status'], 'available')
        self.assertIn('发牌前', view.lines['perfect_pairs'].get())
        if open_details:
            view.show_details(); self.app.update()
            self.assertIn('发牌前预测', view.details.text.get('1.0', 'end-1c'))
        return value

    def assert_paused(self):
        app = self.app; view = app.sidebets
        for var in view.lines.values():
            self.assertIn('当前暂停使用', var.get())
            self.assertNotIn('%', var.get())
            self.assertNotIn('发牌前', var.get())
        if view.details:
            text = view.details.text.get('1.0', 'end-1c')
            self.assertIn('当前暂停使用', text)
            self.assertNotIn('%', text)
            self.assertNotIn('发牌前预测', text)
        self.assertIn('暂停', app.compact_panel.message.get())
        self.assertIn('暂停', app.var_opening_ev.get())
        self.assertNotIn('%', app.compact_panel.identity.get())
        self.assertNotIn('研究EV', app.compact_panel.flow_message.get())

    def test_pending_real_card_hides_forecast_and_open_details_without_losing_record(self):
        original = self.forecast()
        app = self.app; view = app.sidebets
        file = view.store.directory / (original['saved_id'] + '.json')
        raw = file.read_bytes(); events = app.ctrl.ledger.to_list()
        blocker = sqlite3.connect(self.db); blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('8'); self.assert_paused()
            self.assertEqual(app.ctrl.ledger.to_list(), events)
            self.assertEqual(view.forecasts['玩家1'], original)
            for _ in range(5):
                app.update(); sleep(.015); self.assert_paused()
            self.assertEqual(file.read_bytes(), raw)
        finally:
            blocker.rollback(); blocker.close()
        self.wait_saved()
        self.pump(lambda: view.sealed)
        self.assertIn('已封盘', view.lines['perfect_pairs'].get())
        self.assertNotIn('发牌前预测', view.details.text.get('1.0', 'end-1c'))
        self.assertEqual(file.read_bytes(), raw)

    def test_details_opened_after_acceptance_is_also_paused(self):
        self.forecast(open_details=False)
        blocker = sqlite3.connect(self.db); blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('8')
            self.app.sidebets.show_details()
            self.assert_paused()
        finally:
            blocker.rollback(); blocker.close()
        self.wait_saved()

    def test_recording_failure_and_exit_return_keep_all_current_outputs_paused(self):
        original = self.forecast()
        app = self.app
        app._queue_recording('rank', 'Z')
        self.assert_paused()
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual(app._recording_faults[0]['status'], 'failed_before_commit')
        self.assert_paused()
        app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        app.exit_flow.return_to_app()
        self.assertFalse(app._closing); self.assert_paused()
        self.assertEqual(app.sidebets.forecasts['玩家1'], original)
        app.act_reconcile_recording()
        self.pump(lambda: '当前暂停使用' not in app.sidebets.lines['perfect_pairs'].get())
        self.assertFalse(app._recording_faults)

    def test_process_death_requires_readback_and_never_restores_stale_current_forecast(self):
        self.forecast()
        app = self.app
        blocker = sqlite3.connect(self.db); blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('8'); app._recording_owner.thread.terminate()
        finally:
            blocker.rollback(); blocker.close()
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertTrue(app._recording_faults)
        self.assert_paused()
        actual = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app.act_reconcile_recording()
        self.assertEqual(app.ctrl.ledger.to_list(), actual)

    def test_unknown_outcome_after_actual_commit_stays_paused_until_plan_review(self):
        original = self.forecast()
        app = self.app; before = app.ctrl.ledger.to_list()
        with patch('blackjack_lab.ui.recording_process.run_recording_process', post_commit_failure_process):
            self.click('8')
            self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        self.assertEqual(app._recording_faults[0]['status'], 'unknown_commit_outcome')
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        actual = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        self.assertGreater(len(actual), len(before))
        self.assert_paused()
        app.act_reconcile_recording()
        self.assertFalse(app._recording_faults)
        self.assertEqual(app.ctrl.ledger.to_list(), actual)
        self.assertTrue(app.ctrl.entry_plan.input_paused)
        self.assertNotEqual(app.ctrl.entry_plan.ledger_seq, app.ctrl.ledger.events[-1].seq)
        self.assert_paused()
        self.assertEqual(app.sidebets.forecasts['玩家1']['result'], original['result'])
        self.reopen()
        self.app.sidebets.show_details()
        self.assert_paused()

    def test_unsaved_forecast_is_retained_through_pause_and_failed_exit_save(self):
        app = self.app; view = app.sidebets
        with patch.object(view.store, 'save', side_effect=OSError('controlled side-bet snapshot disk fault')):
            original = self.forecast()
            self.assertIsNone(original['saved_id'])
            request = original['result']['request_id']
            frozen = copy.deepcopy(view.pending_saves[request])
            self.click('8'); self.assert_paused(); self.wait_saved()
            app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_save')
            app.exit_flow.return_to_app()
            self.assertEqual(view.pending_saves[request], frozen)
            self.assertEqual(view.forecasts['玩家1']['result'], original['result'])
        view.retry_saves()
        self.pump(lambda: request not in view.pending_saves)
        with view._lock:
            saved_id = view.saved_ids[request]
        saved = view.store.load(saved_id)
        self.assertEqual(saved['result'], frozen['result'])
        self.assertEqual(saved['event_prefix'], frozen['event_prefix'])

    def bclc(self, observations=False):
        app = self.app; app.select_table_mode(BCLC)
        if observations:
            rules = bclc_rules()
            rules.start_from_new_shoe = rules.burn_cards_known = True
            rules.initial_burn_count = 0
            app._set_rule_form(rules)
        app.act_new_shoe(); app.act_new_round()
        self.assertEqual(app.sidebets._profile_mode, BCLC)

    def reopen(self):
        close_app(self.app, discard_fixture_results=True)
        self.app = None
        self.app = BlackjackLabApp(self.db, auto_analysis=False,
                                   background_recording=True, recording_process=True, sidebet_research=True)
        self.app.update()

    def test_bclc_default_unknowns_open_real_settings_window(self):
        self.bclc()
        view = self.app.sidebets
        self.assertEqual((view.profile.a23, view.profile.qka, view.profile.ka2), (None, None, None))
        view.show_details(); self.app.update()
        self.assertEqual([v.get() for v in view.details.aces.values()], ['待确认'] * 3)
        self.assertEqual(self.errors, [])

    def test_bclc_selected_without_shoe_uses_its_own_unknown_settings(self):
        close_app(self.app, discard_fixture_results=True); self.app = None
        self.db = Path(self.tmp.name) / 'before-shoe.db'
        self.app = BlackjackLabApp(self.db, auto_analysis=False,
                                   background_recording=True, recording_process=True, sidebet_research=True)
        self.app.select_table_mode(BCLC); view = self.app.sidebets
        self.assertEqual(view._profile_mode, BCLC)
        view.show_details()
        self.assertEqual([v.get() for v in view.details.aces.values()], ['待确认'] * 3)
        self.assertIn('BCLC', view.details.profile_scope.get())

    def test_unknowns_cannot_be_saved_as_verified_or_invalid_choices_coerced(self):
        self.bclc(); view = self.app.sidebets; view.show_details()
        profile = view.profile; path = view.settings
        original = path.read_bytes() if path.exists() else None
        dialog = view.details
        dialog.status.set('真实桌规已核对'); dialog.source.set('Owned synthetic test source')
        dialog.apply()
        self.assertEqual(view.profile, profile)
        self.assertIn('未知', dialog.error.get())
        dialog.status.set('赔付未核对'); dialog.aces['a23'].set('invalid-choice'); dialog.apply()
        self.assertEqual(view.profile, profile)
        self.assertEqual(path.read_bytes() if path.exists() else None, original)

    def test_restart_with_unreviewed_fault_keeps_bclc_profile_and_details_paused(self):
        self.bclc(); app = self.app
        app._queue_recording('rank', 'Z')
        self.pump(lambda: not app.recording_busy and app._recording_owner.stopped)
        failure_paths = [Path(f['failure_path']) for f in app._recording_faults]
        originals = [p.read_bytes() for p in failure_paths]
        app.on_close(); self.pump(lambda: app.exit_flow.phase == 'needs_recording')
        app.exit_flow.discard(); self.pump(lambda: app.exit_flow.phase == 'finished')
        self.app = None
        self.app = BlackjackLabApp(self.db, auto_analysis=False,
                                   background_recording=True, recording_process=True, sidebet_research=True)
        view = self.app.sidebets
        self.assertEqual(view._profile_mode, BCLC)
        self.assertIsNone(view.profile.a23)
        view.show_details(); self.assert_paused()
        self.assertEqual([p.read_bytes() for p in failure_paths], originals)

    def test_all_tri_state_combinations_save_and_restart_without_coercion(self):
        self.bclc()
        choices = ((None, '待确认'), (True, '是'), (False, '否'))
        for values in itertools.product(choices, repeat=3):
            with self.subTest(values=values):
                view = self.app.sidebets; view.show_details()
                dialog = view.details
                for field, (_value, label) in zip(('a23', 'qka', 'ka2'), values):
                    dialog.aces[field].set(label)
                dialog.apply()
                expected = tuple(value for value, _ in values)
                self.assertEqual((view.profile.a23, view.profile.qka, view.profile.ka2), expected)
                stored = json.loads(view.settings.read_text(encoding='utf-8'))['profile']
                self.assertEqual(tuple(stored[field] for field in ('a23', 'qka', 'ka2')), expected)
                self.reopen(); restored = self.app.sidebets
                self.assertEqual((restored.profile.a23, restored.profile.qka, restored.profile.ka2), expected)
                restored.show_details()
                self.assertEqual(tuple(restored.details.aces[field].get() for field in ('a23', 'qka', 'ka2')),
                                 tuple(label for _, label in values))

    def test_open_editor_tracks_locked_mode_and_profiles_do_not_mix(self):
        app = self.app; view = app.sidebets
        view.apply_profile(replace(view.profile, a23=False, qka=True, ka2=True))
        pragma_file = view.settings; pragma_raw = pragma_file.read_bytes()
        view.show_details()
        self.bclc()
        self.assertIs(view.profile.a23, None)
        self.assertEqual(view.details.aces['a23'].get(), '待确认')
        view.details.aces['qka'].set('否'); view.details.apply()
        bclc_file = view.settings; bclc_raw = bclc_file.read_bytes()
        app.select_table_mode(PRAGMATIC)
        self.assertEqual(view._profile_mode, BCLC)  # The current shoe is still locked BCLC.
        app.act_new_shoe(); app.act_new_round()
        self.assertEqual(view._profile_mode, PRAGMATIC)
        self.assertEqual((view.profile.a23, view.profile.qka, view.profile.ka2), (False, True, True))
        self.assertEqual(view.details.aces['a23'].get(), '否')
        self.assertEqual(pragma_file.read_bytes(), pragma_raw)
        self.assertEqual(bclc_file.read_bytes(), bclc_raw)
        self.reopen()
        self.assertEqual(self.app.sidebets._profile_mode, PRAGMATIC)
        self.assertEqual(self.app.sidebets.profile.a23, False)
        self.app.select_table_mode(BCLC); self.app.act_new_shoe(); self.app.act_new_round()
        self.assertIsNone(self.app.sidebets.profile.a23)
        self.assertIs(self.app.sidebets.profile.qka, False)

    def test_insurance_and_dealer_bj_labels_follow_the_same_pause_state(self):
        self.bclc(observations=True)
        app = self.app
        for rank in ('9', 'A', '8'):
            app._key_rank(rank)
        app._key_hole(); self.wait_saved()
        self.assertIn('研究EV', app.compact_panel.flow_message.get())
        self.assertIn('%', app.compact_panel.identity.get())
        app.sidebets.show_details()
        blocker = sqlite3.connect(self.db); blocker.execute('BEGIN IMMEDIATE')
        try:
            self.click('6'); self.assert_paused()
            for _ in range(5):
                app.update(); sleep(.015); self.assert_paused()
        finally:
            blocker.rollback(); blocker.close()
        self.wait_saved()
        self.pump(lambda: '暂停' not in app.compact_panel.message.get())
        self.assertEqual(app.ctrl.state().current.table.dealer.hands[0].ranks, ['A', '6'])
        self.assertNotIn('研究EV', app.compact_panel.flow_message.get())
