"""Production IPC recording, manual-rule BCLC both-hand and Ace-peek flows."""
import unittest

from blackjack_lab.core.table import ACTION_SPLIT
from blackjack_lab.ui.controller import SessionController
from tests import test_recording_background_ui as fixture


class BclcRulebookProcessTests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved

    def start(self):
        app = self.app
        app.select_table_mode('bclc'); app.var_auto_next.set(False)
        app.act_new_shoe(); app.act_new_round()
        return app

    def test_both_hands_no_das_ordered_save_then_soft17_auto_next(self):
        app = self.start(); app.var_auto_next.set(True)
        for rank in ('8','6','8'): app._key_rank(rank)
        app._key_hole(); app.act_action(ACTION_SPLIT)
        app._key_rank('3'); app._key_rank('9')
        app._key_stand(); app._key_stand(); app._key_rank('A'); app._key_rank('5')
        self.wait_saved()
        seg = app.ctrl.state().current
        self.assertEqual(seg.table.round_no, 2)
        self.assertEqual(seg.table.players['玩家1'].hands[0].ranks, ['5'])
        self.assertFalse(seg.rules.double_after_split)
        self.assertIsNone(seg.rules.start_from_new_shoe)
        self.assertEqual(len([e for e in app.ctrl.ledger.events if e.etype=='CARD_REVEALED']), 1)
        self.assertEqual(app.ctrl.store.load_ledger(app.ctrl.session_id).to_list(), app.ctrl.ledger.to_list())
        self.assertEqual(self.errors, [])

    def test_ace_actual_bj_queue_never_fabricates_negative_peek_or_third_card(self):
        app = self.start()
        for rank in ('9','A','8'): app._key_rank(rank)
        app._key_hole(); app._key_rank('T'); self.wait_saved()
        seg = app.ctrl.state().current
        self.assertEqual(seg.table.dealer.hands[0].ranks, ['A','T'])
        self.assertEqual(seg.shoe.physical_remaining(), 412)
        self.assertEqual(app.ctrl.entry_plan.mode, 'dealer_phase')
        self.assertFalse(any(e.etype=='PEEK_NEGATIVE' for e in app.ctrl.ledger.events))
        recovered = SessionController.recover(self.db, app.ctrl.session_id)
        try:
            self.assertEqual(recovered.entry_plan.to_dict(), app.ctrl.entry_plan.to_dict())
            self.assertEqual(recovered.ledger.to_list(), app.ctrl.ledger.to_list())
        finally: recovered.close()
        self.assertEqual(self.errors, [])

    def test_ace_negative_peek_is_an_explicit_fifo_input(self):
        app = self.start()
        for rank in ('9','A','8'): app._key_rank(rank)
        app._key_hole(); app.act_peek_negative(); app._key_stand(); app._key_rank('6')
        self.wait_saved()
        seg = app.ctrl.state().current
        self.assertTrue(seg.table.dealer_hole_checked_negative)
        self.assertEqual(seg.table.dealer.hands[0].total(), (17,True))
        self.assertEqual(app.ctrl.round_completion_problem(), '')
        self.assertEqual(self.errors, [])
