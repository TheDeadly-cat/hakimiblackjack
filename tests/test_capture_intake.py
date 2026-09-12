# -*- coding: utf-8 -*-
"""帧入口单元测试：所有权、采样、重复帧、有界队列与换代。

不需要桌面，也不需要 windows-capture；这层逻辑必须能脱离 GUI 验证。
"""
from __future__ import annotations

import unittest

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from blackjack_lab.capture.contracts import (
    SOURCE_MONITOR, SOURCE_WINDOW, STATUS_BLACK, STATUS_FROZEN, STATUS_IDLE,
    STATUS_LIVE, STATUS_NO_NEW_FRAME, STATUS_STOPPED,
    CaptureRejected, GenerationToken, SourceSpec, frame_signature, looks_black,
)
from blackjack_lab.capture.frame_intake import FrameIntake

MS = 1_000_000
S = 1_000_000_000


def bgra(height, width, value=128):
    return np.full((height, width, 4), value, dtype=np.uint8)


@unittest.skipIf(np is None, "需要 numpy")
class TestFrameOwnership(unittest.TestCase):
    def test_packet_pixels_survive_backend_buffer_reuse(self):
        """采集后端复用缓冲后，已入队的帧不能跟着变。"""
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        buffer = bgra(8, 10, 50)
        packet = intake.offer(buffer, now_ns=S)
        self.assertIsNotNone(packet)
        original = packet.pixels.copy()

        buffer[:, :, :] = 255  # 后端把同一块内存用于下一帧

        self.assertTrue((packet.pixels == original).all())
        self.assertEqual(int(packet.pixels[0, 0, 0]), 50)

    def test_pixels_are_bgr_three_channels(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        packet = intake.offer(bgra(4, 6), now_ns=S)
        self.assertEqual(packet.pixels.shape, (4, 6, 3))
        self.assertEqual(packet.image_size, (6, 4))


@unittest.skipIf(np is None, "需要 numpy")
class TestSampling(unittest.TestCase):
    def test_frames_faster_than_target_fps_are_dropped_and_counted(self):
        intake = FrameIntake("window:1", target_fps=10.0)
        intake.mark_started(now_ns=0)
        first = intake.offer(bgra(4, 4, 10), now_ns=S)
        self.assertIsNotNone(first)
        # 距上一帧仅 10ms，低于 100ms 采样间隔
        second = intake.offer(bgra(4, 4, 20), now_ns=S + 10 * MS)
        self.assertIsNone(second)
        self.assertEqual(intake.stats.dropped_by_sampling, 1)
        third = intake.offer(bgra(4, 4, 30), now_ns=S + 200 * MS)
        self.assertIsNotNone(third)
        self.assertEqual(intake.stats.accepted, 2)
        self.assertEqual(intake.stats.arrived, 3)
        # 被采样丢掉的帧数跟着下一个被接受的帧一起报出来
        self.assertEqual(third.dropped_before, 1)

    def test_arrived_counts_every_frame_even_when_dropped(self):
        intake = FrameIntake("window:1", target_fps=1.0)
        intake.mark_started(now_ns=0)
        for i in range(5):
            intake.offer(bgra(4, 4, i), now_ns=S + i * MS)
        self.assertEqual(intake.stats.arrived, 5)
        self.assertEqual(intake.stats.accepted, 1)
        self.assertEqual(intake.stats.dropped_by_sampling, 4)


@unittest.skipIf(np is None, "需要 numpy")
class TestRepeatDetection(unittest.TestCase):
    def test_identical_frames_are_marked_repeat(self):
        """连续读取同一张冻结画面不得增加独立支持帧数量。"""
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        a = intake.offer(bgra(6, 6, 77), now_ns=S)
        b = intake.offer(bgra(6, 6, 77), now_ns=2 * S)
        self.assertFalse(a.is_repeat)
        self.assertTrue(b.is_repeat)
        self.assertEqual(intake.stats.repeats, 1)
        self.assertEqual(a.frame_content_signature, b.frame_content_signature)

    def test_changed_frame_is_not_repeat(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        intake.offer(bgra(6, 6, 77), now_ns=S)
        changed = bgra(6, 6, 77)
        changed[0, 0] = 1
        packet = intake.offer(changed, now_ns=2 * S)
        self.assertFalse(packet.is_repeat)
        self.assertEqual(intake.stats.repeats, 0)

    def test_signature_samples_rows_but_detects_change(self):
        base = bgra(64, 8, 100)[:, :, :3]
        other = base.copy()
        other[0, 0, 0] = 250
        self.assertNotEqual(frame_signature(base), frame_signature(other))
        self.assertEqual(frame_signature(base), frame_signature(base.copy()))

    def test_black_frame_detected(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        packet = intake.offer(bgra(6, 6, 0), now_ns=S)
        self.assertTrue(packet.is_black)
        self.assertTrue(looks_black(packet.pixels))
        self.assertEqual(intake.status(now_ns=S), STATUS_BLACK)


@unittest.skipIf(np is None, "需要 numpy")
class TestBoundedQueue(unittest.TestCase):
    def test_queue_keeps_latest_and_counts_drops(self):
        intake = FrameIntake("window:1", target_fps=1000.0, queue_length=2)
        intake.mark_started(now_ns=0)
        for i in range(5):
            intake.offer(bgra(4, 4, i * 10), now_ns=S + i * S)
        self.assertEqual(intake.peek_depth(), 2)
        self.assertEqual(intake.stats.dropped_by_queue, 3)

        newest = intake.latest()
        self.assertEqual(newest.frame_id, 5)
        # latest() 只交最新一帧，被跳过的那帧也要计入丢帧
        self.assertEqual(intake.stats.dropped_by_queue, 4)
        self.assertIsNone(intake.latest())

    def test_queue_length_must_be_positive(self):
        with self.assertRaises(CaptureRejected):
            FrameIntake("window:1", queue_length=0)

    def test_target_fps_must_be_positive(self):
        with self.assertRaises(CaptureRejected):
            FrameIntake("window:1", target_fps=0)


@unittest.skipIf(np is None, "需要 numpy")
class TestCropping(unittest.TestCase):
    def test_crop_limits_pixels_to_selected_region(self):
        intake = FrameIntake("window:1", target_fps=1000.0, crop=(2, 1, 3, 2))
        intake.mark_started(now_ns=0)
        frame = bgra(10, 10, 5)
        frame[1:3, 2:5] = 200
        packet = intake.offer(frame, now_ns=S)
        self.assertEqual(packet.image_size, (3, 2))
        self.assertEqual(packet.crop_origin, (2, 1))
        self.assertEqual(packet.source_size, (10, 10))
        self.assertTrue((packet.pixels == 200).all())

    def test_out_of_bounds_crop_yields_no_frame(self):
        """宁可不给帧，也不把错的一块交给识别器。"""
        intake = FrameIntake("window:1", target_fps=1000.0, crop=(0, 0, 50, 50))
        intake.mark_started(now_ns=0)
        self.assertIsNone(intake.offer(bgra(10, 10), now_ns=S))
        self.assertEqual(intake.stats.accepted, 0)


@unittest.skipIf(np is None, "需要 numpy")
class TestGenerations(unittest.TestCase):
    def test_source_resize_starts_new_epoch_and_clears_queue(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        intake.offer(bgra(10, 10), now_ns=S)
        self.assertEqual(intake.stream_epoch, 0)
        self.assertEqual(intake.peek_depth(), 1)

        intake.offer(bgra(20, 30), now_ns=2 * S)
        self.assertEqual(intake.stream_epoch, 1)
        self.assertEqual(intake.stats.resize_events, 1)
        # 换代时旧帧被清掉，只剩换代之后这一帧
        self.assertEqual(intake.peek_depth(), 1)
        self.assertEqual(intake.latest().source_size, (30, 20))

    def test_layout_version_change_invalidates_old_token(self):
        intake = FrameIntake("window:1", target_fps=1000.0, layout_version=7)
        intake.mark_started(now_ns=0)
        intake.offer(bgra(8, 8), now_ns=S)
        old = intake.token()

        intake.set_layout_version(9, reason="重新标定座位")

        self.assertFalse(old.matches(intake.token()))
        self.assertEqual(intake.layout_version, 9)
        self.assertEqual(intake.peek_depth(), 0)

    def test_same_layout_version_does_not_bump_epoch(self):
        intake = FrameIntake("window:1", layout_version=3)
        before = intake.stream_epoch
        intake.set_layout_version(3)
        self.assertEqual(intake.stream_epoch, before)

    def test_crop_change_starts_new_epoch(self):
        intake = FrameIntake("window:1")
        intake.set_crop((0, 0, 4, 4))
        self.assertEqual(intake.stream_epoch, 1)
        intake.set_crop((0, 0, 4, 4))
        self.assertEqual(intake.stream_epoch, 1)

    def test_packet_token_matches_intake_token(self):
        intake = FrameIntake("window:1", target_fps=1000.0, layout_version=4)
        intake.mark_started(now_ns=0)
        packet = intake.offer(bgra(8, 8), now_ns=S)
        self.assertTrue(packet.token().matches(intake.token()))

    def test_epoch_reasons_are_recorded(self):
        intake = FrameIntake("window:1")
        intake.new_epoch("切换来源")
        report = intake.report()
        self.assertEqual(report["epoch_changes"][-1]["reason"], "切换来源")


@unittest.skipIf(np is None, "需要 numpy")
class TestStatus(unittest.TestCase):
    def test_status_lifecycle(self):
        intake = FrameIntake("window:1", target_fps=1000.0, stall_ms=500)
        self.assertEqual(intake.status(now_ns=0), STATUS_IDLE)

        intake.mark_started(now_ns=0)
        intake.offer(bgra(4, 4, 60), now_ns=S)
        self.assertEqual(intake.status(now_ns=S), STATUS_LIVE)

        # 超过 500ms 没有新帧：报「暂无新帧」，不是捕获失败
        self.assertEqual(intake.status(now_ns=S + 600 * MS), STATUS_NO_NEW_FRAME)

        intake.mark_stopped()
        self.assertEqual(intake.status(now_ns=S), STATUS_STOPPED)

    def test_frozen_status_when_content_repeats(self):
        intake = FrameIntake("window:1", target_fps=1000.0, stall_ms=5000)
        intake.mark_started(now_ns=0)
        intake.offer(bgra(4, 4, 60), now_ns=S)
        intake.offer(bgra(4, 4, 60), now_ns=2 * S)
        self.assertEqual(intake.status(now_ns=2 * S), STATUS_FROZEN)

    def test_stopped_intake_rejects_frames(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        intake.mark_stopped()
        self.assertIsNone(intake.offer(bgra(4, 4), now_ns=S))

    def test_denied_intake_rejects_frames(self):
        intake = FrameIntake("window:1", target_fps=1000.0)
        intake.mark_started(now_ns=0)
        intake.mark_denied("平台拒绝")
        self.assertIsNone(intake.offer(bgra(4, 4), now_ns=S))
        self.assertEqual(intake.report()["denied_reason"], "平台拒绝")

    def test_report_separates_arrival_and_sampled_rate(self):
        intake = FrameIntake("window:1", target_fps=2.0)
        intake.mark_started(now_ns=0)
        for i in range(10):
            intake.offer(bgra(4, 4, i), now_ns=i * 100 * MS)
        report = intake.report(now_ns=S)
        self.assertGreater(report["arrival_fps"], report["accepted_fps"])
        self.assertIn("不是保证速率", report["fps_note"])


class TestSourceSpec(unittest.TestCase):
    def test_window_source_requires_hwnd(self):
        with self.assertRaises(CaptureRejected):
            SourceSpec(kind=SOURCE_WINDOW)

    def test_monitor_source_requires_index(self):
        with self.assertRaises(CaptureRejected):
            SourceSpec(kind=SOURCE_MONITOR)

    def test_unknown_kind_rejected(self):
        with self.assertRaises(CaptureRejected):
            SourceSpec(kind="camera", window_hwnd=1)

    def test_invalid_crop_rejected(self):
        with self.assertRaises(CaptureRejected):
            SourceSpec(kind=SOURCE_WINDOW, window_hwnd=1, crop=(0, 0, 0, 10))
        with self.assertRaises(CaptureRejected):
            SourceSpec(kind=SOURCE_WINDOW, window_hwnd=1, crop=(-1, 0, 10, 10))

    def test_source_id_is_stable(self):
        spec = SourceSpec(kind=SOURCE_WINDOW, window_hwnd=4242)
        self.assertEqual(spec.source_id, "window:4242")
        self.assertEqual(
            SourceSpec(kind=SOURCE_MONITOR, monitor_index=1).source_id, "monitor:1")


class TestGenerationToken(unittest.TestCase):
    def test_tokens_differ_on_any_field(self):
        base = GenerationToken("window:1", 0, 0)
        self.assertTrue(base.matches(GenerationToken("window:1", 0, 0)))
        self.assertFalse(base.matches(GenerationToken("window:2", 0, 0)))
        self.assertFalse(base.matches(GenerationToken("window:1", 1, 0)))
        self.assertFalse(base.matches(GenerationToken("window:1", 0, 1)))


if __name__ == "__main__":
    unittest.main()
