"""Session identity, conservative grouping and non-destructive training contracts."""
from __future__ import annotations

import importlib.util
import contextlib
import io
import json
import tempfile
import unittest
from argparse import Namespace
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.glyph_dataset import (
    GlyphItem, assert_no_leakage, independent_groups, load_queue, sample_origin_id, save_queue,
)

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("train_rank_review", ROOT / "scripts/train_rank_classifier.py")
SCRIPT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCRIPT)


def item(name, session="a", rid=0, split="train", **kwargs):
    return GlyphItem(name, session, f"{name}.jpg", "", rid, split,
                     (0, 0, 8, 8), "black", "", "", label="A", **kwargs)


class DatasetIdentityTests(unittest.TestCase):
    def test_independent_round_zero_and_same_bare_frame_name_are_legal(self):
        a = item("a", "first", split="train")
        b = replace(item("b", "second", split="holdout"), frame=a.frame)
        assert_no_leakage([a, b])

    def test_same_session_round_and_unsigned_frame_remain_blocked(self):
        for b in (item("b", rid=0, split="holdout"),
                  replace(item("b", rid=1, split="holdout"), frame="a.jpg")):
            with self.subTest(b=b), self.assertRaises(ContractError):
                assert_no_leakage([item("a"), b])

    def test_renamed_source_duplicate_frame_and_crop_content_remain_blocked(self):
        for field, value in (("frame_sha256", "f" * 64), ("frame_signature", "signature"),
                             ("crop_sha256", "c" * 64), ("mask_sha256", "m" * 64),
                             ("source_sha256", "s" * 64),
                             ("origin_crop_id", "original")):
            a = item("a", "first")
            b = item("b", "renamed", split="holdout")
            setattr(a, field, value)
            setattr(b, field, value)
            with self.subTest(field=field), self.assertRaises(ContractError):
                assert_no_leakage([a, b])

    def test_same_source_disjoint_rounds_are_allowed_for_development(self):
        a = item("a", rid=0, source_sha256="s" * 64)
        b = item("b", rid=1, split="holdout", source_sha256="s" * 64)
        assert_no_leakage([a, b])
        self.assertFalse(SCRIPT._final_source_status([a], [], [b])["source_independent"])

    def test_physical_card_group_does_not_merge_two_equal_ranks(self):
        a = item("a", physical_card_id="one")
        b = item("b", rid=1, split="validation", physical_card_id="one")
        with self.assertRaises(ContractError):
            assert_no_leakage([a, b])
        b.physical_card_id = "two"
        assert_no_leakage([a, b])
        self.assertNotEqual(sample_origin_id(a), sample_origin_id(b))

    def test_transitive_physical_card_and_original_crop_stay_in_one_group(self):
        items = [item("a", physical_card_id="one"),
                 item("b", rid=1, physical_card_id="one", origin_crop_id="origin"),
                 item("c", rid=2, origin_crop_id="origin"), item("d", rid=3)]
        self.assertEqual(independent_groups(items), [[0, 1, 2], [3]])

    def test_new_evidence_roundtrips_without_promoting_legacy_labels(self):
        a = item("a", source_sha256="s" * 64, frame_sha256="f" * 64,
                 physical_card_id="seat1-0", origin_crop_id="original",
                 extraction_method="manual_bbox", rejection_reason="touching_suit", orientation_deg=15)
        with tempfile.TemporaryDirectory() as tmp:
            save_queue([a], tmp)
            b = load_queue(tmp)[0]
        self.assertEqual(a, b)
        self.assertEqual(b.label_provenance, "unspecified")


