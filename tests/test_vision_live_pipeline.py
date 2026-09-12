# -*- coding: utf-8 -*-
"""实时帧走通现有识别管线。

用已冻结的自建合成样张冒充一帧实时画面，验证接线是真的出候选，
而不是只把帧搬来搬去。合成样张只能证明这条链路通，
不能证明真实牌桌达标。
"""
from __future__ import annotations

import unittest
from pathlib import Path

try:
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

from blackjack_lab.vision.contracts import REVIEW_PENDING, default_layout
from blackjack_lab.vision.image_io import load_image
from blackjack_lab.vision.live_input import (
    SOURCE_LIVE_CAPTURE, LiveStyle, NormalizedBox, recognize_frame,
)
from blackjack_lab.vision.pipeline import default_templates_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE = REPO_ROOT / "fixtures" / "vision" / "synthetic-v1" / "smoke" / "all13.png"


class FakePacket:
    """符合 vision.live_input.FrameLike 结构的最小实现。"""

    def __init__(self, pixels, **kwargs):
        self.pixels = pixels
        self.source_id = kwargs.get("source_id", "window:1234")
        self.stream_epoch = kwargs.get("stream_epoch", 0)
        self.frame_id = kwargs.get("frame_id", 1)
        self.observed_at = kwargs.get("observed_at", 1_700_000_000.5)
        self.layout_version = kwargs.get("layout_version", 0)
        self.frame_content_signature = kwargs.get("frame_content_signature", "sig-1")
        self.is_repeat = kwargs.get("is_repeat", False)
        self.is_black = kwargs.get("is_black", False)

    @property
    def width(self):
        return int(self.pixels.shape[1])

    @property
    def height(self):
        return int(self.pixels.shape[0])


def synthetic_live_style() -> LiveStyle:
    """把已冻结的 synthetic-felt-v1 牌区换算成归一化实时样式。"""
    base = default_layout()
    width, height = base.canvas_width, base.canvas_height
    regions = {}
    for name, box in base.regions.items():
        regions[name] = NormalizedBox(
            x=box.x / width, y=box.y / height,
            w=box.w / width, h=box.h / height,
            seat_hint=box.seat_hint)
    return LiveStyle(
        style_id="synthetic-felt-v1",
        regions=regions,
        felt_kind=base.felt_kind,
        detect={
            "min_w": base.detect_min_w, "min_h": base.detect_min_h,
            "max_w": base.detect_max_w, "max_h": base.detect_max_h,
            "min_aspect": base.detect_min_aspect,
            "max_aspect": base.detect_max_aspect,
            "threshold": base.detect_threshold,
            "expected_card_w": base.expected_card_w,
        },
        title_fragment="synthetic",
    )


def frame_from_fixture(**kwargs) -> FakePacket:
    loaded = load_image(SMOKE, default_layout())
    rgb = np.frombuffer(loaded.rgb, dtype=np.uint8).reshape(
        loaded.height, loaded.width, 3)
    bgr = np.ascontiguousarray(rgb[:, :, ::-1])
    return FakePacket(bgr, **kwargs)


@unittest.skipIf(np is None, "需要 numpy")
@unittest.skipUnless(SMOKE.is_file(), "缺少自建合成样张")
class TestLiveFrameThroughPipeline(unittest.TestCase):
    def test_live_frame_produces_candidates(self):
        result = recognize_frame(
            frame_from_fixture(), synthetic_live_style(),
            templates_dir=default_templates_dir())
        self.assertTrue(result.observations, "实时帧应当产生候选")
        ranks = [o.accepted_rank() for o in result.observations]
        self.assertTrue(any(r is not None for r in ranks), f"没有任何被接受的牌面: {ranks}")

    def test_live_result_is_never_auto_accepted(self):
        result = recognize_frame(
            frame_from_fixture(), synthetic_live_style(),
            templates_dir=default_templates_dir())
        self.assertEqual(result.review_status, REVIEW_PENDING)
        self.assertFalse(result.as_dict()["writes_ledger"])
        self.assertFalse(result.as_dict()["score_is_calibrated_probability"])

    def test_source_declaration_marks_live_capture(self):
        result = recognize_frame(
            frame_from_fixture(), synthetic_live_style(),
            templates_dir=default_templates_dir())
        self.assertEqual(result.source_declaration, SOURCE_LIVE_CAPTURE)

    def test_captured_at_comes_from_frame_not_model_clock(self):
        packet = frame_from_fixture(observed_at=1_700_000_123.25)
        result = recognize_frame(packet, synthetic_live_style(),
                                 templates_dir=default_templates_dir())
        self.assertEqual(result.captured_at, 1_700_000_123.25)
        self.assertNotEqual(result.captured_at, result.recognized_at)

    def test_generation_is_recorded_in_warnings(self):
        packet = frame_from_fixture(stream_epoch=4, layout_version=99)
        result = recognize_frame(packet, synthetic_live_style(),
                                 templates_dir=default_templates_dir())
        joined = " ".join(result.warnings)
        self.assertIn("stream_epoch=4", joined)
        self.assertIn("layout_version=99", joined)

    def test_repeat_frame_is_flagged_as_not_new_evidence(self):
        packet = frame_from_fixture(is_repeat=True)
        result = recognize_frame(packet, synthetic_live_style(),
                                 templates_dir=default_templates_dir())
        self.assertTrue(any("不得计为新的独立支持帧" in w for w in result.warnings))

    def test_black_frame_is_flagged(self):
        packet = frame_from_fixture(is_black=True)
        result = recognize_frame(packet, synthetic_live_style(),
                                 templates_dir=default_templates_dir())
        self.assertTrue(any("全黑" in w for w in result.warnings))

    def test_same_frame_twice_yields_same_observation_ids(self):
        """同一帧重复识别不得产生新身份。"""
        style = synthetic_live_style()
        first = recognize_frame(frame_from_fixture(), style,
                                templates_dir=default_templates_dir())
        second = recognize_frame(frame_from_fixture(), style,
                                 templates_dir=default_templates_dir())
        self.assertEqual([o.observation_id for o in first.observations],
                         [o.observation_id for o in second.observations])

    def test_different_frame_id_yields_different_observation_ids(self):
        """不同帧即使画面一样，也是不同的观察实例，由跟踪器去合并。"""
        style = synthetic_live_style()
        first = recognize_frame(frame_from_fixture(frame_id=1), style,
                                templates_dir=default_templates_dir())
        second = recognize_frame(frame_from_fixture(frame_id=2), style,
                                 templates_dir=default_templates_dir())
        self.assertNotEqual([o.observation_id for o in first.observations],
                            [o.observation_id for o in second.observations])


if __name__ == "__main__":
    unittest.main()
