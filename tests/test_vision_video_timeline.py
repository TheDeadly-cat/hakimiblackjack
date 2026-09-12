"""Synthetic temporal-contract fixtures; these are not human video results."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackjack_lab.vision.contracts import (CardObservation, ContractError, RankHypothesis,
    RecognitionResult, SOURCE_SYNTHETIC, RECOGNITION_SCHEMA_VERSION)
from blackjack_lab.vision.image_io import LoadedImage, crop_rgb
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.video_timeline_evaluation import evaluate_timeline

SOURCE = "a" * 64


def loaded(index):
    rgb = bytes([index % 255]) * (800 * 100 * 3)
    return LoadedImage(Path(f"{index}.png"), 800, 100, hashlib.sha256(rgb).hexdigest(), rgb, len(rgb), "synthetic")


def obj(pid, x=10, rank="8", **kwargs):
    return {"physical_card_id": pid, "bbox": [x, 10, 20, 30], "rank": rank,
            "region_id": "seat", "label_provenance": "human_reviewed",
            "reviewed_by": "synthetic-contract-reviewer", **kwargs}


def truth_frame(index, objects, **kwargs):
    return {"frame_index": index, "source_rgb_sha256": loaded(index).sha256,
            "round_id": 0, "complete": True, "reviewed_by": "synthetic-contract-reviewer",
            "objects": objects, **kwargs}


def annotation(frames, **kwargs):
    return {"schema": "original-frame-annotations-1", "source_sha256": SOURCE,
            "coordinate_space": "source_frame", "frames": frames, **kwargs}


def result(image, detections):
    observations = []
    for i, (x, rank, region) in enumerate(detections):
        observations.append(CardObservation(str(i), image.sha256, f"crop-{i}",
            dict(x=x, y=10, w=20, h=30), region, "style", "model", "digest",
            RECOGNITION_SCHEMA_VERSION, [RankHypothesis(rank, 1.0, rank)] if rank else [],
            None if rank else "rejected", "shown" if rank else "unreadable", SOURCE_SYNTHETIC))
    return RecognitionResult(image.sha256, str(image.path), "style", "model", "digest", observations=observations)


def run(annotation_data, detections, *, bindings=None, failing=None):
    read_indices, recognized = [], []

    def read(index):
        read_indices.append(index)
        if index == failing:
            raise OSError("synthetic missing source frame")
        return loaded(index)

    def predict(image):
        assert isinstance(image, LoadedImage)
        index = int(image.path.stem)
        recognized.append(index)
        return result(image, detections[index]), (0, 0)

    report = evaluate_timeline(annotation_data, source_sha256=SOURCE, first_frame=0,
        last_frame=len(detections) - 1, read_frame=read, recognize_frame=predict,
        time_ms=lambda index: index * 100, round_bindings=bindings)
    return report, read_indices, recognized


class VideoTimelineTests(unittest.TestCase):
    def test_two_equal_ranks_survive_unannotated_frames_and_short_occlusion(self):
        truth = annotation([truth_frame(0, [obj("a"), obj("b", 130)]),
                            truth_frame(4, [obj("a"), obj("b", 130)])])
        both = [(10, "8", "seat"), (130, "8", "seat")]
        report, indices, predicted = run(truth, [both, both, [], both, both])
        self.assertTrue(report["valid"])
        self.assertEqual(indices, list(range(5)))
        self.assertEqual(predicted, indices)
        self.assertEqual(report["n_annotation_frames"], 2)
        self.assertEqual(report["metrics"]["track_fragmentation"], 0)
        self.assertEqual(report["metrics"]["identity_merge_tracks"], 0)
        self.assertEqual(report["operator_bound_round_counts"]["unbound"]["accepted_track_appearances"], 2)
        self.assertIsNone(report["ledger_duplicate_count"])
        self.assertIsNone(report["round_event_agreement"])
        self.assertIsNone(report["video_per_frame_end_to_end_recall"])

    def test_truth_round_changes_never_drive_tracker_but_operator_bindings_can(self):
        truth = annotation([truth_frame(0, [obj("old")], round_id=1),
                            truth_frame(2, [obj("new")], round_id=2)])
        frames = [[(10, "8", "seat")], [], [(10, "8", "seat")]]
        unbound, _, _ = run(truth, frames)
        self.assertEqual(unbound["metrics"]["identity_merge_tracks"], 1)
        bindings = {"schema": "operator-round-bindings-1", "source_sha256": SOURCE,
            "bindings": [{"frame_index": i, "binding_id": f"operator-{i}",
                "provenance": "operator_confirmed", "confirmed_by": "test operator"} for i in (0, 2)]}
        bound, _, _ = run(truth, frames, bindings=bindings)
        self.assertEqual(bound["metrics"]["identity_merge_tracks"], 0)
        self.assertEqual(len(bound["operator_bound_round_counts"]), 2)
        self.assertFalse(bound["ground_truth_used_by_recognizer_or_tracker"])

    def test_round_bindings_cannot_be_unreviewed_truth_labels(self):
        bindings = {"schema": "operator-round-bindings-1", "source_sha256": SOURCE,
            "bindings": [{"frame_index": 0, "binding_id": "truth-0", "provenance": "automatic"}]}
        with self.assertRaises(ContractError):
            run(annotation([truth_frame(0, [])]), [[]], bindings=bindings)

    def test_fragmentation_and_id_switch_are_measured(self):
        truth = annotation([truth_frame(0, [obj("a")]), truth_frame(1, [obj("a", 400)])])
        report, _, _ = run(truth, [[(10, "8", "seat")], [(400, "8", "seat")]])
        self.assertEqual(report["metrics"]["track_fragmentation"], 1)
        self.assertEqual(report["metrics"]["id_switches"], 1)

    def test_one_physical_card_with_two_corner_boxes_is_not_two_truth_cards(self):
        card = obj("one", boxes=[[10, 10, 20, 30], [130, 10, 20, 30]])
        report, _, _ = run(annotation([truth_frame(0, [card])]),
                           [[(10, "8", "seat"), (130, "8", "seat")]])
        self.assertTrue(report["valid"])
        self.assertEqual(report["n_truth_physical_cards"], 1)
        self.assertEqual(report["metrics"]["track_fragmentation"], 1)

    def test_first_wrong_rank_missed_card_and_wrong_owner_remain_visible(self):
        truth = annotation([truth_frame(0, [obj("a"), obj("never", 400, "Q")]),
                            truth_frame(1, [obj("a")])])
        report, _, _ = run(truth, [[(10, "Q", "other")], [(10, "8", "seat")]])
        self.assertEqual(report["metrics"]["first_comparable_prediction_wrong"], 1)
        self.assertEqual(report["metrics"]["physical_cards_never_detected"], 1)
        self.assertEqual(report["metrics"]["identifiable_cards_never_correct"], 1)
        self.assertEqual(report["metrics"]["ownership_wrong_observations"], 1)

    def test_conflicting_known_rank_or_cross_round_physical_id_is_invalid_truth(self):
        for second in (truth_frame(1, [obj("a", rank="Q")]),
                       truth_frame(1, [obj("a")], round_id=1)):
            with self.subTest(second=second):
                report, _, _ = run(annotation([truth_frame(0, [obj("a")]), second]),
                                   [[(10, "8", "seat")]] * 2)
                self.assertFalse(report["valid"])
                self.assertIsNone(report["metrics"])

    def test_unreadable_to_known_rank_is_not_a_truth_conflict(self):
        truth = annotation([truth_frame(0, [obj("a", rank="unreadable")]), truth_frame(1, [obj("a")])])
        report, _, _ = run(truth, [[(10, None, "seat")], [(10, "8", "seat")]])
        self.assertTrue(report["valid"])
        self.assertEqual(report["metrics"]["identifiable_cards_never_correct"], 0)

    def test_good_corner_cannot_hide_wrong_corner_reject_or_unmatched_acceptance(self):
        truth = annotation([truth_frame(0, [obj("a", boxes=[[10, 10, 20, 30], [130, 10, 20, 30]]),
            obj("b", 300), obj("c", 400, "Q")])])
        report, _, _ = run(truth, [[(10, "8", "seat"), (130, "Q", "seat"),
                                   (300, None, "seat"), (600, "A", "seat")]])
        metrics = report["metrics"]
        self.assertEqual(metrics["sampled_correct_rank_outputs"], 1)
        self.assertEqual(metrics["sampled_wrong_rank_outputs"], 1)
        self.assertEqual(metrics["sampled_rejected_identifiable_targets"], 1)
        self.assertEqual(metrics["sampled_missed_identifiable_targets"], 1)
        self.assertEqual(metrics["unmatched_sampled_accepted_outputs"], 1)
        self.assertEqual(metrics["sampled_all_output_verified_precision"], 1 / 3)

    def test_missing_nonannotated_frame_invalidates_continuous_replay(self):
        truth = annotation([truth_frame(0, [obj("a")]), truth_frame(2, [obj("a")])])
        report, indices, _ = run(truth, [[(10, "8", "seat")]] * 3, failing=1)
        self.assertEqual(indices, [0, 1, 2])
        self.assertFalse(report["valid"])
        self.assertIsNone(report["metrics"])
        self.assertEqual(report["n_truth_physical_cards"], 1)
        self.assertEqual(report["expected_decode_frames"], 3)
        self.assertEqual(report["decoded_frames"], 2)

    def test_nonhuman_truth_or_missing_source_pixels_cannot_be_valid(self):
        for record in (truth_frame(0, [obj("a", label_provenance="assistant_proposed")]),
                       truth_frame(0, [obj("a")], source_rgb_sha256=""),
                       truth_frame(0, [obj("a")], complete=False)):
            with self.subTest(record=record):
                report, _, _ = run(annotation([record]), [[(10, "8", "seat")]])
                self.assertFalse(report["valid"])
                self.assertIsNone(report["metrics"])
                self.assertEqual(report["n_truth_physical_cards"], 1)

    def test_material_roi_hash_verifies_only_roi_and_never_controls_detector(self):
        image = loaded(0)
        digest = hashlib.sha256(crop_rgb(image, 10, 0, 300, 100)).hexdigest()
        record = truth_frame(0, [obj("a", 0)], source_rgb_sha256="", material_rgb_sha256=digest)
        truth = annotation([record], coordinate_space="material_roi", source_roi=[10, 0, 310, 100])
        report, _, _ = run(truth, [[(10, "8", "seat")]])
        self.assertTrue(report["valid"])
        self.assertEqual(report["verified_source_frame_count"], 0)
        self.assertEqual(report["verified_material_roi_frame_count"], 1)
        self.assertEqual(report["verified_frame_scope"], "material_roi")
        self.assertEqual(report["annotation_frames"][0]["verified_frame_scope"], "material_roi")
        self.assertEqual(report["metrics"]["physical_cards_never_detected"], 0)

    def test_whole_frame_hash_mismatch_cannot_fall_back_to_matching_roi(self):
        image = loaded(0)
        digest = hashlib.sha256(crop_rgb(image, 0, 0, 300, 100)).hexdigest()
        record = truth_frame(0, [obj("a")], source_rgb_sha256="wrong", material_rgb_sha256=digest)
        report, _, _ = run(annotation([record], coordinate_space="material_roi", source_roi=[0, 0, 300, 100]),
                           [[(10, "8", "seat")]])
        self.assertFalse(report["valid"])

    def test_material_truth_outside_verified_roi_is_invalid_even_inside_source(self):
        image = loaded(0)
        digest = hashlib.sha256(crop_rgb(image, 0, 0, 100, 100)).hexdigest()
        record = truth_frame(0, [obj("outside", 300)], source_rgb_sha256="", material_rgb_sha256=digest)
        report, _, _ = run(annotation([record], coordinate_space="material_roi", source_roi=[0, 0, 100, 100]),
                           [[(300, "8", "seat")]])
        self.assertFalse(report["valid"])
        self.assertIsNone(report["metrics"])
        self.assertTrue(any("verified material ROI" in error for error in report["incomplete_reasons"]))

    def test_automatic_round_groups_are_only_declared_group_counts(self):
        truth = annotation([truth_frame(0, [obj("a")], round_provenance="automatic_white_pixel_segmentation")])
        report, _, _ = run(truth, [[(10, "8", "seat")]])
        self.assertEqual(report["annotation_round_group_counts"]["0"]["provenance"],
                         ["automatic_white_pixel_segmentation"])
        self.assertIsNone(report["round_event_agreement"])

    def test_cli_preserves_existing_report_before_opening_video(self):
        spec = importlib.util.spec_from_file_location("timeline_cli",
            Path(__file__).resolve().parents[1] / "scripts/evaluate_video_timeline.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "existing.json"
            output.write_text("original", encoding="utf-8")
            with self.assertRaises(ContractError):
                script.main(["missing.avi", "missing.json", "--model", "missing", "--layout", "missing",
                    "--style-id", "style", "--first-frame", "0", "--last-frame", "2", "--output", str(output)])
            self.assertEqual(output.read_text(encoding="utf-8"), "original")

    @unittest.skipUnless(cv2_available(), "vision dependencies unavailable")
    def test_actual_video_reader_model_pipeline_and_cli_wiring_on_synthetic_clip(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        from blackjack_lab.vision.rank_classifier import RankClassifier
        from blackjack_lab.vision.real_cards import extract_glyphs
        from blackjack_lab.vision.video_io import VideoReader
        from tests.test_vision_model_adapter import scene
        cv2, np = load_cv2(), load_numpy()
        spec = importlib.util.spec_from_file_location("timeline_video_cli",
            Path(__file__).resolve().parents[1] / "scripts/evaluate_video_timeline.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            bgr, _ = scene()
            video = root / "synthetic.avi"
            writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5.0, (240, 130))
            if not writer.isOpened():
                self.skipTest("Synthetic MJPG writer is unavailable")
            try:
                for _ in range(3):
                    writer.write(bgr)
            finally:
                writer.release()
            glyph = extract_glyphs(bgr)[0]
            junk = np.zeros((24, 24), dtype=np.uint8)
            junk[9:15, :] = 255
            model = root / "model"
            RankClassifier(k=1, min_vote=1, min_margin=0.01, min_similarity=0.9).fit(
                [(glyph.mask, "8"), (junk, "junk")], angles=(0,), style_id="test-style",
                label_review_status="synthetic").save(model)
            style = root / "style.json"
            style.write_text(json.dumps({"normalized": True, "style_id": "test-style", "felt_kind": "navy",
                "regions": {"seat": {"x": 0, "y": 0, "w": 1, "h": 1}}}), encoding="utf-8")
            with VideoReader(video) as reader:
                truth = annotation([], truth_provenance="synthetic_contract_fixture")
                truth["source_sha256"] = reader.asset.sha256
                for index in (0, 2):
                    image = reader.seek(index)
                    truth["frames"].append(truth_frame(index, [
                        obj("left", bbox=[23, 25, 25, 30]), obj("right", bbox=[143, 25, 25, 30])],
                        source_rgb_sha256=image.sha256))
            annotation_path = root / "truth.json"
            annotation_path.write_text(json.dumps(truth), encoding="utf-8")
            output = root / "report.json"
            rc = script.main([str(video), str(annotation_path), "--model", str(model), "--layout", str(style),
                "--style-id", "test-style", "--first-frame", "0", "--last-frame", "2", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rc, 0)
            self.assertEqual(report["decoded_frames"], 3)
            self.assertEqual(report["recognized_frames"], 3)
            self.assertEqual(report["n_annotation_frames"], 2)
            self.assertEqual(report["verified_source_frame_count"], 2)
            self.assertEqual(report["prediction_entry"], "VideoReader -> pipeline.recognize_loaded -> FrameTracker")
            self.assertFalse(report["final_acceptance_eligible"])
            self.assertIsNone(report["ledger_duplicate_count"])


if __name__ == "__main__":
    unittest.main()
