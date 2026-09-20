# -*- coding: utf-8 -*-
"""旁观深色桌：裁切、座位、检牌；不依赖真实平台原片入库。"""
import tempfile
import unittest
from blackjack_lab.vision.deps import cv2_available
from pathlib import Path

from blackjack_lab.vision.contracts import navy_layout
from blackjack_lab.vision.detector import detect_all_regions
from blackjack_lab.vision.image_io import LoadedImage, sha256_bytes
from blackjack_lab.vision.pipeline import infer_layout, recognize_loaded
from blackjack_lab.vision.synthetic import RgbCanvas
from blackjack_lab.vision.table_crop import apply_layout_crops


NAVY = (18, 28, 48)


def _loaded_from_canvas(canvas: RgbCanvas, path: Path) -> LoadedImage:
    canvas.save_png(path)
    rgb = bytes(canvas.buf)
    return LoadedImage(path, canvas.width, canvas.height, sha256_bytes(rgb), rgb, len(rgb), "png")


class TestNavyLayout(unittest.TestCase):
    def test_profile_has_seven_seats_and_dealer(self):
        layout = navy_layout()
        self.assertEqual(layout.style_id, "navy-live-felt-v1")
        self.assertEqual(layout.felt_kind, "navy")
        self.assertEqual(layout.platform_claim, "none")
        self.assertEqual(layout.source_frame_width, 2560)
        self.assertIn("dealer", layout.regions)
        for i in range(1, 8):
            self.assertIn(f"seat_{i}", layout.regions)

    def test_full_desktop_is_cropped_to_felt(self):
        layout = navy_layout()
        canvas = RgbCanvas(2560, 1440, (200, 140, 80))
        x, y, w, h = layout.source_crop
        fx, fy, fw, fh = layout.felt_crop
        canvas.fill_rect(x, y, w, h, (30, 40, 70))
        canvas.fill_rect(x + fx, y + fy, fw, fh, NAVY)
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded_from_canvas(canvas, Path(tmp) / "desk.png")
            felt = apply_layout_crops(loaded, layout)
            self.assertEqual((felt.width, felt.height), (1180, 282))
            self.assertEqual(infer_layout(loaded).style_id, "navy-live-felt-v1")


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class TestNavyDetect(unittest.TestCase):
    def test_cards_go_to_dealer_and_player_seats(self):
        layout = navy_layout()
        canvas = RgbCanvas(1180, 282, NAVY)
        canvas.fill_rect(520, 70, 24, 36, (248, 248, 242))
        canvas.fill_rect(430, 175, 24, 36, (248, 248, 242))
        canvas.fill_rect(700, 175, 52, 36, (248, 248, 242))
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded_from_canvas(canvas, Path(tmp) / "felt.png")
            detected = detect_all_regions(loaded, layout)
            self.assertTrue(detected["style_ok"])
            self.assertGreaterEqual(len(detected["boxes"]["dealer"]), 1)
            player_boxes = sum(len(detected["boxes"][f"seat_{i}"]) for i in range(1, 8))
            self.assertGreaterEqual(player_boxes, 2)

    def test_green_felt_is_not_navy(self):
        layout = navy_layout()
        canvas = RgbCanvas(1180, 282, (18, 92, 48))
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded_from_canvas(canvas, Path(tmp) / "green.png")
            detected = detect_all_regions(loaded, layout)
            self.assertFalse(detected["style_ok"])

    def test_recognize_does_not_auto_accept_without_templates(self):
        layout = navy_layout()
        canvas = RgbCanvas(1180, 282, NAVY)
        canvas.fill_rect(520, 70, 24, 36, (248, 248, 242))
        with tempfile.TemporaryDirectory() as tmp:
            loaded = _loaded_from_canvas(canvas, Path(tmp) / "one.png")
            result = recognize_loaded(loaded, layout=layout)
            self.assertEqual(result.layout_profile_id, "navy-live-felt-v1")
            self.assertTrue(result.observations)
            self.assertIsNone(result.observations[0].accepted_rank())
            self.assertTrue(
                result.observations[0].reject_reason
                and ("人工确认" in result.observations[0].reject_reason
                     or "印刷" in result.observations[0].reject_reason)
            )

    def test_corner_ink_ranks_an_ace(self):
        from blackjack_lab.vision.navy_cards import recognize_navy_face
        import numpy as np
        import cv2
        card = np.full((72, 48, 3), 248, dtype=np.uint8)
        cv2.putText(card, "A", (4, 38), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (20, 20, 20), 2)
        out = recognize_navy_face(card)
        self.assertTrue(out["rank_candidates"])
        self.assertEqual(out["rank_candidates"][0].rank, "A")


if __name__ == "__main__":
    unittest.main()
