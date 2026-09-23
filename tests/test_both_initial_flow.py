import unittest
from dataclasses import replace

from tests import test_common_settings as fixture
from blackjack_lab.analysis.contracts import InputUnavailable
from blackjack_lab.analysis.split_contracts import BOTH_INITIAL_ENGINE, DAS_ENGINE, DAS_STRATEGY
from blackjack_lab.analysis.service import calculate
from blackjack_lab.core.table import ACTION_SPLIT, ACTION_DOUBLE
from blackjack_lab.ui.controller import SessionController


class TestBothInitialFlow(unittest.TestCase):
    setUp = fixture.TestCommonSettings.setUp
    close = fixture.TestCommonSettings.close
    start = fixture.TestCommonSettings.start

    def pair(self, rank='8'):
        self.start()
        for card in (rank, '6', rank):
            self.app._key_rank(card)
        self.app.act_action(ACTION_SPLIT)

    def target(self):
        app = self.app
        return app.var_target.get(), app._hand_ordinal(app.var_target.get())

    def test_fill_both_before_action_then_return_first_with_undo_and_recovery(self):
        self.pair()
        app, ctrl = self.app, self.app.ctrl
        self.assertEqual(self.target(), ('玩家1', 1))
        app._key_rank('3')
        self.assertEqual(self.target(), ('玩家1', 2))
        first, second = ctrl.state().current.table.players['玩家1'].hands
        self.assertFalse(ctrl.state().current.table.action_states('玩家1', first.hand_id)[ACTION_DOUBLE].allowed)
        snapshot = ctrl.current_decision_input('玩家1')
        self.assertEqual(snapshot.engine_version, BOTH_INITIAL_ENGINE)
        self.assertEqual(snapshot.active_hand_id, second.hand_id)
        self.assertEqual(snapshot.legal_actions, ('deal',))
        result = calculate(snapshot, 'both-initial-fixture', 5)
        self.assertEqual(result['status'], 'available', result.get('reason'))
        self.assertEqual(result['actions']['deal']['max_final_investment'], 4)
        with self.assertRaises(ValueError):
            replace(snapshot, engine_version=DAS_ENGINE, strategy_version=DAS_STRATEGY).validate()
        app._key_rank('9')
        self.assertEqual(self.target(), ('玩家1', 1))
        snapshot = ctrl.current_decision_input('玩家1')
        self.assertEqual(snapshot.active_hand_id, first.hand_id)
        self.assertIn('double', snapshot.legal_actions)
        self.assertEqual(snapshot.hands[1].ranks, ('8', '9'))
        app.act_undo()
        self.assertEqual(self.target(), ('玩家1', 2))
        self.assertEqual(ctrl.current_decision_input('玩家1').legal_actions, ('deal',))
        recovered = SessionController.recover(self.db, ctrl.session_id)
        try:
            self.assertEqual(recovered.entry_plan.to_dict(), ctrl.entry_plan.to_dict())
            self.assertEqual(recovered.current_decision_input('玩家1').input_digest,
                             ctrl.current_decision_input('玩家1').input_digest)
        finally:
            recovered.close()
        app._key_rank('9')
        app.act_action(ACTION_DOUBLE)
        app._key_rank('T')
        self.assertEqual(self.target(), ('玩家1', 2))
        app._key_stand()
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(ctrl.state().current.table.split_order_violations, [])
        self.assertEqual(self.errors, [])

    def test_first_split_21_still_gives_second_its_card_then_skips_first(self):
        self.pair('T')
        app = self.app
        app._key_rank('A')
        self.assertEqual(self.target(), ('玩家1', 2))
        app._key_rank('7')
        self.assertEqual(self.target(), ('玩家1', 2))
        snapshot = app.ctrl.current_decision_input('玩家1')
        self.assertTrue(snapshot.hands[0].closed)
        self.assertEqual(snapshot.active_index, 1)
        app._key_stand()
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(self.errors, [])

    def test_split_aces_get_one_card_each_then_dealer(self):
        self.pair('A')
        app = self.app
        app._key_rank('8')
        self.assertEqual(self.target(), ('玩家1', 2))
        app._key_rank('9')
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(app.ctrl.current_decision_input('玩家1').legal_actions, ('complete',))
        self.assertEqual(self.errors, [])

    def test_original_natural_and_hit_21_skip_to_next_player_and_auto_settle(self):
        app = self.app
        app.act_new_shoe()
        app.var_participants['玩家2'].set(True)
        app.act_new_round()
        for rank in ('A', 'T', '6', 'T', '6'):
            app._key_rank(rank)
        self.assertEqual(app.var_target.get(), '玩家2')
        with self.assertRaises(InputUnavailable) as error:
            app.ctrl.current_decision_input('玩家1')
        self.assertEqual(error.exception.code, 'TOTAL_21')
        app._key_rank('5')
        self.assertEqual(app.var_target.get(), '庄家')
        self.assertEqual(app.compact_panel.seat_table.item('玩家2', 'values')[-1], '已达21点')
        app.act_undo()
        self.assertEqual(app.var_target.get(), '玩家2')
        self.assertEqual(app.ctrl.state().current.table.players['玩家2'].hands[0].total()[0], 16)
        app._key_rank('5')
        app._key_rank('A')
        self.assertEqual(app.ctrl.state().current.table.round_no, 2)
        self.assertEqual(app.var_target.get(), '玩家1')
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
