"""Manual v1.1 rules, migration boundaries, recorded peek and no-DAS workflow."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import BOTH_INITIAL_ENGINE, BOTH_INITIAL_STRATEGY, NO_DAS_BOTH_INITIAL_ENGINE
from blackjack_lab.core.rules import CONFIRM_VERIFIED
from blackjack_lab.core.table import ACTION_DOUBLE, ACTION_SPLIT, ACTION_HIT, TableError
from blackjack_lab.ui.table_modes import BCLC, PRAGMATIC, ModeSettings, TableModeStore, bclc_rules, legacy_bclc_draft_rules
from blackjack_lab.ui.rule_summary import rule_summary
from tests.test_analysis_integration import example
from tests import test_table_modes as fixture


def observed_rules():
    rules = bclc_rules()
    rules.burn_cards_known = rules.start_from_new_shoe = True
    rules.initial_burn_count = 0
    return rules


class BclcRulebookTests(unittest.TestCase):
    def test_known_rules_and_unknown_observations_are_separate(self):
        rules = bclc_rules()
        self.assertEqual((rules.n_decks, rules.version, rules.dealer_soft17), (8, 2, 'S17'))
        self.assertEqual(rules.confirm_status, CONFIRM_VERIFIED)
        self.assertEqual(rules.check_bj_when, 'before_player_actions_A')
        self.assertFalse(rules.double_after_split)
        self.assertFalse(rules.resplit_aces)
        self.assertTrue(rules.split_ace_hit_once)
        self.assertEqual(rules.max_split_hands, 2)
        self.assertIsNone(rules.surrender)
        for field in ('burn_cards_known', 'initial_burn_count', 'start_from_new_shoe'):
            self.assertIsNone(getattr(rules, field))
        ledger = example(8, cards=('8', '8'), up='6', rules=rules)
        with self.assertRaises(InputUnavailable) as error:
            build_input(ledger, '玩家1')
        self.assertEqual(error.exception.code, 'START_UNKNOWN')

    def test_saved_v1_upgrades_next_preset_without_rewriting_original_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'migration.db'
            store = TableModeStore(db)
            old = legacy_bclc_draft_rules()
            old.burn_cards_known = True; old.initial_burn_count = 3
            settings = ModeSettings(old, 'reverse', False, False)
            store.save_switch(PRAGMATIC, store.get(PRAGMATIC), BCLC, settings)
            before = store.path.read_bytes()
            updated = TableModeStore(db).get(BCLC)
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(json.loads(before)['modes'][BCLC]['rules']['version'], 1)
            self.assertEqual(updated.rules.version, 2)
            self.assertFalse(updated.rules.double_after_split)
            self.assertEqual(updated.rules.initial_burn_count, 3)
            self.assertIsNone(updated.rules.start_from_new_shoe)
            self.assertFalse(updated.auto_next)
            self.assertEqual(settings.rules.version, 1)
            recovered = TableModeStore(db)
            recovered.save_switch(BCLC, updated, BCLC, updated)
            backups = list(recovered.path.with_name(recovered.path.name + '.history').glob('*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(backups[0].read_bytes(), before)

    def test_observed_input_uses_distinct_identity_and_original_double(self):
        ledger = example(8, cards=('8', '8'), up='6', rules=observed_rules())
        snapshot = build_input(ledger, '玩家1')
        snapshot.validate()
        self.assertEqual(snapshot.engine_version, NO_DAS_BOTH_INITIAL_ENGINE)
        self.assertIn('double', snapshot.legal_actions)
        result = calculate(snapshot, 'bclc-rulebook-integration', 5)
        self.assertEqual(result['status'], 'available', result.get('reason'))
        self.assertEqual(result['actions']['split']['max_final_investment'], 2)
        self.assertEqual(result['actions']['split']['possible_future_additional'], 0)
        self.assertEqual(result['actions']['double']['max_final_investment'], 2)
        with self.assertRaises(ValueError):
            replace(snapshot, engine_version=BOTH_INITIAL_ENGINE, strategy_version=BOTH_INITIAL_STRATEGY).validate()

    def test_opening_model_accepts_no_das_but_never_fills_unknown_observations(self):
        from blackjack_lab.analysis.opening import validate_rules
        with self.assertRaises(InputUnavailable) as error:
            validate_rules(json.loads(bclc_rules().to_json()))
        self.assertEqual(error.exception.code, 'START_UNKNOWN')
        validate_rules(json.loads(observed_rules().to_json()))

    def test_opening_native_both_initial_without_das_keeps_unit_split_stakes(self):
        from blackjack_lab.analysis.opening_service import estimate_counts
        # Pure 2s: two split hands each reach 20, dealer reaches 18. No DAS.
        result = estimate_counts((0,32,0,0,0,0,0,0,0,0), das=False, both_initial=True,
                                 surrender=False, peek_ten=False, samples=1000)
        self.assertEqual(result['ev'], 2)
        self.assertEqual(result['histogram'][12], 1000)

    def test_recorded_split_input_fills_both_and_never_offers_das(self):
        ledger = example(8, cards=('8','8'), up='6', rules=observed_rules())
        first = build_input(ledger, '玩家1').hand_id
        ledger.player_action('玩家1', first, '分牌')
        ledger.deal('玩家1', '3', hand_id=first)
        second = ledger.replay().current.table.players['玩家1'].hands[1].hand_id
        waiting = build_input(ledger, '玩家1')
        self.assertEqual(waiting.active_hand_id, second)
        self.assertEqual(waiting.legal_actions, ('deal',))
        ledger.deal('玩家1', '9', hand_id=second)
        ready = build_input(ledger, '玩家1'); ready.validate()
        self.assertEqual(ready.active_hand_id, first)
        self.assertEqual(ready.legal_actions, ('stand','hit'))
        with self.assertRaises(ValueError): replace(ready, legal_actions=('stand','hit','double')).validate()


class BclcRulebookUITests(unittest.TestCase):
    setUp = fixture.TestTableModeUI.setUp

    def start(self, first='8', up='6', second='8'):
        app = self.app
        app.select_table_mode(BCLC); app.var_auto_next.set(False)
        app.act_new_shoe(); app.act_new_round()
        for rank in (first, up, second): app._key_rank(rank)
        app._key_hole()
        return app

    def test_unknown_observations_remain_recordable_and_visible(self):
        app = self.start()
        self.assertIn('本靴观察待确认', app.compact_panel.mode_status.get())
        self.assertIn('分牌后禁止加倍', app.compact_panel.flow_message.get() +
                      rule_summary(app.ctrl.current_rules()))
        with self.assertRaises(InputUnavailable) as error:
            app.ctrl.analysis_input('玩家1')
        self.assertEqual(error.exception.code, 'START_UNKNOWN')
        self.assertEqual(app.ctrl.state().current.shoe.physical_remaining(), 412)

    def test_fill_both_then_hit_or_stand_with_no_double_or_resplit(self):
        app = self.start(); app.act_action(ACTION_SPLIT)
        app._key_rank('8'); app._key_rank('3')
        seg = app.ctrl.state().current
        first, second = seg.table.players['玩家1'].hands
        self.assertEqual(seg.table.action_states('玩家1', first.hand_id)[ACTION_DOUBLE].allowed, False)
        self.assertEqual(seg.table.action_states('玩家1', first.hand_id)[ACTION_SPLIT].allowed, False)
        before = app.ctrl.ledger.to_list()
        for action in (ACTION_DOUBLE, ACTION_SPLIT):
            with self.assertRaises(TableError): app.ctrl.player_action('玩家1', first.hand_id, action)
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        app._key_stand(); app._key_stand()
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(app.ctrl.state().current.table.split_order_violations, [])
        self.assertEqual(self.errors, [])

    def test_split_aces_one_each_undo_and_recover(self):
        app = self.start('A', '6', 'A'); app.act_action(ACTION_SPLIT)
        app._key_rank('T'); app._key_rank('9')
        self.assertEqual(app.var_target.get(), '庄家')
        app.act_undo()
        self.assertEqual(app.ctrl.entry_plan.continuation_hand_ordinal, 2)
        from blackjack_lab.ui.controller import SessionController
        recovered = SessionController.recover(app.ctrl.store.db_path, app.ctrl.session_id)
        try:
            self.assertEqual(recovered.entry_plan.to_dict(), app.ctrl.entry_plan.to_dict())
            self.assertEqual(recovered.state().current.rules.to_json(), app.ctrl.current_rules().to_json())
        finally: recovered.close()
        app._key_rank('9')
        hand = app.ctrl.state().current.table.players['玩家1'].hands[0]
        with self.assertRaises(TableError): app.ctrl.player_action('玩家1', hand.hand_id, ACTION_HIT)

    def test_ace_actual_bj_reveals_original_hole_without_negative_peek(self):
        app = self.start('9', 'A', '8')
        self.assertEqual(app.ctrl.entry_plan.mode, 'peek_wait')
        hole = app.ctrl.recording_dealer_route('庄家')
        app._key_rank('T')
        seg = app.ctrl.state().current
        self.assertEqual(seg.table.dealer.hands[0].ranks, ['A', 'T'])
        reveal = [e for e in app.ctrl.ledger.events if e.etype == 'CARD_REVEALED']
        self.assertEqual(len(reveal), 1)
        self.assertEqual(reveal[0].payload['target_event_id'], hole)
        self.assertFalse(any(e.etype == 'PEEK_NEGATIVE' for e in app.ctrl.ledger.events))
        self.assertEqual(app.ctrl.entry_plan.mode, 'dealer_phase')
        self.assertEqual(seg.shoe.physical_remaining(), 412)
        self.assertEqual(self.errors, [])

    def test_ace_actual_negative_peek_soft17_stands_and_ten_never_waits(self):
        app = self.start('9', 'A', '8')
        app.act_peek_negative(); app._key_stand(); app._key_rank('6')
        seg = app.ctrl.state().current
        self.assertTrue(seg.table.dealer_hole_checked_negative)
        self.assertEqual(seg.table.dealer.hands[0].total(), (17, True))
        self.assertEqual(app.ctrl.round_completion_problem(), '')
        self.assertEqual(len(seg.table.dealer.hands[0].cards), 2)
        app.act_new_shoe(); app.act_new_round()
        for rank in ('9', 'T', '8'): app._key_rank(rank)
        app._key_hole()
        self.assertEqual(app.ctrl.entry_plan.mode, 'player_continuation')
        self.assertFalse(app.ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertEqual(self.errors, [])

    def test_current_v1_shoe_does_not_change_and_new_version_requires_new_shoe(self):
        app = self.app
        app.select_table_mode(BCLC); app._set_rule_form(legacy_bclc_draft_rules())
        app.act_new_shoe(); app.act_new_round(); app._key_rank('8')
        before = app.ctrl.ledger.to_list(); plan = app.ctrl.entry_plan.to_dict()
        app._apply_table_mode(BCLC, ModeSettings(bclc_rules(), 'reverse', False))
        app.compact_panel.render()
        self.assertTrue(app._mode_change_pending(app.ctrl.state().current))
        self.assertIsNone(app._automatic_next_options())
        self.assertIn('视频草案', app.compact_panel.mode_status.get())
        self.assertEqual(app.ctrl.ledger.to_list(), before)
        self.assertEqual(app.ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(app.ctrl.current_rules().version, 1)
