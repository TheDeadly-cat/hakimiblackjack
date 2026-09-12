# -*- coding: utf-8 -*-
"""独立识别器：13 点数、双 8、空区、牌背、拒识；不写账本。"""
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.vision.contracts import FACE_BACK, FACE_UNREADABLE, RANKS_13, REVIEW_PENDING
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.holdout import write_bundle
from blackjack_lab.vision.pipeline import recognize_path
from blackjack_lab.vision.synthetic import (
    PlacedCard, UNSUPPORTED_FELT, render_table, smoke_all13_cards, smoke_two_eights,
)

HAVE_CV2 = cv2_available()


@unittest.skipUnless(HAVE_CV2, "未安装识牌依赖，跳过识别器测试")
class TestVisionPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.bundle = Path(cls.tmp.name)
        write_bundle(cls.bundle)
        cls.templates = cls.bundle / "templates"

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _recognize_cards(self, cards, name="scene.png"):
        canvas, labels = render_table(cards)
        path = self.bundle / name
        canvas.save_png(path)
        result = recognize_path(path, templates_dir=self.templates)
        return result, labels

    def test_all_thirteen_raw_ranks(self):
        result, labels = self._recognize_cards(smoke_all13_cards(), "all13.png")
        self.assertEqual(result.review_status, REVIEW_PENDING)
        accepted = [o.accepted_rank() for o in result.observations if o.accepted_rank()]
        self.assertEqual(sorted(accepted, key=lambda r: RANKS_13.index(r)), list(RANKS_13))
        for obs in result.observations:
            self.assertTrue(all(c.rank != "T" for c in obs.rank_candidates))
            self.assertNotEqual(obs.accepted_rank(), "T")
        self.assertFalse(result.as_dict()["writes_ledger"])

    def test_two_eights_are_two_observations(self):
        result, _ = self._recognize_cards(smoke_two_eights(), "two8.png")
        eights = [o for o in result.observations if o.accepted_rank() == "8"]
        self.assertEqual(len(eights), 2)
        self.assertNotEqual(eights[0].observation_id, eights[1].observation_id)
        self.assertNotEqual(eights[0].bbox, eights[1].bbox)
        self.assertIsNone(eights[0].as_dict()["track_id"])

    def test_empty_table_does_not_invent_cards(self):
        result, _ = self._recognize_cards([], "empty.png")
        self.assertEqual(result.observations, [])
        self.assertGreaterEqual(len(result.empty_regions), 1)

    def test_card_back_is_not_a_rank(self):
        result, _ = self._recognize_cards(
            [PlacedCard(None, 120, 50, "dealer", face="back")], "back.png")
        self.assertTrue(result.observations)
        self.assertTrue(all(o.face_state_candidate == FACE_BACK or o.accepted_rank() is None
                            for o in result.observations))
        self.assertTrue(all(o.accepted_rank() is None for o in result.observations
                            if o.face_state_candidate == FACE_BACK))

    def test_occluded_index_rejected(self):
        result, _ = self._recognize_cards(
            [PlacedCard("Q", 200, 400, "player_target", occlude_index=True)], "occ.png")
        self.assertTrue(result.observations)
        for obs in result.observations:
            self.assertIsNone(obs.accepted_rank())
            self.assertEqual(obs.face_state_candidate, FACE_UNREADABLE)

    def test_ten_face_cards_not_merged(self):
        layout_player = "player_target"
        cards = [
            PlacedCard("10", 120, 400, layout_player),
            PlacedCard("J", 230, 400, layout_player),
            PlacedCard("Q", 340, 400, layout_player),
            PlacedCard("K", 450, 400, layout_player),
        ]
        result, _ = self._recognize_cards(cards, "tens.png")
        accepted = [o.accepted_rank() for o in result.observations if o.accepted_rank()]
        self.assertCountEqual(accepted, ["10", "J", "Q", "K"])

    def test_same_image_is_idempotent_observation_ids(self):
        cards = smoke_two_eights()
        first, _ = self._recognize_cards(cards, "idemp.png")
        second = recognize_path(self.bundle / "idemp.png", templates_dir=self.templates)
        self.assertEqual(
            [o.observation_id for o in first.observations],
            [o.observation_id for o in second.observations],
        )

    def test_does_not_create_user_database(self):
        before = list(self.bundle.glob("*.db"))
        self._recognize_cards(smoke_all13_cards(), "nodb.png")
        after = list(self.bundle.glob("*.db"))
        self.assertEqual(before, after)

    def test_unsupported_felt_does_not_accept_rank(self):
        cards = [PlacedCard("A", 120, 50, "dealer")]
        canvas, _labels = render_table(cards, felt=UNSUPPORTED_FELT)
        path = self.bundle / "badfelt.png"
        canvas.save_png(path)
        result = recognize_path(path, templates_dir=self.templates)
        self.assertTrue(all(obs.accepted_rank() is None for obs in result.observations))
        self.assertEqual(result.reject_reason, "unsupported_or_uncertain_style")


if __name__ == "__main__":
    unittest.main()
