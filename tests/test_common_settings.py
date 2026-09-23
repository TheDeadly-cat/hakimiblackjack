import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.analysis.split_contracts import supported_same_value_das_rules
from blackjack_lab.core.rules import CONFIRM_VERIFIED, RuleProfile
from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_SPLIT, ACTION_SURRENDER
from blackjack_lab.ui.app import BlackjackLabApp


class TestCommonSettings(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'common-settings.db'
        self.errors = []
        for name, effect in [('showerror', lambda *a, **k: self.errors.append(a)),
                             ('showinfo', lambda *a, **k: None), ('askyesno', lambda *a, **k: True)]:
            context = patch('blackjack_lab.ui.app.messagebox.' + name, side_effect=effect)
            context.start()
            self.addCleanup(context.stop)
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.addCleanup(self.close)
        self.app.update()

    def close(self):
        if self.app:
            self.app.on_close()
            self.app = None

    def start(self):
        self.assertEqual(self.app.compact_panel.flow_primary.cget('text'), '新建牌盒')
        self.app.compact_panel.flow_primary.invoke()
        self.assertEqual(self.app.compact_panel.flow_primary.cget('text'), '开始本轮')
        self.app.compact_panel.flow_primary.invoke()
        self.assertEqual(self.errors, [])

    def test_fresh_app_has_complete_requested_preset_without_creating_shoe(self):
        rules = self.app._build_rules()
        self.assertEqual(rules.n_decks, 8)
        self.assertEqual(rules.dealer_soft17, 'S17')
        self.assertEqual(rules.split_match, 'same_value')
        self.assertEqual(rules.surrender, 'late')
        self.assertIsNone(rules.double_on_totals)
        self.assertTrue(rules.double_after_split)
        self.assertEqual(rules.confirm_status, CONFIRM_VERIFIED)
        self.assertTrue(supported_same_value_das_rules(rules))
        self.assertTrue(self.app.var_simple_hole.get())
        self.assertIsNone(self.app.ctrl.state().current)
        self.assertFalse(any(e.etype == 'SHOE_CREATED' for e in self.app.ctrl.ledger.events))

    def test_default_start_hole_reveal_and_s17_need_no_template_or_checkbox(self):
        with patch('blackjack_lab.ui.app.messagebox.askyesno') as confirmation:
            self.start()
        confirmation.assert_not_called()
        app, ctrl = self.app, self.app.ctrl
        for rank in ['T', '6', '6']:
            app._key_rank(rank)
        holes = [e for e in ctrl.ledger.events if e.payload.get('face_state') == 'hidden']
        self.assertEqual(len(holes), 1)
        self.assertIsNone(holes[0].payload['rank'])
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        app._key_stand()
        app._key_rank('A')
        self.assertEqual([e.etype for e in ctrl.ledger.events[-3:]], ['CARD_REVEALED', 'ROUND_ENDED', 'ROUND_STARTED'])
        self.assertEqual(ctrl.ledger.events[-3].payload['target_event_id'], holes[0].event_id)
        self.assertEqual(ctrl.state().current.table.round_no, 2)
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertEqual(ctrl.state().current.shoe.physical_remaining(), 412)
        app.act_undo()
        self.assertEqual(ctrl.state().current.table.round_no, 1)
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(ctrl.state().current.shoe.unrevealed_out, 1)
        self.assertEqual(self.errors, [])

    def test_different_ten_faces_split_late_surrender_and_double_after_split_are_available(self):
        self.start()
        app, ctrl = self.app, self.app.ctrl
        for rank in ['J', '6', 'Q']:
            app._key_rank(rank)
        table = ctrl.state().current.table
        legal = table.action_states('玩家1', table.players['玩家1'].hands[0].hand_id)
        for action in (ACTION_SPLIT, ACTION_DOUBLE, ACTION_SURRENDER):
            self.assertTrue(legal[action].allowed, legal[action].reason)
        ctrl.analysis_input('玩家1').validate()
        app.act_action(ACTION_SPLIT)
        app._key_rank('2')
        app._key_rank('8')
        table = ctrl.state().current.table
        self.assertTrue(table.action_states('玩家1', table.players['玩家1'].hands[0].hand_id)[ACTION_DOUBLE].allowed)
        app.act_action(ACTION_DOUBLE)
        app._key_rank('9')
        app._key_stand()
        self.assertEqual(app.var_target.get(), '庄家')
        app._key_rank('A')
        self.assertEqual(ctrl.state().current.table.round_no, 2)
        self.assertEqual(self.errors, [])

    def test_one_click_restores_preset_without_touching_current_rules_cards_or_plan(self):
        app = self.app
        app.var_s17.set('H17')
        app.var_simple_hole.set(False)
        self.start()
        app._key_rank('9')
        before = app.ctrl.ledger.to_list()
        plan = copy.deepcopy(app.ctrl.entry_plan.to_dict())
        locked = app.ctrl.state().current.rules.to_json()
        app.var_decks.set(6)
        app.var_surrender.set('不支持')
        with patch('blackjack_lab.ui.app.messagebox.askyesno') as confirmation:
            app.compact_panel.common_settings_button.invoke()
        confirmation.assert_not_called()
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual(app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(app.ctrl.state().current.rules.to_json(), locked)
        self.assertEqual(app._build_rules().n_decks, 8)
        self.assertTrue(supported_same_value_das_rules(app._build_rules()))
        self.assertTrue(app.var_simple_hole.get())
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.assertEqual(self.app.ctrl.state().current.rules.to_json(), locked)
        self.assertEqual(self.app.ctrl.ledger.to_list(), before)
        self.assertFalse(self.app.ctrl.entry_plan.simple_hole)
        self.assertFalse(self.app.var_simple_hole.get())

    def test_recovered_unknown_rules_remain_unknown_despite_complete_new_shoe_form(self):
        ctrl = self.app.ctrl
        ctrl.new_shoe(RuleProfile(n_decks=8))
        ctrl.start_round(['玩家1'])
        ledger = ctrl.ledger.to_list()
        rules = ctrl.state().current.rules.to_json()
        self.close()
        self.app = BlackjackLabApp(self.db, auto_analysis=False)
        self.assertEqual(self.app.ctrl.state().current.rules.to_json(), rules)
        self.assertEqual(self.app.ctrl.ledger.to_list(), ledger)
        self.assertIsNone(self.app.ctrl.state().current.rules.dealer_soft17)
        self.assertFalse(self.app.ctrl.entry_plan.simple_hole)


if __name__ == '__main__':
    unittest.main()