class TrainingContractTests(unittest.TestCase):
    def test_renamed_copies_of_training_mask_cannot_cross_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            for directory, name in ((a, "a"), (b, "b")):
                save_queue([replace(item(name, name), mask_file="mask.png")], directory)
                (directory / "mask.png").write_bytes(b"identical mask file")
            args = Namespace(queue=str(a), train_queue=[], validation_queue=[], holdout_queue=[str(b)])
            with self.assertRaises(ContractError):
                SCRIPT._load_inputs(args)

    def test_recorded_mask_hash_is_verified_before_training(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_queue([replace(item("a"), mask_file="mask.png", mask_sha256="0" * 64)], root)
            (root / "mask.png").write_bytes(b"modified mask file")
            args = Namespace(queue=str(root), train_queue=[], validation_queue=[], holdout_queue=[])
            with self.assertRaisesRegex(ContractError, "SHA256"):
                SCRIPT._load_inputs(args)

    @unittest.skipUnless(cv2_available(), "vision dependencies unavailable")
    def test_reencoded_mask_copy_cannot_cross_holdout(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            mask = np.zeros((20, 16), dtype=np.uint8)
            mask[4:15, 5:9] = 255
            for directory, name, compression in ((a, "a", 0), (b, "b", 9)):
                save_queue([replace(item(name, name), mask_file="mask.png")], directory)
                cv2.imwrite(str(directory / "mask.png"), mask, [cv2.IMWRITE_PNG_COMPRESSION, compression])
            self.assertNotEqual((a / "mask.png").read_bytes(), (b / "mask.png").read_bytes())
            args = Namespace(queue=str(a), train_queue=[], validation_queue=[], holdout_queue=[str(b)])
            with self.assertRaisesRegex(ContractError, "mask_content_sha256"):
                SCRIPT._load_inputs(args)

    def test_internal_validation_prefers_whole_sessions_not_bare_round_ids(self):
        train = [item(f"{s}-{r}", session=s, rid=r) for s in ("a", "b") for r in range(8)]
        fit, val, info = SCRIPT._validation_split(train)
        self.assertEqual(info["mode"], "whole_session_source_groups")
        self.assertFalse({i.session for i in fit} & {i.session for i in val})
        self.assertTrue({i.round_id for i in fit} & {i.round_id for i in val})

    def test_internal_validation_does_not_split_transitive_identity(self):
        train = [item(str(i), rid=i) for i in range(9)]
        train[0].physical_card_id = train[8].physical_card_id = "same"
        fit, val, _ = SCRIPT._validation_split(train)
        self.assertEqual(train[0] in fit, train[8] in fit)
        self.assertTrue(fit)
        self.assertTrue(val)

    def test_multi_queue_paths_are_resolved_per_queue_and_sources_are_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            save_queue([replace(item("a", "a"), mask_file="same.png")], a)
            save_queue([replace(item("b", "b"), mask_file="same.png")], b)
            before = [(path / "queue.jsonl").read_bytes() for path in (a, b)]
            args = Namespace(queue=str(a), train_queue=[], validation_queue=[], holdout_queue=[str(b)])
            rows, sources = SCRIPT._load_inputs(args)
            self.assertEqual(rows[0].mask_file, str((a / "same.png").resolve()))
            self.assertEqual(rows[1].mask_file, str((b / "same.png").resolve()))
            self.assertEqual([row.split for row in rows], ["train", "holdout"])
            self.assertEqual(before, [(path / "queue.jsonl").read_bytes() for path in (a, b)])
            self.assertEqual(len(sources), 2)

    def test_threshold_selection_penalizes_junk_in_precision(self):
        train = [item(str(i), rid=i) for i in range(8)]
        validation = [item(f"v{i}", "v", split="validation") for i in range(4)]

        class FakeModel:
            def __init__(self, **kwargs):
                self.__dict__.update(kwargs)
            def fit(self, *args, **kwargs):
                return self

        def evaluate(model, rows, root):
            self.assertEqual(rows, validation)
            loose = model.min_similarity < 0.85
            return {"valid": True, "all_output_precision": 0.8 if loose else 1.0,
                    "accepted_correct": 4 if loose else 2, "accepted_wrong": 0,
                    "junk_as_rank": 1 if loose else 0,
                    "extracted_identifiable_recall": 1.0 if loose else 0.5}

        with patch.object(SCRIPT, "RankClassifier", FakeModel), \
                patch.object(SCRIPT, "pairs_from_items", return_value=[]), \
                patch.object(SCRIPT, "evaluate_items", side_effect=evaluate):
            thresholds, audit = SCRIPT._tune_on_train(train, Path(), validation)
        self.assertEqual(thresholds["min_similarity"], 0.85)
        self.assertFalse(audit["holdout_used_for_selection"])
        self.assertEqual(len(audit["trials"]), 32)

    def test_existing_model_and_report_are_preserved_before_input_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            model = Path(tmp) / "old-model"
            model.mkdir()
            sentinel = model / "model.npz"
            sentinel.write_bytes(b"old model")
            report = Path(tmp) / "old-report.json"
            report.write_text("old report", encoding="utf-8")
            result = SCRIPT.main(["missing-queue", "--model", str(model), "--report", str(report)])
            self.assertEqual(result, 2)
            self.assertEqual(sentinel.read_bytes(), b"old model")
            self.assertEqual(report.read_text(encoding="utf-8"), "old report")

    @unittest.skipUnless(cv2_available(), "vision dependencies unavailable")
    def test_missing_holdout_file_keeps_denominator_and_run_is_incomplete(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mask = np.zeros((20, 16), dtype=np.uint8)
            mask[2:18, 4:8] = 255
            cv2.imwrite(str(root / "train.png"), mask)
            train = replace(item("train"), mask_file="train.png")
            missing = replace(item("missing", "independent", split="holdout"), mask_file="missing.png")
            # Empty/corrupt files must not fail in the new identity hash reader
            # before evaluate_items can retain their denominator and invalid row.
            (root / "missing.png").write_bytes(b"")
            save_queue([train, missing], root)
            output = root / "run"
            result = SCRIPT.main([str(root), "--output", str(output)])
            report = json.loads((output / "holdout-report.json").read_text(encoding="utf-8"))
            self.assertEqual(result, 2)
            self.assertEqual(report["holdout"]["n_labeled"], 1)
            self.assertEqual(report["holdout"]["n_identifiable"], 1)
            self.assertEqual(report["holdout"]["n_invalid"], 1)
            self.assertFalse(report["evaluation_complete"])
            self.assertFalse(report["final_source_status"]["final_acceptance_eligible"])

    @unittest.skipUnless(cv2_available(), "vision dependencies unavailable")
    def test_train_only_append_preserves_historical_holdout_truncation(self):
        from blackjack_lab.vision.deps import load_cv2
        from tests.test_vision_model_adapter import scene
        spec = importlib.util.spec_from_file_location("prepare_history", ROOT / "scripts/prepare_glyph_queue.py")
        prepare = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(prepare)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "frames").mkdir()
            bgr, _ = scene()
            frames = []
            for i in range(3):
                pixels = bgr.copy()
                pixels[0, 0, 0] = i
                name = f"f{i}.png"
                load_cv2().imwrite(str(root / "frames" / name), pixels)
                frames.append({"file": name, "elapsed_s": i * 10, "white_delta": -20000,
                               "white_pixels": 100, "signature": f"frame{i}"})
            (root / "manifest.json").write_text(json.dumps({"session": "probe", "frames": frames}))
            out = root / "queue"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(prepare.main([str(root), "--output", str(out), "--max-glyphs", "1"]), 2)
            original_split = (out / "split.json").read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(prepare.main([str(root), "--output", str(out), "--append"]), 2)
            split = json.loads((out / "split.json").read_text(encoding="utf-8"))
            self.assertFalse(split["valid"])
            self.assertTrue(split["prior_truncated_candidates"])
            self.assertEqual(sum(i.split == "holdout" for i in load_queue(out)), 1)
            histories = list((out / "history").rglob("split.json"))
            self.assertTrue(any(path.read_bytes() == original_split for path in histories))


if __name__ == "__main__":
    unittest.main()
