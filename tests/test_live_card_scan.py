import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ui.controller import SessionController


class TestLiveCardScan(unittest.TestCase):
    def test_current_round_scan_stays_bounded_and_tracks_correction_undo_recovery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'scan.db'
            ctrl = SessionController(path)
            try:
                self.assertEqual(ctrl.live_card_event_ids(), [])
                ctrl.new_shoe(RuleProfile(n_decks=8))
                for _ in range(8):
                    ctrl.start_round(['玩家1'])
                    ctrl.deal_shown('玩家1', '9')
                    ctrl.deal_shown('庄家', '8')
                    ctrl.deal_shown('玩家1', '8')
                    ctrl.deal_shown('庄家', '9')
                    ctrl._apply('end_round', settle=False, observation_status='complete')
                ctrl.start_round(['玩家1'])
                first = ctrl.deal_shown('玩家1', '5')
                second = ctrl.deal_shown('庄家', '6')
                before = ctrl.ledger.to_list()
                with patch.object(ctrl, 'state', wraps=ctrl.state) as state:
                    self.assertEqual(ctrl.live_card_event_ids(), [first.event_id, second.event_id])
                self.assertEqual(state.call_count, 1)
                self.assertEqual(ctrl.ledger.to_list(), before)
                ctrl.correct(second.event_id, {'rank': '7'}, 'synthetic correction')
                self.assertEqual(ctrl.live_card_event_ids(), [first.event_id, second.event_id])
                ctrl.undo_last()
                ctrl.undo_last()
                self.assertEqual(ctrl.live_card_event_ids(), [first.event_id])
                session_id = ctrl.session_id
            finally:
                ctrl.close()
            recovered = SessionController.recover(path, session_id)
            try:
                self.assertEqual(recovered.live_card_event_ids(), [first.event_id])
                self.assertTrue(recovered.entry_plan.paused)  # Generic correction still requires alignment.
            finally:
                recovered.close()
