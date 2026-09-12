# -*- coding: utf-8 -*-
"""旁观录像：开靴声明、跨帧去重、后揭牌不污染当时判断。"""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.vision.contracts import FACE_BACK, FACE_SHOWN
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.tracker import FrameTracker
from blackjack_lab.vision.video_contracts import (
    SHOE_START_FULL, SHOE_START_MID, SHOE_START_UNCERTAIN, Detection, TemporalCard,
    later_reveal_must_not_rewrite_prior, shoe_start_intent,
)
from blackjack_lab.vision.video_io import VideoRejected, validate_local_video_path


def _box(x, y=10, w=40, h=50):
    return {"x": x, "y": y, "w": w, "h": h}


def _det(x, region="player_target", rank="8", face=FACE_SHOWN, crop="c"):
    return Detection(
        bbox=_box(x), region_id=region, seat_hint="玩家1",
        rank_hint=rank, face_state=face, crop_sha256=crop,
    )


class TestShoeStart(unittest.TestCase):
    def test_never_invents_decks_or_burns(self):
        for kind in (SHOE_START_FULL, SHOE_START_MID, SHOE_START_UNCERTAIN):
            intent = shoe_start_intent(kind)
            self.assertIsNone(intent.n_decks)
            self.assertIsNone(intent.burn_cards_known)
            self.assertIsNone(intent.initial_burn_count)
            self.assertFalse(intent.analysis_ready)

    def test_mid_shoe_is_not_a_new_shoe(self):
        intent = shoe_start_intent(SHOE_START_MID)
        self.assertIs(intent.start_from_new_shoe, False)

    def test_uncertain_stays_unknown(self):
        self.assertIsNone(shoe_start_intent(SHOE_START_UNCERTAIN).start_from_new_shoe)


class TestTemporalHoleCard(unittest.TestCase):
    def test_later_flip_does_not_reveal_earlier_decision(self):
        hole = TemporalCard(
            observation_id="hole", initial_face=FACE_BACK, first_seen_ms=1000)
        self.assertIsNone(
            later_reveal_must_not_rewrite_prior(hole, decision_ms=1500,
                                                later_reveal_ms=4000, later_rank="K"))
        hole.revealed_ms = 4000
        hole.confirmed_rank = "K"
        self.assertEqual(hole.visible_rank_at(4000), "K")
        self.assertIsNone(hole.visible_rank_at(1500))


class TestFrameTracker(unittest.TestCase):
    def test_same_card_many_frames_is_one_track(self):
        tracker = FrameTracker()
        first = tracker.ingest(0, 0, [_det(20, crop="a")])
        second = tracker.ingest(1, 40, [_det(22, crop="a")])
        third = tracker.ingest(2, 80, [_det(21, crop="a")])
        self.assertEqual(len(first), 1)
        self.assertEqual(len(third), 1)
        self.assertEqual(first[0].observation_id, third[0].observation_id)
        self.assertEqual(second[0].first_seen_frame, 0)

    def test_two_eights_are_two_tracks(self):
        tracker = FrameTracker()
        tracks = tracker.ingest(0, 0, [
            _det(20, crop="left"),
            _det(90, crop="right"),
        ])
        self.assertEqual(len(tracks), 2)
        self.assertNotEqual(tracks[0].observation_id, tracks[1].observation_id)
        again = tracker.ingest(1, 40, [
            _det(21, crop="left"),
            _det(91, crop="right"),
        ])
        self.assertEqual(len(again), 2)
        ids = {t.observation_id for t in again}
        self.assertEqual(ids, {tracks[0].observation_id, tracks[1].observation_id})

    def test_split_move_updates_seat_not_new_deal(self):
        tracker = FrameTracker()
        first = tracker.ingest(0, 0, [_det(20, region="player_target", crop="one")])
        moved = tracker.ingest(3, 120, [_det(200, region="player_split", crop="one")])
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].observation_id, first[0].observation_id)
        self.assertTrue(moved[0].moved)
        self.assertEqual(moved[0].region_id, "player_split")

    def test_replay_after_commit_does_not_create_new_id(self):
        tracker = FrameTracker()
        first = tracker.ingest(0, 0, [_det(20, crop="a")])
        tracker.mark_committed(first[0].observation_id)
        replay = tracker.ingest(0, 0, [_det(20, crop="a")])
        self.assertEqual(len(replay), 1)
        self.assertEqual(replay[0].observation_id, first[0].observation_id)
        self.assertTrue(replay[0].committed)
        self.assertFalse(replay[0].may_write_ledger())


class TestVideoPath(unittest.TestCase):
    def test_reject_http(self):
        with self.assertRaises(VideoRejected):
            validate_local_video_path("https://example.invalid/table.mp4")

    def test_reject_png_as_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "still.png"
            path.write_bytes(b"not-a-video")
            with self.assertRaises(VideoRejected):
                validate_local_video_path(path)


@unittest.skipUnless(cv2_available(), "需要 OpenCV 才能编解码短录像")
class TestVideoReader(unittest.TestCase):
    def test_seek_does_not_rewrite_source(self):
        from blackjack_lab.vision.synthetic import FELT, RgbCanvas
        from blackjack_lab.vision.video_io import VideoReader

        cv2 = __import__("cv2")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "clip.avi"
            canvas = RgbCanvas(64, 48, FELT)
            writer = cv2.VideoWriter(
                str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
            self.assertTrue(writer.isOpened())
            frame = __import__("numpy").frombuffer(bytes(canvas.buf), dtype="uint8").reshape(48, 64, 3)
            for _ in range(4):
                writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
            writer.release()
            before = source.read_bytes()
            with VideoReader(source) as reader:
                frame = reader.seek(0)
                self.assertEqual(frame.format, "video-frame")
                self.assertEqual((frame.width, frame.height), (64, 48))
                reader.seek(2)
            self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
