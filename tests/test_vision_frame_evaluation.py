"""Original-frame metrics must expose misses, false outputs and evidence gaps."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.vision.contracts import default_layout
from blackjack_lab.vision.frame_evaluation import evaluate_annotated_frames
from blackjack_lab.vision.image_io import LoadedImage
from blackjack_lab.vision.live_input import LiveStyle, NormalizedBox


def obj(rank, x=0, pid="one", **kwargs):
    return {"rank": rank, "bbox": [x, 0, 10, 20], "physical_card_id": pid,
            "label_provenance": "human_reviewed", "reviewed_by": "synthetic-test-reviewer", **kwargs}


def frame(objects, **kwargs):
    return {"file": "f.png", "sha256": "frame", "complete": True,
            "reviewed_by": "synthetic-test-reviewer", "objects": objects, **kwargs}


def annotation(frames):
    return {"schema": "original-frame-annotations-1", "source_sha256": "source",
            "selection": {"method": "synthetic_contract_fixture"}, "frames": frames}


def candidate(rank, x=0, **kwargs):
    return {"rank": rank, "bbox": [x, 0, 10, 20], **kwargs}


class OriginalFrameEvaluationTests(unittest.TestCase):
    def test_missing_detection_and_all_false_outputs_enter_denominators(self):
        truth = [obj("Q"), obj("A", 20, "two"), obj("K", 40, "three"), obj("J", 60, "four"),
                 obj("junk", 80, "junk")]
        outputs = [candidate("Q"), candidate("8", 20), candidate(None, 40), candidate("K", 80),
                   candidate("Q"), candidate("3", 100)]
        report = evaluate_annotated_frames(annotation([frame(truth)]), lambda _: outputs)
        self.assertTrue(report["valid"])
        self.assertEqual(report["correct"], 1)
        self.assertEqual(report["wrong"], 1)
        self.assertEqual(report["rejected"], 1)
        self.assertEqual(report["missed_extraction"], 1)
        self.assertEqual(report["junk_as_rank"], 1)
        self.assertEqual(report["duplicate_accepted"], 1)
        self.assertEqual(report["unmatched_accepted"], 1)
        self.assertEqual(report["all_output_precision"], 1 / 5)
        self.assertEqual(report["sampled_frame_end_to_end_recall"], 1 / 4)
        self.assertEqual(report["extraction_recall"], 3 / 4)
        self.assertIsNone(report["round_event_agreement"])
        self.assertIsNone(report["ledger_duplicate_count"])
        self.assertFalse(report["final_acceptance_eligible"])

    def test_geometry_matching_never_selects_truth_rank_to_make_output_correct(self):
        truth = [obj("A"), obj("Q", 20, "two")]
        report = evaluate_annotated_frames(annotation([frame(truth)]),
                                           lambda _: [candidate("Q"), candidate("A", 20)])
        self.assertEqual(report["correct"], 0)
        self.assertEqual(report["wrong"], 2)

    def test_two_same_rank_cards_remain_two_physical_objects(self):
        truth = [obj("8"), obj("8", 20, "two")]
        report = evaluate_annotated_frames(annotation([frame(truth)]),
                                           lambda _: [candidate("8"), candidate("8", 20)])
        self.assertEqual(report["correct"], 2)
        self.assertEqual(report["n_annotated_physical_cards"], 2)
        self.assertEqual(report["duplicate_candidate"], 0)

    def test_failed_frame_is_invalid_not_a_silent_denominator_drop(self):
        def fail(_):
            raise FileNotFoundError("original missing")
        report = evaluate_annotated_frames(annotation([frame([obj("Q"), obj("junk", 20)])]), fail)
        self.assertFalse(report["valid"])
        self.assertEqual(report["n_truth_identifiable"], 1)
        self.assertEqual(report["n_truth_junk"], 1)
        self.assertEqual(report["n_invalid_identifiable"], 1)
        self.assertEqual(report["per_rank"]["Q"]["invalid"], 1)
        self.assertEqual(report["missed_extraction"], 0)
        self.assertIsNone(report["sampled_frame_end_to_end_recall"])

    def test_unreviewed_or_partial_frame_never_publishes_acceptance_metrics(self):
        for record in (frame([obj("Q")], complete=False),
                       frame([obj("Q", label_provenance="assistant_proposed")]),
                       frame([obj("Q")], reviewed_by="")):
            with self.subTest(record=record):
                report = evaluate_annotated_frames(annotation([record]), lambda _: [candidate("Q")])
                self.assertFalse(report["valid"])
                self.assertEqual(report["n_truth_identifiable"], 1)
                self.assertEqual(report["correct"], 1)
                self.assertIsNone(report["all_output_precision"])

    def test_source_decode_errors_or_truncation_invalidate_otherwise_complete_frames(self):
        for manifest in ({"valid": False}, {"decode_errors": [3]}, {"truncated": True}):
            report = evaluate_annotated_frames(annotation([frame([obj("Q")])]),
                lambda _: [candidate("Q")], source_manifest=manifest)
            self.assertFalse(report["valid"])
            self.assertIsNone(report["sampled_frame_end_to_end_recall"])

    def test_missing_objects_is_unknown_truth_not_a_verified_empty_table(self):
        record = frame([])
        del record["objects"]
        for raw in (record, {**record, "objects": None}):
            report = evaluate_annotated_frames(annotation([raw]), lambda _: [candidate("Q")])
            self.assertFalse(report["valid"])
            self.assertIn("missing_or_invalid_objects", report["incomplete_reasons"])
            self.assertIsNone(report["all_output_precision"])

    def test_annotation_source_incompleteness_survives_new_manifest(self):
        truth = annotation([frame([obj("Q")])])
        truth["source_complete"] = False
        report = evaluate_annotated_frames(truth, lambda _: [candidate("Q")], source_manifest={"valid": True})
        self.assertFalse(report["valid"])
        self.assertIn("source_incomplete_or_truncated", report["incomplete_reasons"])
        self.assertIsNone(report["sampled_frame_end_to_end_recall"])

    def test_unreadable_truth_is_distinct_from_junk_and_rank(self):
        report = evaluate_annotated_frames(annotation([frame([obj("unreadable")])]),
                                           lambda _: [candidate("Q")])
        self.assertEqual(report["n_truth_identifiable"], 0)
        self.assertEqual(report["n_truth_unreadable"], 1)
        self.assertEqual(report["junk_as_rank"], 0)
        self.assertEqual(report["unreadable_as_rank"], 1)
        self.assertEqual(report["all_output_precision"], 0)

    def test_duplicate_truth_physical_id_invalidates_annotation(self):
        report = evaluate_annotated_frames(annotation([frame([obj("Q"), obj("Q", 20)])]),
                                           lambda _: [candidate("Q"), candidate("Q", 20)])
        self.assertFalse(report["valid"])
        self.assertEqual(report["n_truth_identifiable"], 2)

    def test_ownership_error_is_reported_separately_from_correct_rank(self):
        report = evaluate_annotated_frames(annotation([frame([obj("Q", region_id="seat1")])]),
                                           lambda _: [candidate("Q", region_id="seat2")])
        self.assertEqual(report["correct"], 1)
        self.assertEqual(report["ownership_wrong"], 1)

    def test_pipeline_wrapper_uses_original_frame_and_maps_cropped_coordinates(self):
        spec = importlib.util.spec_from_file_location("original_frames_cli",
            Path(__file__).resolve().parents[1] / "scripts/evaluate_original_frames.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        layout = replace(default_layout(), source_frame_width=100, source_frame_height=80,
                         source_crop=(10, 10, 80, 60), felt_crop=(5, 5, 60, 40),
                         canvas_width=60, canvas_height=40)
        self.assertEqual(script.crop_offset(100, 80, layout), (15, 15))
        loaded = LoadedImage(Path("f.png"), 100, 80, "frame", bytes(100 * 80 * 3), 1, "PNG")

        class Observation:
            bbox = dict(x=2, y=3, w=10, h=20)
            observation_id, region_id, reject_reason, model_id, model_digest = "obs", "seat", None, "model", "digest"
            def accepted_rank(self):
                return "Q"

        class Result:
            observations = [Observation()]

        adapter = object()
        with tempfile.TemporaryDirectory() as tmp, patch.object(script, "load_image", return_value=loaded), \
                patch.object(script, "recognize_loaded", return_value=Result()) as recognize:
            predictor = script.make_predictor(tmp, {}, {"frames": [{"file": "f.png"}]}, layout, adapter)
            outputs = predictor(frame([obj("Q")]))
            recognize.assert_called_once_with(loaded, layout=layout, adapter=adapter)
        self.assertEqual(outputs[0]["bbox"], dict(x=17, y=18, w=10, h=20))
        self.assertEqual(outputs[0]["rank"], "Q")

    def test_normalized_capture_style_uses_shared_crop_and_original_coordinates(self):
        spec = importlib.util.spec_from_file_location("normalized_original_cli",
            Path(__file__).resolve().parents[1] / "scripts/evaluate_original_frames.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        style = LiveStyle("test-style", {"all": NormalizedBox(0, 0, 1, 1)},
                          capture_crop=NormalizedBox(0.1, 0.25, 0.6, 0.5))
        loaded = LoadedImage(Path("f.png"), 100, 80, "frame", bytes(100 * 80 * 3), 1, "PNG")

        class Observation:
            bbox = dict(x=2, y=3, w=10, h=20)
            observation_id, region_id, reject_reason, model_id, model_digest = "obs", "all", None, "model", "digest"
            def accepted_rank(self):
                return "Q"

        class Result:
            observations = [Observation()]

        with tempfile.TemporaryDirectory() as tmp, patch.object(script, "load_image", return_value=loaded), \
                patch.object(script, "recognize_loaded", return_value=Result()) as recognize:
            path = Path(tmp) / "style.json"
            path.write_text(json.dumps(style.as_dict()), encoding="utf-8")
            selected = script.load_style(path)
            predictor = script.make_predictor(tmp, {}, {"frames": [{"file": "f.png"}]}, selected, object())
            outputs = predictor(frame([obj("Q")]))
            canvas = recognize.call_args.args[0]
            resolved = recognize.call_args.kwargs["layout"]
            self.assertEqual((canvas.width, canvas.height), (60, 40))
            self.assertEqual((resolved.canvas_width, resolved.canvas_height), (60, 40))
        self.assertEqual(outputs[0]["bbox"], dict(x=12, y=23, w=10, h=20))


if __name__ == "__main__":
    unittest.main()
