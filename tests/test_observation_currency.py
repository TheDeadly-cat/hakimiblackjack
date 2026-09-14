"""Observation currency without a capture backend or ledger writes."""
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.observation.currency import (
    MODE_LIVE, MODE_MANUAL, MODE_REPLAY, REASON_ALIGNED, REASON_FROZEN,
    REASON_OVERFLOW, REASON_STOPPED, REASON_UNCONFIRMED, STATUS_FROZEN,
    STATUS_LIVE, STATUS_STOPPED, ObservationState,
)
from blackjack_lab.ui.assisted_recording import AssistedRecording, DraftError
from blackjack_lab.ui.controller import SessionController


class ObservationStateTest(unittest.TestCase):
    def test_unconfirmed_and_overflow_do_not_require_live_source(self):
        obs = ObservationState()
        self.assertEqual(MODE_MANUAL, obs.mode)
        self.assertEqual((True, REASON_ALIGNED), obs.live_table_applicable())
        obs.set_unconfirmed(1)
        self.assertEqual((False, REASON_UNCONFIRMED), obs.live_table_applicable())
        obs.set_unconfirmed(0)
        self.assertTrue(obs.live_table_applicable()[0])
        obs.mark_overflow()
        self.assertEqual((False, REASON_OVERFLOW), obs.live_table_applicable())
        obs.set_unconfirmed(0)
        self.assertEqual((False, REASON_OVERFLOW), obs.live_table_applicable())
        obs.reconcile(acknowledge_overflow=False)
        self.assertEqual((False, REASON_OVERFLOW), obs.live_table_applicable())
        obs.reconcile(acknowledge_overflow=True)
        self.assertEqual((True, REASON_ALIGNED), obs.live_table_applicable())

    def test_live_freeze_and_stop_do_not_trap_manual_or_replay(self):
        obs = ObservationState()
        obs.connect_source("src-1")
        obs.note_source(STATUS_LIVE)
        obs.note_source(STATUS_FROZEN)
        self.assertEqual(MODE_LIVE, obs.mode)
        self.assertEqual((False, REASON_FROZEN), obs.live_table_applicable())
        obs.enter_manual()
        self.assertEqual((True, REASON_ALIGNED), obs.live_table_applicable())
        obs.connect_source("src-2")
        obs.note_source(STATUS_STOPPED)
        self.assertEqual((False, REASON_STOPPED), obs.live_table_applicable())
        obs.enter_replay()
        self.assertEqual(MODE_REPLAY, obs.mode)
        self.assertEqual((True, REASON_ALIGNED), obs.live_table_applicable())
        obs.set_unconfirmed(2)
        self.assertEqual((False, REASON_UNCONFIRMED), obs.live_table_applicable())


class AssistedObservationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl = SessionController(Path(self.tmp.name) / "test.db")
        self.ctrl.new_shoe(RuleProfile(n_decks=6, split_match="same_rank"))
        self.ctrl.start_round()
        self.work = AssistedRecording(self.ctrl)
        self.source = Path(self.tmp.name) / "source.png"
        self.source.write_bytes(b"fixture-only")

    def tearDown(self):
        self.work.close()
        self.ctrl.close()
        self.tmp.cleanup()

    def test_unconfirmed_candidate_leaves_ledger_unchanged_and_demotes(self):
        before = self.ctrl.ledger.to_list()
        self.work.connect("src", lambda: "")
        draft = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source,
                                  crop_image=self.source)
        self.assertIsNotNone(draft)
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.assertEqual((False, REASON_UNCONFIRMED), self.work.observation.live_table_applicable())

    def test_overflow_then_empty_queue_still_needs_observation_check(self):
        before = self.ctrl.ledger.to_list()
        self.work.capacity = 1
        self.work.connect("src", lambda: "")
        first = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source,
                                  crop_image=self.source)
        self.assertIsNone(self.work.enqueue(key="b", rank="3", original={}, source_image=self.source,
                                            crop_image=self.source))
        extra = self.work.enqueue(key="c", rank="4", original={}, source_image=self.source,
                                  crop_image=self.source)
        self.assertIsNone(extra)
        self.work.select(first.draft_id)
        with self.assertRaisesRegex(DraftError, "未处理"):
            self.work.complete_observation_check()
        self.work.edit(operation="reject")
        self.work.confirm(first.draft_id)
        self.assertEqual([], self.work.unresolved_pending)
        self.assertEqual((False, REASON_OVERFLOW), self.work.observation.live_table_applicable())
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.work.complete_observation_check()
        self.assertEqual((True, REASON_ALIGNED), self.work.observation.live_table_applicable())
        self.assertEqual(before, self.ctrl.ledger.to_list())

    def test_reject_fake_establishes_a_new_valid_epoch_without_ledger_write(self):
        before = self.ctrl.ledger.to_list()
        self.work.connect("src", lambda: "")
        draft = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source,
                                  crop_image=self.source)
        self.work.edit(operation="reject")
        self.work.confirm(draft.draft_id)
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.assertEqual((True, REASON_ALIGNED), self.work.observation.live_table_applicable())
