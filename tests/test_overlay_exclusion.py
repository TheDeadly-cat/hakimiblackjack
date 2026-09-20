"""Source frames that contain the lab overlay are not table sources."""
import unittest
from types import SimpleNamespace

from blackjack_lab.capture.contracts import CaptureRejected
from blackjack_lab.capture.overlay_exclusion import (
    is_lab_overlay, refuse_overlay_source, source_frame_excludes_overlay,
)


def _solid(color, height=4, width=6):
    return [[color for _ in range(width)] for _ in range(height)]


class OverlayExclusionTest(unittest.TestCase):
    def test_lab_overlay_window_is_refused_as_source(self):
        overlay = SimpleNamespace(title="Hakimi 辅助记牌")
        table = SimpleNamespace(title="Authorized table replay")
        self.assertTrue(is_lab_overlay(overlay))
        self.assertFalse(is_lab_overlay(table))
        with self.assertRaises(CaptureRejected):
            refuse_overlay_source(overlay)
        self.assertIs(table, refuse_overlay_source(table))

    def test_overlay_color_in_source_frame_fails(self):
        felt = (40, 90, 30)
        overlay = (12, 43, 20)
        captured = _solid(overlay)
        body = source_frame_excludes_overlay(captured, felt_bgr=felt, overlay_bgr=overlay)
        self.assertFalse(body["passed"])
        self.assertGreater(body["overlay_fraction"], 0.5)
        self.assertTrue(body["not_fullscreen_acceptance"])

    def test_felt_only_frame_passes_this_item_not_fullscreen(self):
        felt = (40, 90, 30)
        overlay = (12, 43, 20)
        captured = _solid(felt)
        body = source_frame_excludes_overlay(captured, felt_bgr=felt, overlay_bgr=overlay)
        self.assertTrue(body["passed"])
        self.assertLessEqual(body["overlay_fraction"], 0.01)
        self.assertTrue(body["not_fullscreen_acceptance"])

    def test_missing_frame_is_not_a_pass(self):
        body = source_frame_excludes_overlay(None, felt_bgr=(1, 2, 3), overlay_bgr=(9, 8, 7))
        self.assertFalse(body["passed"])

    def test_lab_overlay_windows_are_not_listed_as_table_sources(self):
        from blackjack_lab.capture.window_list import WindowInfo, should_list_window

        def info(title, pid=11, width=800, height=600):
            return WindowInfo(hwnd=1, title=title, class_name="x", process_id=pid,
                              process_name="x.exe", x=0, y=0, width=width, height=height,
                              client_width=width, client_height=height, dpi=96, minimized=False)

        self.assertFalse(should_list_window(info("Hakimi 辅助记牌"), own_pid=99))
        self.assertFalse(should_list_window(info("Chrome", pid=99), own_pid=99))
        self.assertFalse(should_list_window(info("Chrome", width=100, height=100), own_pid=1))
        self.assertTrue(should_list_window(info("Authorized table replay"), own_pid=1))

    def test_open_window_source_refuses_overlay_title(self):
        from unittest.mock import patch
        from blackjack_lab.capture.wgc_source import open_window_source
        overlay = SimpleNamespace(
            hwnd=1, title="Hakimi 辅助记牌", process_id=2, minimized=False)
        with patch("blackjack_lab.capture.window_list.describe_window", return_value=overlay):
            with self.assertRaises(CaptureRejected):
                open_window_source(1)
