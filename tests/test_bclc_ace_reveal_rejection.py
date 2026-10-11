"""Known Ace/peek input conflicts never masquerade as storage failures."""
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.table import ACTION_STAND
from blackjack_lab.storage.database import LocalStore
from blackjack_lab.ui.read_snapshot import PrefixSnapshot
from blackjack_lab.ui.recording_commands import RecordingInputRejected, record_input
from blackjack_lab.ui.recording_owner import RecordingOwner
from tests import test_bclc_ace_recording as ace_fixture
from tests import test_recording_owner as owner_fixture
from tests import test_recording_background_ui as ui_fixture


class BclcAceRevealRejectionTests(unittest.TestCase):
    use_process = False
    setUp = ace_fixture.BclcAceRecordingTests.setUp
    prepare = ace_fixture.BclcAceRecordingTests.prepare
    next_options = staticmethod(ace_fixture.BclcAceRecordingTests.next_options)
    results = owner_fixture.TestRecordingOwner.results
    close_owner = owner_fixture.TestRecordingOwner.close_owner

    def dealer_after_negative(self):
        ctrl = self.prepare(cards={'玩家1': ('9', '4')})
        # Mirror the actual interaction: shortcut card/undo, shortcut stand/undo,
        # then an explicit observed non-BJ check followed by player input/stand.
        ctrl.record_bclc_ace_input('rank', rank='6'); ctrl.undo_last()
        ctrl.record_bclc_ace_input('action', action=ACTION_STAND); ctrl.undo_last()
        self.assertTrue(ctrl.bclc_ace_input_ready())
        ctrl.peek_negative()
        ctrl.deal_shown('玩家1', '5', hand_id=ctrl.entry_plan.continuation_hand_id)
        ctrl.player_action('玩家1', ctrl.entry_plan.continuation_hand_id, ACTION_STAND)
        self.assertEqual(ctrl.entry_plan.mode, 'dealer_phase')
        self.assertTrue(ctrl.state().current.table.dealer_hole_checked_negative)
        self.owner = RecordingOwner(ctrl.store.db_path, ctrl.recording_source, use_process=self.use_process)
        self.addCleanup(self.close_owner)
        return ctrl

    def intent(self, ctrl, rank, *, manual=False):
        return dict(kind='rank', rank=rank, follow_plan=not manual,
                    auto_next=self.next_options(ctrl), reveal_event_id=None,
                    selection=dict(seat='庄家', hand_id=None, hand_mode='latest',
                                   mode='揭示' if manual else '新发牌', suit=None))

    def test_negative_peek_ten_is_rejected_without_fault_lock_and_actual_non_ten_can_follow(self):
        ctrl = self.dealer_after_negative()
        before = PrefixSnapshot.capture(ctrl.ledger)
        hole = ctrl.entry_plan.filled_slots[ctrl.entry_plan.hole_slot().slot_id]
        self.owner.submit(before, 'record_input', self.intent(ctrl, 'T'))
        refused = self.results(1)[0]
        self.assertEqual(refused.status, 'rejected')
        self.assertIn('非BJ检查结果矛盾', refused.error)
        self.assertEqual(refused.before, before)
        self.assertEqual(refused.after, before)
        self.assertEqual(refused.event_ids, ())
        self.assertIsNone(refused.failure_path)
        self.assertFalse(self.owner.failed)
        self.assertFalse(Path(str(ctrl.store.db_path) + '.recording-failures').exists())
        self.assertEqual(PrefixSnapshot.capture(ctrl.store.load_ledger(ctrl.session_id)), before)
        self.owner.submit(before, 'record_input', self.intent(ctrl, '6'))
        accepted = self.results(1)[0]
        self.assertEqual(accepted.status, 'committed')
        self.assertEqual([event.etype for event in accepted.ledger.events[-3:]],
                         ['CARD_REVEALED', 'ROUND_ENDED', 'ROUND_STARTED'])
        self.assertEqual(accepted.ledger.events[-3].payload['target_event_id'], hole)
        self.assertEqual(accepted.ledger.events[-3].payload['rank'], '6')
        self.assertEqual(accepted.ledger.to_list()[:len(ctrl.ledger.events)], ctrl.ledger.to_list())
        self.assertFalse(self.owner.failed)

    def test_manual_reveal_has_same_no_write_conflict_proof(self):
        ctrl = self.dealer_after_negative()
        before, plan = ctrl.ledger.to_list(), ctrl.entry_plan.to_dict()
        with patch.object(ctrl.store, 'append_validated', wraps=ctrl.store.append_validated) as saved:
            with self.assertRaisesRegex(RecordingInputRejected, '非BJ检查结果矛盾'):
                record_input(ctrl, self.intent(ctrl, 'T', manual=True))
        self.assertEqual(saved.call_count, 0)
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertEqual(ctrl.entry_plan.to_dict(), plan)
        self.assertEqual(ctrl.store.load_ledger(ctrl.session_id).to_list(), before)

    def test_valid_reveal_storage_failure_is_not_downgraded_to_rejected_input(self):
        ctrl = self.dealer_after_negative()
        before = PrefixSnapshot.capture(ctrl.ledger)
        with patch.object(LocalStore, 'append_validated', side_effect=sqlite3.OperationalError('synthetic disk failure')):
            self.owner.submit(before, 'record_input', self.intent(ctrl, '6'))
            receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'failed_before_commit')
        self.assertIsNotNone(receipt.failure_path)
        self.assertTrue(self.owner.failed)
        self.assertEqual(receipt.after, before)

    def test_exception_after_valid_reveal_commit_remains_unknown(self):
        ctrl = self.dealer_after_negative()
        before = PrefixSnapshot.capture(ctrl.ledger)
        original = LocalStore.append_validated
        def after_write(store, candidate):
            original(store, candidate)
            raise OSError('synthetic receipt interruption')
        with patch.object(LocalStore, 'append_validated', side_effect=after_write, autospec=True):
            self.owner.submit(before, 'record_input', self.intent(ctrl, '6'))
            receipt = self.results(1)[0]
        self.assertEqual(receipt.status, 'unknown_commit_outcome')
        self.assertIsNotNone(receipt.failure_path)
        self.assertNotEqual(receipt.after, before)
        self.assertTrue(self.owner.failed)
        actual = ctrl.store.load_ledger(ctrl.session_id)
        self.assertEqual(len([event for event in actual.events if event.etype == 'CARD_REVEALED']), 1)


