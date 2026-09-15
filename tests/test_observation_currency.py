"""Observation currency without a capture backend or ledger writes."""
from pathlib import Path
import tempfile
import unittest

from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.observation.currency import (
    KIND_LIVE_CURRENT, KIND_MANUAL_ASOF, KIND_REPLAY, MODE_LIVE, MODE_MANUAL, MODE_REPLAY,
    REASON_ALIGNED, REASON_DEFERRED, REASON_FROZEN, REASON_MANUAL, REASON_OVERFLOW,
    REASON_REGIONS, REASON_REPLAY, REASON_STOPPED, REASON_UNCONFIRMED,
    STATUS_FROZEN, STATUS_LIVE, STATUS_STOPPED, ObservationState,
)
from blackjack_lab.ui.assisted_recording import AssistedRecording, DraftError
from blackjack_lab.ui.controller import SessionController


class ObservationStateTest(unittest.TestCase):
    def test_manual_and_replay_are_not_live_table(self):
        obs = ObservationState()
        self.assertEqual(MODE_MANUAL, obs.mode)
        self.assertEqual((False, REASON_MANUAL), obs.live_table_applicable())
        self.assertEqual(KIND_MANUAL_ASOF, obs.revision().applicability_kind())
        obs.enter_replay()
        self.assertEqual(MODE_REPLAY, obs.mode)
        self.assertEqual((False, REASON_REPLAY), obs.live_table_applicable())
        self.assertEqual(KIND_REPLAY, obs.revision().applicability_kind())

    def test_display_policy_says_what_each_state_may_show(self):
        obs = ObservationState()
        manual = obs.revision().display_policy()
        self.assertTrue(manual["asof_compute_allowed"])
        self.assertTrue(manual["asof_numbers_display_allowed"])
        self.assertFalse(manual["live_table_label_allowed"])
        self.assertFalse(manual["timely_live_claim_allowed"])
        self.assertTrue(manual["manual_is_not_live"])
        obs.enter_replay()
        replay = obs.revision().display_policy()
        self.assertFalse(replay["live_table_label_allowed"])
        self.assertTrue(replay["replay_is_not_live"])
        self.assertEqual(KIND_REPLAY, replay["applicability_kind"])
        obs.connect_source("src-policy")
        obs.note_source(STATUS_LIVE)
        live = obs.revision().display_policy()
        self.assertTrue(live["live_table_label_allowed"])
        self.assertTrue(live["timely_live_claim_allowed"])
        self.assertEqual(KIND_LIVE_CURRENT, live["applicability_kind"])
        obs.set_unconfirmed(1)
        blocked = obs.revision().display_policy()
        self.assertFalse(blocked["live_table_label_allowed"])
        self.assertFalse(blocked["timely_live_claim_allowed"])
        self.assertTrue(blocked["asof_numbers_display_allowed"])
        self.assertEqual(REASON_UNCONFIRMED, blocked["reason"])

    def test_live_unconfirmed_and_overflow_need_an_explicit_check(self):
        obs = ObservationState()
        obs.connect_source("src-1")
        obs.note_source(STATUS_LIVE)
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

    def test_frame_tick_is_not_a_knowledge_change(self):
        obs = ObservationState()
        obs.connect_source("src-1")
        obs.note_source(STATUS_LIVE, last_frame_ns=10)
        first = obs.revision()
        obs.note_frame(11)
        second = obs.revision()
        self.assertNotEqual(first.last_frame_ns, second.last_frame_ns)
        self.assertEqual(first.knowledge_identity(), second.knowledge_identity())
        self.assertTrue(obs.live_table_applicable()[0])

    def test_unresolved_regions_block_live_table(self):
        obs = ObservationState()
        obs.connect_source("src-1")
        obs.note_source(STATUS_LIVE)
        obs.set_unresolved_regions(2)
        self.assertEqual((False, REASON_REGIONS), obs.live_table_applicable())
        obs.set_unresolved_regions(0)
        self.assertEqual((True, REASON_ALIGNED), obs.live_table_applicable())

    def test_bool_and_float_counts_are_rejected(self):
        obs = ObservationState()
        with self.assertRaises(ValueError):
            obs.set_unconfirmed(True)
        with self.assertRaises(ValueError):
            obs.set_unresolved_regions(1.5)
        with self.assertRaises(ValueError):
            obs.defer_unconfirmed(-1)

    def test_live_freeze_and_stop_do_not_turn_replay_into_a_live_table(self):
        obs = ObservationState()
        obs.connect_source("src-1")
        obs.note_source(STATUS_LIVE)
        obs.note_source(STATUS_FROZEN)
        self.assertEqual(MODE_LIVE, obs.mode)
        self.assertEqual((False, REASON_FROZEN), obs.live_table_applicable())
        obs.enter_manual()
        self.assertEqual((False, REASON_MANUAL), obs.live_table_applicable())
        obs.connect_source("src-2")
        obs.note_source(STATUS_STOPPED)
        self.assertEqual((False, REASON_STOPPED), obs.live_table_applicable())
        obs.enter_replay()
        self.assertEqual(MODE_REPLAY, obs.mode)
        self.assertEqual((False, REASON_REPLAY), obs.live_table_applicable())
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

    def test_discard_does_not_auto_prove_the_table_was_reconciled(self):
        self.work.connect("src", lambda: "")
        draft = self.work.enqueue(key="a", rank="3", original={}, source_image=self.source,
                                  crop_image=self.source)
        self.work.select(draft.draft_id)
        self.work.discard()
        self.assertEqual([], self.work.unresolved_pending)
        self.assertEqual((False, REASON_DEFERRED), self.work.observation.live_table_applicable())
        self.work.complete_observation_check()
        self.assertEqual((True, REASON_ALIGNED), self.work.observation.live_table_applicable())
