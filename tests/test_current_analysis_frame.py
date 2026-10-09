from pathlib import Path
import tempfile
import unittest

from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.split_contracts import both_initial_das_rules
from blackjack_lab.ledger.ledger import LedgerError
from blackjack_lab.ui.controller import SessionController


class TestCurrentAnalysisFrame(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ctrl = SessionController(Path(self.tmp.name) / 'frame.db')
        self.addCleanup(self.ctrl.close)
        self.ctrl.new_shoe(both_initial_das_rules(8))
        self.ctrl.start_round(['玩家1'], simple_hole=True)
        self.ctrl.deal_shown('玩家1', '8')
        self.ctrl.deal_shown('庄家', '6')
        self.ctrl.deal_shown('玩家1', '8')

    def assert_matches_full_reference(self):
        expected = build_input(self.ctrl.ledger, '玩家1', other_players_stand=True)
        actual = self.ctrl.current_decision_input('玩家1')
        actual.validate()
        self.assertEqual(actual.to_dict(), expected.to_dict())

    def test_current_frame_equals_independent_full_builder(self):
        self.assert_matches_full_reference()

    def test_undo_and_correction_do_not_reuse_future_counts(self):
        before = self.ctrl.current_decision_input('玩家1')
        self.ctrl.deal_shown('玩家1', '2')
        self.assert_matches_full_reference()
        self.ctrl.undo_last()
        self.assert_matches_full_reference()
        after = self.ctrl.current_decision_input('玩家1')
        self.assertEqual(before.counts, after.counts)
        self.assertNotEqual(before.prefix_digest, after.prefix_digest)

    def test_in_place_invalid_event_still_rejected_by_independent_replay(self):
        self.assert_matches_full_reference()
        self.ctrl.ledger.events[-1].observed_at = None
        with self.assertRaises((ValueError, LedgerError)):
            self.ctrl.current_decision_input('玩家1')

    def test_in_frame_mutation_cannot_retag_validated_state(self):
        with self.ctrl.read_frame():
            self.ctrl.state()
            self.ctrl.ledger.events[-1].evidence = 'in-place changed metadata'
            with self.assertRaisesRegex(LedgerError, '前缀'):
                self.ctrl.current_decision_input('玩家1')

    def test_recovery_uses_recovered_full_content(self):
        session = self.ctrl.session_id
        self.ctrl.close()
        self.ctrl = SessionController.recover(Path(self.tmp.name) / 'frame.db', session)
        self.addCleanup(self.ctrl.close)
        self.assert_matches_full_reference()


if __name__ == '__main__':
    unittest.main()
