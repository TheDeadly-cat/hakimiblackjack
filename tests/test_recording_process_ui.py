"""The production spawned owner: real IPC, ordered close, and unexpected death."""
import unittest

from tests import test_recording_background_ui as fixture


class RecordingProcessUITests(unittest.TestCase):
    use_process = True
    setUp = fixture.RecordingBackgroundUITests.setUp
    close = fixture.RecordingBackgroundUITests.close
    pump = fixture.RecordingBackgroundUITests.pump
    wait_saved = fixture.RecordingBackgroundUITests.wait_saved
    deals = fixture.RecordingBackgroundUITests.deals

    test_released_equal_keys_and_held_repeat_keep_fifo_seat_order = (
        fixture.RecordingBackgroundUITests.test_released_equal_keys_and_held_repeat_keep_fifo_seat_order)
    test_failed_input_and_later_unexecuted_input_are_retained_without_retry = (
        fixture.RecordingBackgroundUITests.test_failed_input_and_later_unexecuted_input_are_retained_without_retry)
    test_split_double_dealer_reveal_and_automatic_next_share_ordered_plan = (
        fixture.RecordingBackgroundUITests.test_split_double_dealer_reveal_and_automatic_next_share_ordered_plan)
    test_bclc_initial_flow_requires_explicit_hole_and_keeps_unknown_rules = (
        fixture.RecordingBackgroundUITests.test_bclc_initial_flow_requires_explicit_hole_and_keeps_unknown_rules)
    test_bclc_actual_hole_reveal_does_not_invent_a_third_card_or_soft17_rule = (
        fixture.RecordingBackgroundUITests.test_bclc_actual_hole_reveal_does_not_invent_a_third_card_or_soft17_rule)

    def test_immediate_close_waits_for_spawned_owner_and_ordered_receipts(self):
        app = self.app; session = app.ctrl.session_id
        app._key_rank('8'); app._key_rank('6'); owner = app._recording_owner
        app.on_close()
        self.pump(lambda: app.exit_flow.phase == 'finished')
        self.assertTrue(owner.stopped)
        self.app = None
        from blackjack_lab.ui.controller import SessionController
        recovered = SessionController.recover(self.db, session)
        try:
            self.assertEqual(self.deals(recovered.ledger), [('玩家1', '8'), ('庄家', '6')])
        finally:
            recovered.close()

    def test_unexpected_process_death_preserves_unknown_inputs_for_readback(self):
        app = self.app
        app._key_rank('8'); app._key_rank('6'); app._key_rank('8')
        owner = app._recording_owner
        self.assertTrue(owner.thread.is_alive())
        owner.thread.terminate()  # Fault injection against this test's new local owner only.
        self.pump(lambda: not app.recording_busy and owner.stopped)
        self.assertTrue(app._recording_faults)
        self.assertTrue(all(f['status'] == 'unknown_commit_outcome' for f in app._recording_faults))
        actual = app.ctrl.store.load_ledger(app.ctrl.session_id).to_list()
        app.act_reconcile_recording()
        self.assertEqual(app.ctrl.ledger.to_list(), actual)
        self.assertFalse(app.recording_busy)
        self.assertFalse(app._recording_faults)


if __name__ == '__main__':
    unittest.main()
