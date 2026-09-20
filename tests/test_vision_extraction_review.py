"""Extraction mechanisms on synthetic shapes; no real-card accuracy assertions."""
from __future__ import annotations

import unittest

from blackjack_lab.vision.deps import cv2_available


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class ExtractionReviewTests(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        self.cv2, self.np = load_cv2(), load_numpy()

    def card(self):
        image = self.np.full((140, 180, 3), (100, 40, 20), dtype=self.np.uint8)
        image[20:120, 20:160] = 245
        return image

    def test_small_card_edge_opening_restores_ink_without_closing_glyph_hole(self):
        from blackjack_lab.vision.real_cards import _glyph_components, card_body_mask
        image = self.card()
        image[35:57, 27:65] = 0
        image[40:51, 35:55] = 245
        image[43:45, 20:28] = 0  # Ink touches the background through a two-pixel gap.
        self.assertEqual(list(_glyph_components(image, close_border=False)), [])
        glyphs = [(g, r) for g, r in _glyph_components(image) if not r]
        self.assertEqual(len(glyphs), 1)
        glyph = glyphs[0][0]
        self.assertGreater(glyph.area, 500)
        self.assertTrue((glyph.mask == 0).any(), "Character holes must remain in the feature mask")
        original_white, _ = card_body_mask(image, close_border=False)
        closed_outline_white, _ = card_body_mask(image)
        self.np.testing.assert_array_equal(original_white, closed_outline_white)

    def test_perspective_wide_q_and_a_shapes_remain_candidates(self):
        from blackjack_lab.vision.real_cards import extract_glyphs, extraction_diagnostics
        image = self.card()
        # A wide hollow shape and a wide peaked shape stand in for perspective Q/A.
        image[30:52, 30:72] = 0
        image[35:47, 38:64] = 245
        self.cv2.line(image, (55, 43), (72, 53), (0, 0, 0), 3)
        self.cv2.line(image, (100, 90), (120, 70), (0, 0, 220), 4)
        self.cv2.line(image, (120, 70), (140, 90), (0, 0, 220), 4)
        self.cv2.line(image, (108, 83), (132, 83), (0, 0, 220), 4)
        glyphs = extract_glyphs(image)
        self.assertEqual(len(glyphs), 2)
        self.assertEqual({g.ink for g in glyphs}, {"red", "black"})
        rows = [r for r in extraction_diagnostics(image) if r["accepted"]]
        self.assertTrue(all(r["legacy_aspect_rejected"] for r in rows))

    def test_forty_eight_pixel_glyph_kept_but_large_joined_block_still_rejected(self):
        from blackjack_lab.vision.real_cards import _glyph_components
        image = self.card()
        image[30:52, 30:78] = 0
        image[35:47, 38:69] = 245
        image[75:100, 35:117] = 0
        image[80:95, 45:106] = 245
        components = list(_glyph_components(image))
        narrow = [(g, r) for g, r in components if g.width == 48]
        broad = [(g, r) for g, r in components if g.width == 82]
        self.assertEqual(len(narrow), 1)
        self.assertEqual(narrow[0][1], [])
        self.assertEqual(len(broad), 1)
        self.assertIn("size", broad[0][1])

    def test_tiny_outline_closing_does_not_turn_isolated_print_blocks_into_card_body(self):
        from blackjack_lab.vision.real_cards import extract_glyphs
        image = self.np.full((140, 180, 3), (100, 40, 20), dtype=self.np.uint8)
        for x in (10, 45, 80, 115):
            image[30:55, x:x+25] = 245
            image[37:48, x+7:x+18] = 0
        self.assertEqual(extract_glyphs(image), [])


if __name__ == "__main__":
    unittest.main()
