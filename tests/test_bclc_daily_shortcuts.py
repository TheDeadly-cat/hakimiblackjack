"""Daily BCLC entries use actual declared observations and reversible input."""
from dataclasses import asdict
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from blackjack_lab.core.cards import TEN_BUCKET
from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_SPLIT, ACTION_STAND
from blackjack_lab.ledger.events import CARD_DEALT, CARD_REVEALED, PEEK_NEGATIVE, PLAYER_ACTION, UNDO
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.export import export_json
from blackjack_lab.ui.deal_entry import MODE_PEEK_WAIT
from blackjack_lab.ui.manual_keymap import resolve
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, bclc_rules
from tests import test_table_modes as fixture


class BclcDailyShortcutTests(unittest.TestCase):
    setUp = fixture.TestTableModeUI.setUp

    def key(self, keysym):
        app = self.app
        app.deiconify(); app.focus_force(); app.update()
        app.event_generate('<KeyPress>', keysym=keysym, when='tail')
        app.event_generate('<KeyRelease>', keysym=keysym, when='tail')
        app.update()

    def initial(self, ranks=('8', 'A', '8'), players=('玩家1',)):
        app = self.app
        app.select_table_mode(BCLC)
        app.act_new_shoe()
        for name, selected in app.var_participants.items():
            selected.set(name in players)
        app.act_new_round()
        app.var_auto_next.set(False)
        for rank in ranks:
            app._key_rank(rank)
        app.update()
        self.assertEqual(self.errors, [])
        self.assertEqual(app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.assertFalse(app.ctrl.state().current.table.dealer_hole_checked_negative)
        return app.ctrl.ledger.events[-1]

    def test_UI_new_eight_deck_shoe_records_user_declared_complete_zero_burn_observation(self):
        app = self.app
        original = asdict(bclc_rules())
        app.select_table_mode(BCLC)
        app.compact_panel.new_shoe_button.invoke()
        actual = asdict(app.ctrl.state().current.rules)
        self.assertEqual(actual, dict(original, start_from_new_shoe=True,
                                     burn_cards_known=True, initial_burn_count=0))
        created = app.ctrl.ledger.events[-1]
        self.assertEqual(created.etype, 'SHOE_CREATED')
        self.assertEqual(json.loads(created.payload['rules_snapshot']), json.loads(app.ctrl.state().current.rules.to_json()))
        self.assertIsNone(bclc_rules().start_from_new_shoe)
        self.assertIsNone(bclc_rules().initial_burn_count)
        self.assertEqual(self.errors, [])

    def test_direct_controller_and_imported_shoes_keep_unknown_observations(self):
        app = self.app
        app.ctrl.new_shoe(bclc_rules())
        direct = app.ctrl.state().current.rules
        self.assertIsNone(direct.start_from_new_shoe)
        self.assertIsNone(direct.burn_cards_known)
        self.assertIsNone(direct.initial_burn_count)
        imported = EventLedger('imported-unknown-shoe')
        imported.start_session('synthetic import')
        created = imported.create_shoe(bclc_rules())
        original = created.to_dict()
        path = Path(self.tmp.name) / 'unknown-shoe.json'
        export_json(imported, path, session_name='synthetic unknown shoe')
        app.ctrl.import_file(path)
        current = app.ctrl.state().current.rules
        self.assertIsNone(current.start_from_new_shoe)
        self.assertIsNone(current.burn_cards_known)
        self.assertIsNone(current.initial_burn_count)
        self.assertEqual(app.ctrl.ledger._find(created.event_id).to_dict(), original)
        self.assertEqual(self.errors, [])

    def test_ten_aliases_after_A_initial_cards_reveal_original_dealer_BJ(self):
        app = self.app
        for key in ('0', 'J', 'Q', 'K'):
            with self.subTest(key=key):
                hole = self.initial()
                self.assertEqual(resolve(key).rank, TEN_BUCKET)
                before = app.ctrl.ledger.to_list()
                physical = app.ctrl.state().current.shoe.physical_remaining()
                self.key(key)
                added = app.ctrl.ledger.events[len(before):]
                self.assertEqual([event.etype for event in added], [CARD_REVEALED])
                self.assertEqual(added[0].payload['target_event_id'], hole.event_id)
                self.assertEqual(app.ctrl.state().current.table.dealer.hands[0].ranks, ['A', TEN_BUCKET])
                self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), physical)
                self.assertEqual(app.ctrl.ledger.to_list()[:len(before)], before)
                self.assertFalse(any(event.etype == PEEK_NEGATIVE for event in added))
                self.assertEqual(self.errors, [])

    def test_non_ten_after_A_saves_non_BJ_and_first_player_card_in_one_transaction(self):
        app = self.app
        self.initial(('9', '8', 'A', '7', '8'), ('玩家7', '玩家1'))
        before = app.ctrl.ledger.to_list()
        physical = app.ctrl.state().current.shoe.physical_remaining()
        append = app.ctrl.store.append_validated
        with patch.object(app.ctrl.store, 'append_validated', wraps=append) as commit:
            self.key('3')
        self.assertEqual(commit.call_count, 1)
        added = app.ctrl.ledger.events[len(before):]
        self.assertEqual([event.etype for event in added], [PEEK_NEGATIVE, CARD_DEALT])
        self.assertEqual(added[-1].payload['seat'], '玩家7')
        current = app.ctrl.state().current
        self.assertEqual(current.table.players['玩家7'].hands[0].ranks, ['9', '7', '3'])
        self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['8', '8'])
        self.assertTrue(current.table.dealer_hole_checked_negative)
        self.assertEqual(current.shoe.physical_remaining(), physical - 1)
        self.assertFalse(any(event.etype == CARD_REVEALED for event in added))
        self.key('BackSpace')
        restored = app.ctrl.state().current
        self.assertEqual(app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
        self.assertFalse(restored.table.dealer_hole_checked_negative)
        self.assertEqual(restored.table.players['玩家7'].hands[0].ranks, ['9', '7'])
        self.assertEqual(restored.shoe.physical_remaining(), physical)
        self.assertEqual([event.etype for event in app.ctrl.ledger.events[-2:]], [UNDO, UNDO])
        self.assertEqual(app.ctrl.ledger.to_list()[:len(before)], before)
        self.assertEqual(self.errors, [])

    def test_A_phase_legal_stand_double_split_buttons_and_atomic_shortcuts_undo_once(self):
        app = self.app
        for key, action, index in (('minus', ACTION_STAND, 0), ('asterisk', ACTION_DOUBLE, 1),
                                   ('slash', ACTION_SPLIT, 2)):
            with self.subTest(action=action):
                self.initial()
                button = app.compact_panel.action_buttons[index]
                self.assertFalse(button.instate(['disabled']))
                self.assertEqual(button.winfo_manager(), 'pack')
                before = app.ctrl.ledger.to_list()
                physical = app.ctrl.state().current.shoe.physical_remaining()
                append = app.ctrl.store.append_validated
                with patch.object(app.ctrl.store, 'append_validated', wraps=append) as commit:
                    self.key(key)
                self.assertEqual(commit.call_count, 1)
                added = app.ctrl.ledger.events[len(before):]
                self.assertEqual([event.etype for event in added], [PEEK_NEGATIVE, PLAYER_ACTION])
                self.assertEqual(added[-1].payload['action'], action)
                self.assertEqual(added[-1].payload['seat'], '玩家1')
                self.assertTrue(app.ctrl.state().current.table.dealer_hole_checked_negative)
                with patch.object(app.ctrl.store, 'append_validated', wraps=append) as commit:
                    self.key('BackSpace')
                self.assertEqual(commit.call_count, 1)
                self.assertEqual(app.ctrl.entry_plan.mode, MODE_PEEK_WAIT)
                current = app.ctrl.state().current
                self.assertFalse(current.table.dealer_hole_checked_negative)
                self.assertEqual(len(current.table.players['玩家1'].hands), 1)
                self.assertEqual(current.table.players['玩家1'].hands[0].ranks, ['8', '8'])
                self.assertFalse(current.table.players['玩家1'].hands[0].doubled)
                self.assertEqual(current.shoe.physical_remaining(), physical)
                self.assertEqual(app.ctrl.ledger.to_list()[:len(before)], before)
                self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())
                self.assertEqual(self.errors, [])

    def test_disabled_sidebet_labels_stay_unmapped_and_original_profiles_are_unchanged(self):
        app, side = self.app, self.app.sidebets
        self.assertFalse(app.sidebet_research)
        original_path = side.settings
        original = json.dumps(dict(schema=1, profile=side.profile.to_dict(), enabled=True),
                              ensure_ascii=False, indent=3).encode('utf-8') + b'\n'
        original_path.write_bytes(original)
        app.select_table_mode(BCLC)
        bclc_path = side.settings
        bclc_original = json.dumps(dict(schema=1, profile=side.profile.to_dict(), enabled=True),
                                   ensure_ascii=False, indent=2).encode('utf-8') + b'\n'
        bclc_path.write_bytes(bclc_original)
        app.select_table_mode(PRAGMATIC); app.select_table_mode(BCLC)
        self.initial()
        side.refresh(); side.render(); app.update()
        self.assertFalse(side.enabled.get())
        self.assertTrue(side.line_labels)
        for label in side.line_labels.values():
            self.assertEqual(label.winfo_manager(), '')
            self.assertFalse(label.winfo_ismapped())
        self.assertEqual(original_path.read_bytes(), original)
        self.assertEqual(bclc_path.read_bytes(), bclc_original)
        self.assertIsNone(side.worker.active)
        self.assertEqual(side.worker.pending, {})
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
