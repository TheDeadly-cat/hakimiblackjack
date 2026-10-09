import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.core.rules import CONFIRM_UNKNOWN, CONFIRM_VERIFIED
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, TableModeStore, default_settings, legacy_bclc_draft_rules


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
        app.act_new_shoe()
        self.assertEqual(app.sidebets.profile.confirmation, 'unconfirmed')
        self.assertIsNone(app.sidebets.profile.payouts('perfect_pairs'))
        self.assertIsNone(app.sidebets.profile.payouts('21+3'))
        app.var_participants['玩家7'].set(True)
        app.act_new_round()
        self.assertEqual(app.ctrl.entry_plan.participating_seats, ('玩家7', '玩家1'))
        for rank in ('9', '8', '6', '7', '5'):
            app._key_rank(rank)
        self.assertEqual([e.payload['seat'] for e in app.ctrl.ledger.events if e.etype == 'CARD_DEALT'],
                         ['玩家7', '玩家1', '庄家', '玩家7', '玩家1'])
        self.assertEqual(app.ctrl.state().current.shoe.unrevealed_out, 0)
        app._key_hole()
        self.assertEqual(app.ctrl.state().current.shoe.unrevealed_out, 1)
        with self.assertRaisesRegex(InputUnavailable, '完整新牌靴'):
            app.ctrl.analysis_input('玩家1')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
