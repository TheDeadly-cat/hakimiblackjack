import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.core.rules import CONFIRM_UNKNOWN, CONFIRM_VERIFIED
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, TableModeStore, default_settings, legacy_bclc_draft_rules, bclc_rules


class TestStoredTableModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'modes.db'

    def test_separate_presets_survive_restart_without_resetting_pragmatic(self):
        store = TableModeStore(self.db)
        pragmatic = store.get(PRAGMATIC)
        pragmatic.rules.n_decks = 6
        pragmatic.deal_direction = 'reverse'
        bclc = store.get(BCLC)
        store.save_switch(PRAGMATIC, pragmatic, BCLC, bclc)
        recovered = TableModeStore(self.db)
        self.assertEqual(recovered.selected, BCLC)
        self.assertEqual(recovered.get(PRAGMATIC).rules.n_decks, 6)
        self.assertEqual(recovered.get(BCLC).rules.n_decks, 8)
        self.assertEqual(recovered.get(PRAGMATIC).deal_direction, 'reverse')
        self.assertEqual(recovered.get(BCLC).rules.confirm_status, CONFIRM_VERIFIED)

    def test_draft_does_not_fabricate_peek_burn_or_complete_shoe(self):
        rules = legacy_bclc_draft_rules()
        for field in ('check_bj_when', 'dealer_bj_extra_bet_rule', 'burn_cards_known',
                      'initial_burn_count', 'start_from_new_shoe', 'dealer_soft17',
                      'double_after_split', 'resplit_aces', 'split_ace_hit_once'):
            self.assertIsNone(getattr(rules, field), field)
        self.assertEqual(rules.confirm_status, CONFIRM_UNKNOWN)
        self.assertEqual(default_settings(BCLC).deal_direction, 'reverse')

    def test_corrupt_saved_settings_are_preserved(self):
        path = Path(str(self.db) + '.table-modes.json')
        path.write_bytes(b'{damaged')
        with self.assertRaises(ValueError):
            TableModeStore(self.db)
        self.assertEqual(path.read_bytes(), b'{damaged')

    def test_another_windows_settings_are_not_silently_overwritten(self):
        first, second = TableModeStore(self.db), TableModeStore(self.db)
        first.save_switch(PRAGMATIC, first.get(PRAGMATIC), BCLC, first.get(BCLC))
        original = first.path.read_bytes()
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            second.save_switch(PRAGMATIC, second.get(PRAGMATIC), PRAGMATIC, second.get(PRAGMATIC))
        self.assertEqual(first.path.read_bytes(), original)

    def test_new_bclc_default_and_explicit_legacy_preference_migration(self):
        store = TableModeStore(self.db)
        self.assertTrue(store.get(BCLC).simple_hole)
        legacy = store.get(BCLC)
        legacy.simple_hole = False
        store.save_switch(PRAGMATIC, store.get(PRAGMATIC), BCLC, legacy)
        original = store.path.read_bytes()
        rules = legacy.rules.to_json()
        recovered = TableModeStore(self.db)
        self.assertFalse(recovered.get(BCLC).simple_hole)
        self.assertEqual(recovered.path.read_bytes(), original)
        updated = recovered.get(BCLC)
        updated.simple_hole = True  # The user explicitly requested this preference update.
        recovered.save_switch(BCLC, updated, BCLC, updated)
        self.assertTrue(updated.simple_hole)
        self.assertEqual(updated.rules.to_json(), rules)
        self.assertEqual(recovered.get(PRAGMATIC).to_dict(), store.get(PRAGMATIC).to_dict())
        self.assertTrue(TableModeStore(self.db).get(BCLC).simple_hole)
        backup = recovered.path.with_name(recovered.path.name + '.history') / (hashlib.sha256(original).hexdigest() + '.json')
        self.assertEqual(backup.read_bytes(), original)


