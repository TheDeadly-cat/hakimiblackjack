"""Continuous source ownership without a capture backend or automatic commits."""
from copy import deepcopy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from tests.test_realtime_review import fixture, np
from blackjack_lab.core.rules import RuleProfile
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ui.assisted_recording import AssistedRecording, DraftError
from blackjack_lab.ui.continuous_review import ContinuousReviewFeed


@unittest.skipIf(np is None, "optional numpy")
class ContinuousReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ctrl = SessionController(Path(self.tmp.name) / "db.sqlite")
        self.ctrl.new_shoe(RuleProfile(n_decks=6))
        self.ctrl.start_round()
        self.work = AssistedRecording(self.ctrl)
        self.owner, self.row, self.intake = fixture()
        self.owner.finished = self.owner.source.finished = False
        self.owner.source.preview = self.intake.preview
        self.owner._latest = self.row
        self.feed = ContinuousReviewFeed(self.work, self.owner)

    def tearDown(self):
        self.work.close()
        self.ctrl.close()
        self.tmp.cleanup()

    def test_original_row_unchanged_repeated_poll_no_duplicate_and_source_continues(self):
        original = deepcopy(self.row.recognition.as_dict())
        before = self.ctrl.ledger.to_list()
        self.assertTrue(self.feed.poll())
        selected = self.work.selected_id
        self.assertFalse(self.feed.poll())
        self.assertEqual(2, len(self.work.pending))
        self.assertEqual(selected, self.work.selected_id)
        self.assertEqual(original, self.row.recognition.as_dict())
        self.assertFalse(self.owner.stopped)
        self.assertEqual(before, self.ctrl.ledger.to_list())
        self.assertTrue(Path(self.work.selected.crop_image).is_file())

    def test_source_freeze_stops_confirmation_but_manual_capture_is_independent(self):
        self.feed.poll()
        selected = self.work.selected_id
        with patch("blackjack_lab.ui.continuous_review.time.perf_counter_ns",
                   return_value=self.row.packet.observed_monotonic_ns + 2_000_000_000):
            with self.assertRaisesRegex(DraftError, "未更新"):
                self.work.confirm(selected)
        self.work.manual()
        self.assertIsNotNone(self.work.selected.source_image)

    def test_explicit_video_replay_stops_source_and_can_drain_current_round(self):
        self.feed.poll()
        self.owner.finished = self.owner.source.finished = True
        with self.assertRaisesRegex(DraftError, "已停止"):
            self.work.confirm(self.work.selected_id)
        self.feed.enter_replay()
        self.assertTrue(self.feed.replay_mode)
        self.work.confirm(self.work.selected_id)
        self.assertEqual(1, len(self.work.records()))
        self.assertFalse(self.feed.poll())

    def test_rebinding_retained_frame_never_overwrites_original_snapshot(self):
        self.feed.poll()
        original_path = Path(self.work.selected.source_image)
        before = original_path.read_bytes()
        self.feed = ContinuousReviewFeed(self.work, self.owner)
        self.feed.poll()
        paths = {d.source_image for d in self.work.pending}
        self.assertEqual(2, len(paths))
        self.assertEqual(before, original_path.read_bytes())

    def test_unranked_region_is_visible_and_later_same_track_hint_can_queue(self):
        obs = self.row.recognition.observations[0]
        obs.reject_reason = "不确定"
        self.feed.poll()
        self.assertEqual(1, self.feed.unranked_count)
        self.assertEqual(1, len(self.work.pending))
        self.assertNotIn((self.feed.epoch, obs.observation_id), self.work.seen)
        record = self.feed.snapshot_unranked()
        self.assertEqual(obs.bbox, record["regions"][0]["bbox"])
        self.assertFalse(self.owner.stopped)
        self.assertEqual([], self.work.records())
        obs.reject_reason = None
        self.row.row_id += 1
        self.owner.records.append(self.row.metadata())
        self.feed.poll()
        self.assertEqual(2, len(self.work.pending))

    def test_explicit_unranked_supplement_bypasses_automatic_capacity_but_never_auto_commits(self):
        self.work.capacity = 1
        self.row.recognition.observations[0].reject_reason = "不确定"
        self.feed.poll()
        self.assertEqual(1, len(self.work.automatic_pending))
        record = self.feed.snapshot_unranked()
        draft = self.feed.draft_region(record, 0)
        self.assertTrue(draft.human_requested)
        self.assertIsNone(draft.rank)
        self.assertEqual(1, len(self.work.manual_pending))
        self.assertEqual([], self.work.records())
        self.work.edit(face="unknown")
        self.work.confirm(draft.draft_id)
        self.assertEqual(1, self.ctrl.state().current.shoe.unrevealed_out)

    def test_all_regions_mode_is_available_and_keeps_original_rejection(self):
        self.row.recognition.observations[0].reject_reason = "不确定"
        self.feed.poll()
        self.feed.set_queue_policy("all-regions")
        self.feed.poll()
        self.assertEqual(2, len(self.work.pending))
        unresolved = next(d for d in self.work.pending if d.rank is None)
        self.assertEqual("不确定", unresolved.original["observation"]["reject_reason"])

    def test_replay_retains_last_uncertain_snapshot_and_old_rebind_cannot_submit_it(self):
        self.row.recognition.observations[0].reject_reason = "不确定"
        self.feed.poll()
        self.owner.finished = self.owner.source.finished = True
        self.feed.enter_replay()
        record = self.feed.snapshot_unranked()
        self.assertTrue(self.owner.stopped)
        self.assertIsNotNone(self.feed.draft_region(record, 0))
        new_feed = ContinuousReviewFeed(self.work, self.owner)
        with self.assertRaises(ValueError):
            new_feed.draft_region(record, 0)


if __name__ == "__main__":
    unittest.main()
