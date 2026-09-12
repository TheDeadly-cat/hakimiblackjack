# -*- coding: utf-8 -*-
"""识牌候选契约：非法分数、T 桶、R1 待核对。"""
import json
import unittest
from pathlib import Path

from blackjack_lab.core.cards import RANKS
from blackjack_lab.vision.contracts import (
    FACE_SHOWN, RECOGNITION_SCHEMA_VERSION, RANKS_13, REVIEW_PENDING,
    CardObservation, ContractError, RankHypothesis, RecognitionResult,
    default_layout, result_from_dict, validate_match_score, validate_observation,
    validate_rank, validate_result,
)


class TestRankContract(unittest.TestCase):
    def test_thirteen_raw_ranks_match_core(self):
        self.assertEqual(tuple(RANKS), RANKS_13)
        self.assertNotIn("T", RANKS_13)

    def test_t_bucket_forbidden(self):
        with self.assertRaises(ContractError):
            validate_rank("T")

    def test_score_bounds(self):
        self.assertEqual(validate_match_score(0.91), 0.91)
        with self.assertRaises(ContractError):
            validate_match_score(1.2)
        with self.assertRaises(ContractError):
            validate_match_score(float("nan"))

    def test_example_json_validates(self):
        path = Path(__file__).resolve().parents[1] / "blackjack_lab" / "vision" / "styles" / "example_candidate.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        result = result_from_dict(data)
        self.assertEqual(result.review_status, REVIEW_PENDING)
        self.assertEqual(len(result.observations), 2)
        self.assertEqual(result.observations[0].accepted_rank(), "8")
        self.assertNotEqual(result.observations[0].observation_id,
                            result.observations[1].observation_id)

    def test_layout_frozen_limits(self):
        layout = default_layout()
        self.assertEqual(layout.layout_profile_id, "synthetic-felt-v1")
        self.assertEqual(layout.platform_claim, "none")
        self.assertEqual(layout.max_image_bytes, 20 * 1024 * 1024)
        self.assertEqual(layout.queue_length, 1)
        self.assertEqual(layout.canvas_width, 960)

    def test_pending_status_required(self):
        obs = CardObservation(
            observation_id="a" * 32, asset_sha256="b" * 64, crop_sha256="c" * 64,
            bbox={"x": 1, "y": 1, "w": 10, "h": 10}, region_id="dealer",
            layout_profile_id="synthetic-felt-v1", model_id="m", model_digest="d",
            recognition_schema_version=RECOGNITION_SCHEMA_VERSION,
            rank_candidates=[RankHypothesis("8", 0.9, "8")],
            reject_reason=None, face_state_candidate=FACE_SHOWN,
            source_declaration="自建合成样式",
        )
        result = RecognitionResult(
            asset_sha256="b" * 64, image_path="x.png",
            layout_profile_id="synthetic-felt-v1", model_id="m", model_digest="d",
            observations=[obs],
        )
        validate_result(result)
        result.review_status = "已入账"
        with self.assertRaises(ContractError):
            validate_result(result)

    def test_r3_holdout_plan_meets_closed_set_counts(self):
        from blackjack_lab.vision.holdout import (
            R3_IDENTIFIABLE_FRAMES, R3_INTERFERENCE, R3_PRIMARY_PER_RANK,
        )
        self.assertGreaterEqual(R3_PRIMARY_PER_RANK, 50)
        self.assertGreaterEqual(R3_IDENTIFIABLE_FRAMES, 13 * 50)
        self.assertGreaterEqual(R3_INTERFERENCE, 200)


if __name__ == "__main__":
    unittest.main()
