# -*- coding: utf-8 -*-
"""实时帧接入识别管线：颜色通道、归一化标定与布局版本。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VISION_DIR = REPO_ROOT / "blackjack_lab" / "vision"
CAPTURE_DIR = REPO_ROOT / "blackjack_lab" / "capture"

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from blackjack_lab.capture.contracts import FramePacket
from blackjack_lab.vision.deps import ImageRejected
from blackjack_lab.vision.live_input import (
    LiveStyle, NormalizedBox, capture_crop_pixels, frame_to_loaded, layout_for_frame,
)


def make_packet(pixels, *, frame_id=1, stream_epoch=0, layout_version=0,
                signature="sig", is_repeat=False):
    height, width = (pixels.shape[0], pixels.shape[1]) if pixels is not None else (0, 0)
    return FramePacket(
        source_id="window:99",
        stream_epoch=stream_epoch,
        frame_id=frame_id,
        media_time_ns=123456,
        observed_at=1_700_000_000.0,
        observed_monotonic_ns=5_000,
        image_size=(width, height),
        source_size=(width, height),
        crop_origin=(0, 0),
        layout_version=layout_version,
        frame_content_signature=signature,
        is_repeat=is_repeat,
        pixels=pixels,
    )


def simple_style(**kwargs):
    regions = kwargs.pop("regions", None) or {
        "dealer": NormalizedBox(0.3, 0.05, 0.4, 0.25, seat_hint="庄家"),
        "seat_1": NormalizedBox(0.0, 0.4, 0.5, 0.5, seat_hint="玩家1"),
    }
    return LiveStyle(style_id="live-test-v1", regions=regions, **kwargs)


@unittest.skipIf(np is None, "需要 numpy")
class TestFrameToLoaded(unittest.TestCase):
    def test_bgr_pixels_become_rgb_bytes(self):
        pixels = np.zeros((1, 2, 3), dtype=np.uint8)
        pixels[0, 0] = (10, 20, 30)   # BGR
        pixels[0, 1] = (200, 100, 50)
        loaded = frame_to_loaded(make_packet(pixels))
        self.assertEqual(loaded.width, 2)
        self.assertEqual(loaded.height, 1)
        self.assertEqual(loaded.format, "live-frame")
        # RGB 顺序：BGR(10,20,30) -> RGB(30,20,10)
        self.assertEqual(tuple(loaded.rgb[0:3]), (30, 20, 10))
        self.assertEqual(tuple(loaded.rgb[3:6]), (50, 100, 200))
        self.assertEqual(loaded.byte_size, 6)

    def test_digest_is_stable_for_same_frame_and_unique_per_frame(self):
        pixels = np.full((2, 2, 3), 7, dtype=np.uint8)
        one = frame_to_loaded(make_packet(pixels, frame_id=1, signature="a"))
        same = frame_to_loaded(make_packet(pixels, frame_id=1, signature="a"))
        other = frame_to_loaded(make_packet(pixels, frame_id=2, signature="a"))
        self.assertEqual(one.sha256, same.sha256)
        self.assertNotEqual(one.sha256, other.sha256)

    def test_epoch_changes_asset_identity(self):
        pixels = np.full((2, 2, 3), 7, dtype=np.uint8)
        first = frame_to_loaded(make_packet(pixels, stream_epoch=0))
        second = frame_to_loaded(make_packet(pixels, stream_epoch=1))
        self.assertNotEqual(first.sha256, second.sha256)

    def test_missing_pixels_rejected(self):
        with self.assertRaises(ImageRejected):
            frame_to_loaded(make_packet(None))

    def test_wrong_channel_count_rejected(self):
        with self.assertRaises(ImageRejected):
            frame_to_loaded(make_packet(np.zeros((2, 2, 4), dtype=np.uint8)))


class TestNormalizedBox(unittest.TestCase):
    def test_out_of_range_rejected(self):
        with self.assertRaises(ImageRejected):
            NormalizedBox(-0.1, 0.0, 0.5, 0.5)
        with self.assertRaises(ImageRejected):
            NormalizedBox(0.0, 0.0, 1.5, 0.5)

    def test_region_past_edge_rejected(self):
        with self.assertRaises(ImageRejected):
            NormalizedBox(0.8, 0.0, 0.5, 0.5)

    def test_zero_size_rejected(self):
        with self.assertRaises(ImageRejected):
            NormalizedBox(0.1, 0.1, 0.0, 0.2)

    def test_to_pixels_scales(self):
        box = NormalizedBox(0.5, 0.25, 0.25, 0.5, seat_hint="玩家3")
        self.assertEqual(box.to_pixels(1000, 400),
                         {"x": 500, "y": 100, "w": 250, "h": 200, "seat_hint": "玩家3"})

    def test_to_pixels_clamps_to_frame(self):
        box = NormalizedBox(0.999, 0.0, 0.001, 1.0)
        pixels = box.to_pixels(100, 10)
        self.assertLessEqual(pixels["x"] + pixels["w"], 100)


class TestLiveStyle(unittest.TestCase):
    def test_round_trip_preserves_regions(self):
        style = simple_style(title_fragment="Live Blackjack")
        with tempfile.TemporaryDirectory() as tmp:
            path = style.save(Path(tmp) / "style.json")
            loaded = LiveStyle.load(path)
        self.assertEqual(loaded.style_id, style.style_id)
        self.assertEqual(set(loaded.regions), {"dealer", "seat_1"})
        self.assertEqual(loaded.regions["dealer"].seat_hint, "庄家")
        self.assertEqual(loaded.title_fragment, "Live Blackjack")

    def test_save_is_atomic_and_leaves_no_temp_file(self):
        style = simple_style()
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nested" / "style.json"
            style.save(dest)
            self.assertTrue(dest.is_file())
            self.assertEqual(list(dest.parent.glob("*.tmp")), [])

    def test_absolute_pixel_style_rejected(self):
        with self.assertRaises(ImageRejected):
            LiveStyle.from_dict({
                "style_id": "navy-live-felt-v1",
                "regions": {"dealer": {"x": 400, "y": 40, "w": 380, "h": 110}},
            })

    def test_empty_regions_rejected(self):
        with self.assertRaises(ImageRejected):
            LiveStyle.from_dict({
                "style_id": "x", "normalized": True, "regions": {}})

    def test_layout_version_changes_with_frame_size(self):
        style = simple_style()
        self.assertNotEqual(style.layout_version(1180, 542),
                            style.layout_version(2360, 1084))

    def test_layout_version_changes_when_regions_move(self):
        before = simple_style().layout_version(1000, 500)
        moved = simple_style(regions={
            "dealer": NormalizedBox(0.31, 0.05, 0.4, 0.25, seat_hint="庄家"),
            "seat_1": NormalizedBox(0.0, 0.4, 0.5, 0.5, seat_hint="玩家1"),
        }).layout_version(1000, 500)
        self.assertNotEqual(before, moved)

    def test_layout_version_is_stable_for_same_input(self):
        self.assertEqual(simple_style().layout_version(800, 600),
                         simple_style().layout_version(800, 600))


class TestLayoutForFrame(unittest.TestCase):
    def test_regions_scale_with_frame(self):
        style = simple_style()
        small = layout_for_frame(style, 1000, 400)
        large = layout_for_frame(style, 2000, 800)
        self.assertEqual(small.region("dealer").x, 300)
        self.assertEqual(large.region("dealer").x, 600)
        self.assertEqual(large.canvas_width, 2000)

    def test_seat_hints_survive(self):
        layout = layout_for_frame(simple_style(), 1000, 400)
        self.assertEqual(layout.region("seat_1").seat_hint, "玩家1")

    def test_live_layout_has_no_absolute_source_crop(self):
        """实时帧已在捕获层裁好，布局不得再套一层固定像素裁区。"""
        layout = layout_for_frame(simple_style(), 1180, 542)
        self.assertIsNone(layout.source_crop)
        self.assertIsNone(layout.felt_crop)

    def test_invalid_frame_size_rejected(self):
        with self.assertRaises(ImageRejected):
            layout_for_frame(simple_style(), 0, 400)

    def test_capture_crop_converted_to_source_pixels(self):
        style = simple_style(capture_crop=NormalizedBox(0.1, 0.2, 0.5, 0.4))
        self.assertEqual(capture_crop_pixels(style, 2560, 1440), (256, 288, 1280, 576))

    def test_no_capture_crop_returns_none(self):
        self.assertIsNone(capture_crop_pixels(simple_style(), 2560, 1440))


class TestModuleBoundaries(unittest.TestCase):
    """识别器不碰 Win32，捕获层不认识牌面。两边都要能单独导入。"""

    def test_vision_does_not_import_capture(self):
        for path in VISION_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from ..capture", text, f"{path.name} 不应导入捕获层")
            self.assertNotIn("blackjack_lab.capture", text, f"{path.name} 不应导入捕获层")

    def test_capture_does_not_import_vision_or_ledger(self):
        for path in CAPTURE_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("from ..vision", "from ..ledger", "from ..ui",
                              "from ..analysis", "from ..storage"):
                self.assertNotIn(forbidden, text, f"{path.name} 不应导入 {forbidden}")

    def test_live_input_imports_without_capture_backend(self):
        """没装 windows-capture 也要能导入识别侧的实时接入。"""
        code = (
            "import sys; import blackjack_lab.vision.live_input as m; "
            "assert 'windows_capture' not in sys.modules, '不应拖入采集后端'; "
            "assert m.LIVE_SCHEMA_VERSION"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, (proc.stdout or "") + (proc.stderr or ""))

    def test_capture_package_imports_without_backend_or_numpy_use(self):
        code = (
            "import sys; import blackjack_lab.capture as c; "
            "assert 'windows_capture' not in sys.modules, '不应在导入时拖入后端'; "
            "assert c.SOURCE_WINDOW == 'window'"
        )
        proc = subprocess.run(
            [sys.executable, "-c", code], cwd=str(REPO_ROOT),
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(proc.returncode, 0, (proc.stdout or "") + (proc.stderr or ""))


if __name__ == "__main__":
    unittest.main()