class TestBclcHiddenProtocol(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.ui.controller import SessionController
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctrl = SessionController(Path(self.tmp.name) / 'hidden-protocol.db')
        self.addCleanup(self.ctrl.close)

    def initial(self, up):
        rules = bclc_rules()
        self.ctrl.new_shoe(rules)
        self.ctrl.start_round(['玩家1'], my_seat='玩家1', simple_hole=True)
        for rank in ('9', up, '8'):
            slot = self.ctrl.entry_plan.slot()
            self.ctrl.deal_shown(slot.seat, rank, initial_slot_id=slot.slot_id)
        return rules

    def test_non_ace_including_ten_auto_registers_unknown_hole_and_enters_players(self):
        from blackjack_lab.core.rules import requires_bj_peek
        rules = self.initial('T')
        for rank in ('2', '3', '4', '5', '6', '7', '8', '9', 'T'):
            self.assertFalse(requires_bj_peek(rules, rank))
        self.assertEqual(self.ctrl.entry_plan.mode, 'player_continuation')
        hidden = self.ctrl.ledger.events[-1]
        self.assertEqual(hidden.payload['face_state'], 'hidden')
        self.assertIsNone(hidden.payload['rank'])
        self.assertEqual(self.ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertFalse(any(event.etype in ('PEEK_NEGATIVE', 'CARD_REVEALED') for event in self.ctrl.ledger.events))

    def test_ace_registers_only_hole_existence_and_waits_for_actual_check(self):
        self.initial('A')
        self.assertEqual(self.ctrl.entry_plan.mode, 'peek_wait')
        self.assertIsNone(self.ctrl.ledger.events[-1].payload['rank'])
        self.assertFalse(self.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertFalse(any(event.etype in ('PEEK_NEGATIVE', 'CARD_REVEALED') for event in self.ctrl.ledger.events))
        self.assertEqual(self.ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(self.ctrl.state().current.shoe.physical_remaining(), 412)

    def test_unknown_shoe_observations_allow_recording_and_group_undo_but_not_ev(self):
        rules = self.initial('6')
        locked = self.ctrl.state().current.rules.to_json()
        for field in ('start_from_new_shoe', 'burn_cards_known', 'initial_burn_count'):
            self.assertIsNone(getattr(self.ctrl.state().current.rules, field))
        self.assertEqual(self.ctrl.entry_plan.mode, 'player_continuation')
        with self.assertRaises(InputUnavailable) as error:
            self.ctrl.analysis_input('玩家1')
        self.assertEqual(error.exception.code, 'START_UNKNOWN')
        before = self.ctrl.ledger.to_list()
        trigger, hole = self.ctrl.ledger.events[-2:]
        self.ctrl.undo_last()
        self.assertEqual(self.ctrl.ledger.to_list()[:-2], before)
        self.assertEqual([event.payload['target_event_id'] for event in self.ctrl.ledger.events[-2:]],
                         [hole.event_id, trigger.event_id])
        self.assertEqual(self.ctrl.entry_plan.next_card_text(), '下一张给：玩家1，第2张')
        self.assertEqual(self.ctrl.state().current.rules.to_json(), locked)
        self.assertEqual(locked, rules.to_json())


class TestTableModeUI(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.ui.app import BlackjackLabApp
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.errors = []
        for name, effect in [('showerror', lambda *a, **k: self.errors.append(a)),
                             ('showinfo', lambda *a, **k: None), ('askyesno', lambda *a, **k: True)]:
            context = patch('blackjack_lab.ui.app.messagebox.' + name, side_effect=effect)
            context.start(); self.addCleanup(context.stop)
        self.app = BlackjackLabApp(Path(self.tmp.name) / 'mode-ui.db', auto_analysis=False)
        self.app.withdraw()
        from scripts.tk_lifecycle import close_app
        self.addCleanup(lambda: close_app(self.app, discard_fixture_results=True))

    def test_switch_keeps_locked_events_plan_and_previous_pragmatic_settings(self):
        app = self.app
        app.act_new_shoe(); app.act_new_round(); app._key_rank('9')
        events = app.ctrl.ledger.to_list()
        plan = copy.deepcopy(app.ctrl.entry_plan.to_dict())
        locked = app.ctrl.state().current.rules.to_json()
        app.var_decks.set(6)
        app.compact_panel.mode_buttons[BCLC].invoke()
        self.assertEqual(app.var_table_mode.get(), BCLC)
        self.assertEqual(app.ctrl.ledger.to_list(), events)
        self.assertEqual(app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(app.ctrl.state().current.rules.to_json(), locked)
        app.compact_panel.mode_buttons[PRAGMATIC].invoke()
        self.assertEqual(app.var_decks.get(), 6)
        self.assertEqual(self.errors, [])

    def test_bclc_queue_records_actual_hole_and_never_guesses_unknown_analysis(self):
        app = self.app
        app.select_table_mode(BCLC)
        app.ctrl.new_shoe(bclc_rules())  # Explicit imported shoe with unknown observations.
        self.assertEqual(app.sidebets.profile.confirmation, 'unconfirmed')
        self.assertIsNone(app.sidebets.profile.payouts('perfect_pairs'))
        self.assertIsNone(app.sidebets.profile.payouts('21+3'))
        app.var_participants['玩家7'].set(True)
        app.act_new_round()
        self.assertEqual(app.ctrl.entry_plan.participating_seats, ('玩家7', '玩家1'))
        for rank in ('9', '8', '6', '7', '5'):
            app._key_rank(rank)
        self.assertEqual([e.payload['seat'] for e in app.ctrl.ledger.events if e.etype == 'CARD_DEALT'],
                         ['玩家7', '玩家1', '庄家', '玩家7', '玩家1', '庄家'])
        self.assertEqual(app.ctrl.state().current.shoe.unrevealed_out, 1)
        with self.assertRaisesRegex(InputUnavailable, '完整新牌靴'):
            app.ctrl.analysis_input('玩家1')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
