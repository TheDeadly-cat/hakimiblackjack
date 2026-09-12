"""Engineering fixtures for assisted annotation; no real-video accuracy claims."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.review_assisted_batch import ReviewStore
from scripts.prepare_assisted_review import prepare, unchanged_background
from blackjack_lab.vision.deps import cv2_available


class ReviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.annotation = self.root / "review.json"
        self.data = {"schema": "original-frame-annotations-1", "source_sha256": "source-one",
                     "final_acceptance_eligible": False, "frames": [{"complete": False, "objects": [
                         {"rank": "K", "label_provenance": "assistant_proposed", "proposed_rank": "10"},
                         {"rank": "junk", "label_provenance": "assistant_proposed"}]}]}
        self.annotation.write_text(json.dumps(self.data), encoding="utf-8")
        self.bundle = self.root / "bundle.json"
        self.bundle.write_text(json.dumps({"schema": "assisted-review-bundle-1", "sessions": [
            {"annotations": str(self.annotation), "source_sha256": "source-one"}]}), encoding="utf-8")
        self.store = ReviewStore(self.bundle)

    def saved(self):
        return json.loads(self.annotation.read_text(encoding="utf-8"))

    def test_open_never_promotes_or_rewrites_suggestions(self):
        original = self.annotation.read_bytes()
        ReviewStore(self.bundle)
        self.assertEqual(original, self.annotation.read_bytes())
        self.assertEqual(self.saved(), self.data)

    def test_explicit_single_correction_preserves_sibling_and_model_guess(self):
        self.store.confirm(0, 0, "Review fixture", selected=0, rank="Q")
        saved = self.saved()
        self.assertFalse(saved["frames"][0]["complete"])
        first, sibling = saved["frames"][0]["objects"]
        self.assertEqual((first["rank"], first["proposed_rank"]), ("Q", "10"))
        self.assertEqual(first["label_provenance"], "human_reviewed")
        self.assertEqual(sibling["label_provenance"], "assistant_proposed")
        self.assertFalse(saved["final_acceptance_eligible"])

    def test_whole_page_confirmation_includes_noncard_items(self):
        self.store.confirm(0, 0, "Review fixture")
        f = self.saved()["frames"][0]
        self.assertTrue(f["complete"])
        self.assertTrue(all(o["reviewed_by"] == "Review fixture" for o in f["objects"]))

    def test_requires_reviewer_and_valid_rank_before_changes(self):
        for reviewer, rank in [(" ", "Q"), ("Reviewer", "invalid")]:
            with self.assertRaises(ValueError):
                self.store.confirm(0, 0, reviewer, selected=0, rank=rank)
        self.assertEqual(self.saved(), self.data)
        self.assertEqual(self.store.history, [])

    def test_undo_restores_page_and_original_provenance(self):
        self.store.confirm(0, 0, "Review fixture")
        confirmed = self.saved()
        self.store.delete(0, 0, 1)
        self.assertFalse(self.saved()["frames"][0]["complete"])
        self.assertNotIn("reviewed_by", self.saved()["frames"][0])
        self.assertEqual(self.store.undo(), (0, 0))
        self.assertEqual(self.saved(), confirmed)
        self.store.undo()
        self.assertEqual(self.saved(), self.data)

    def test_other_writer_conflict_preserves_disk_and_rolls_back_memory(self):
        external = copy.deepcopy(self.data)
        external["external_change"] = True
        self.annotation.write_text(json.dumps(external), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "另一个程序"):
            self.store.confirm(0, 0, "Review fixture", selected=0, rank="Q")
        self.assertEqual(self.saved(), external)
        self.assertEqual(self.store.documents[0], self.data)
        self.assertEqual(self.store.history, [])

    def test_conflict_during_undo_keeps_history_and_current_memory(self):
        self.store.confirm(0, 0, "Review fixture")
        current = copy.deepcopy(self.store.documents[0])
        self.annotation.write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.store.undo()
        self.assertEqual(self.store.documents[0], current)
        self.assertEqual(len(self.store.history), 1)
        self.assertEqual(self.saved(), {})

    def test_source_mismatch_rejected(self):
        changed = copy.deepcopy(self.data)
        changed["source_sha256"] = "different-video"
        self.annotation.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(ValueError):
            ReviewStore(self.bundle)


@unittest.skipUnless(cv2_available(), "optional image dependencies unavailable")
class AssistedPreparationTests(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        self.cv2, self.np = load_cv2(), load_numpy()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "frames").mkdir()
        self.image = self.np.full((80, 160, 3), 240, self.np.uint8)
        self.cv2.putText(self.image, "Q", (30, 48), self.cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 0), 2)
        self.cv2.imwrite(str(self.root / "frames/a.png"), self.image)
        self.digest = hashlib.sha256((self.root / "frames/a.png").read_bytes()).hexdigest()
        self.manifest = {"session": "fixture", "source_sha256": "video-fixture", "valid": True,
                         "frames": [{"file": "a.png", "white_pixels": 100, "elapsed_s": 0}]}
        self.write_manifest()
        self.model = SimpleNamespace(model_id="fixture-model", predict_mask=Mock(return_value=SimpleNamespace(
            raw_label="Q", similarity=.8, score=.7, accepted=False, angle=0)))

    def write_manifest(self):
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")

    def test_new_candidates_stay_pending_even_after_assisted_preparation(self):
        glyph = SimpleNamespace(mask=self.image[:, :, 0], bbox=(30, 25, 24, 24))
        with patch("scripts.prepare_assisted_review.extract_glyphs", return_value=[glyph]):
            data = prepare(self.root, self.root / "new.json", self.model, step=1)
        self.assertFalse(data["final_acceptance_eligible"])
        self.assertFalse(data["frames"][0]["complete"])
        obj = data["frames"][0]["objects"][0]
        self.assertEqual(obj["label_provenance"], "assistant_proposed")
        self.assertFalse(obj["assistant_visual_reviewed"])
        self.assertTrue(obj["physical_card_id"].startswith("candidate-"))
        self.assertIn("not_verified", obj["identity_provenance"])

    def test_completed_human_record_is_preserved_and_source_file_untouched(self):
        human = {"file": "a.png", "sha256": self.digest, "complete": True, "reviewed_by": "Fixture",
                 "objects": [{"rank": "A", "bbox": [1, 2, 30, 40], "physical_card_id": "A",
                              "label_provenance": "human_reviewed", "reviewed_by": "Fixture"}]}
        original = self.root / "original.json"
        original.write_text(json.dumps({"schema": "original-frame-annotations-1", "source_sha256": "video-fixture", "frames": [human]}), encoding="utf-8")
        before = original.read_bytes()
        data = prepare(self.root, self.root / "new.json", self.model, original_annotations=original)
        self.assertEqual(data["frames"], [human])
        self.assertEqual(original.read_bytes(), before)
        self.model.predict_mask.assert_not_called()
        human["sha256"] = "wrong-frame-hash"
        original.write_text(json.dumps({"schema": "original-frame-annotations-1", "source_sha256": "video-fixture", "frames": [human]}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "原标注帧已变化"):
            prepare(self.root, self.root / "other.json", self.model, original_annotations=original)

    def test_output_overwrite_and_incomplete_material_rejected(self):
        out = self.root / "existing.json"
        out.write_text("keep", encoding="utf-8")
        with self.assertRaises(ValueError):
            prepare(self.root, out, self.model)
        self.assertEqual(out.read_text(), "keep")
        self.manifest["truncated"] = True
        self.write_manifest()
        with self.assertRaises(ValueError):
            prepare(self.root, self.root / "new.json", self.model)

    def test_empty_reference_matches_static_text_but_not_a_new_covering_card(self):
        bbox = (30, 25, 24, 24)
        self.assertTrue(unchanged_background(self.image, self.image.copy(), bbox))
        new_card = self.image.copy()
        new_card[20:60, 20:70] = 250
        self.assertFalse(unchanged_background(new_card, self.image, bbox))
        with self.assertRaises(ValueError):
            unchanged_background(new_card, self.image[:10], bbox)


if __name__ == "__main__":
    unittest.main()