class ProcessBclcAceRevealRejectionTests(BclcAceRevealRejectionTests):
    use_process = True


class BclcAceRevealRejectionUITests(unittest.TestCase):
    use_process = False
    setUp = ui_fixture.RecordingBackgroundUITests.setUp
    close = ui_fixture.RecordingBackgroundUITests.close
    pump = ui_fixture.RecordingBackgroundUITests.pump
    wait_saved = ui_fixture.RecordingBackgroundUITests.wait_saved

    def test_wrong_ten_preserves_back_recording_and_analysis_then_accepts_actual_non_ten_hole(self):
        app = self.app
        app.select_table_mode('bclc')
        app.var_auto_next.set(False)
        app.act_new_shoe(); app.act_new_round()
        for rank in ('9', 'A', '4', '5'):
            app._key_rank(rank)
        app._key_stand(); self.wait_saved()
        ctrl = app.ctrl
        self.assertEqual(ctrl.entry_plan.mode, 'dealer_phase')
        self.assertTrue(ctrl.state().current.table.dealer_hole_checked_negative)
        before = ctrl.ledger.to_list()
        hole = ctrl.entry_plan.filled_slots[ctrl.entry_plan.hole_slot().slot_id]
        app._key_rank('T'); self.wait_saved()
        self.assertEqual(ctrl.ledger.to_list(), before)
        self.assertIn('非BJ检查结果矛盾', app.var_recording_save.get())
        self.assertFalse(app.recording_guard())
        self.assertIsNone(app.recording_advice_pause())
        self.assertFalse(app.compact_panel.undo_button.instate(['disabled']))
        self.assertTrue(all(not button.instate(['disabled']) for button in app.compact_panel.card_buttons))
        self.assertFalse(Path(str(self.db) + '.recording-failures').exists())
        app.act_undo(); self.wait_saved()
        self.assertEqual(ctrl.entry_plan.mode, 'player_continuation')
        self.assertTrue(ctrl.state().current.table.dealer_hole_checked_negative)
        self.assertIsNotNone(app.analysis_panel.current_input)
        self.assertFalse(app.analysis_panel.compute_button.instate(['disabled']))
        self.assertIsNone(app.recording_advice_pause())
        app._key_stand(); self.wait_saved()
        app._key_rank('6'); self.wait_saved()
        self.assertEqual(ctrl.ledger.events[-1].etype, 'CARD_REVEALED')
        self.assertEqual(ctrl.ledger.events[-1].payload['target_event_id'], hole)
        self.assertEqual(ctrl.state().current.table.dealer.hands[0].ranks, ['A', '6'])
        self.assertEqual(ctrl.ledger.to_list()[:len(before)], before)
        self.assertEqual(self.errors, [])


class ProcessBclcAceRevealRejectionUITests(BclcAceRevealRejectionUITests):
    use_process = True


if __name__ == '__main__':
    unittest.main()
