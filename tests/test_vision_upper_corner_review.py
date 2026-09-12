"""Upper-only annotation policy tests; fixtures do not establish real accuracy."""
import copy
import json
import unittest
from types import SimpleNamespace

from blackjack_lab.vision.corner_policy import UpperCornerSelector, corner_key
from blackjack_lab.vision.deps import cv2_available
from scripts.prepare_upper_review import select_frame
from scripts.review_assisted_batch import first_pending_page, labels_complete
from tests import test_vision_assisted_review as fixture_helpers


class UpperReviewSelectionTests(unittest.TestCase):
    def setUp(self):
        self.frame = {"sha256": "frame-content", "complete": False, "objects": [
            {"bbox": [20, 10, 20, 15], "rank": "8", "physical_card_id": "candidate-1",
             "label_provenance": "human_reviewed", "reviewed_by": "Fixture", "reviewed_at": "saved-time"},
            {"bbox": [55, 45, 20, 15], "rank": "8", "physical_card_id": "candidate-2",
             "label_provenance": "human_reviewed", "reviewed_by": "Fixture", "reviewed_at": "saved-time"}]}
        self.selector = SimpleNamespace(assess=lambda box: {"keep": box[1] < 30, "reason": "fixture-edge"})

    def test_lower_copy_archived_without_erasing_human_confirmation(self):
        original = copy.deepcopy(self.frame)
        result = select_frame(self.frame, "video", self.selector)
        self.assertEqual(self.frame, original)
        self.assertEqual(len(result["objects"]), 1)
        self.assertEqual(len(result["excluded_objects"]), 1)
        for before, after in zip(original["objects"], result["objects"] + result["excluded_objects"]):
            for key, value in before.items():
                self.assertEqual(after[key], value)

    def test_human_rank_pinned_when_assistant_changes_crop_and_suggests_rank(self):
        key = corner_key("video", self.frame["sha256"], self.frame["objects"][0]["bbox"])
        result = select_frame(self.frame, "video", self.selector,
                              {key: {"keep": True, "bbox": [21, 11, 18, 14], "rank": "9"}})
        obj = result["objects"][0]
        self.assertEqual(obj["rank"], "8")
        self.assertEqual(obj["reviewed_by"], "Fixture")
        self.assertEqual(obj["assistant_upper_rank_suggestion"], "9")
        self.assertEqual(obj["bbox_before_upper_selection"], [20, 10, 20, 15])
        self.assertEqual(obj["bbox_provenance"], "assistant_upper_corner_crop")

    def test_same_rank_on_two_cards_is_not_deduplicated_by_rank(self):
        self.frame["objects"][1]["bbox"] = [120, 10, 20, 15]
        result = select_frame(self.frame, "video", self.selector)
        self.assertEqual([o["rank"] for o in result["objects"]], ["8", "8"])
        self.assertNotEqual(*[o["upper_corner_selection"]["original_key"] for o in result["objects"]])

    def test_no_bottom_fallback_when_upper_is_absent(self):
        self.frame["objects"] = self.frame["objects"][1:]
        result = select_frame(self.frame, "video", self.selector)
        self.assertEqual(result["objects"], [])
        self.assertEqual(len(result["excluded_objects"]), 1)

    def test_suggestions_and_scope_are_not_promoted_to_human_truth(self):
        self.frame["objects"][0]["label_provenance"] = "assistant_proposed"
        result = select_frame(self.frame, "video", self.selector)
        self.assertFalse(labels_complete(result))
        self.assertEqual(result["objects"][0]["label_provenance"], "assistant_proposed")
        self.assertEqual(result["upper_selection_provenance"], "assistant_proposed")

    def test_completed_labels_are_not_reset_by_unreviewed_archived_items(self):
        self.frame["objects"][1]["label_provenance"] = "assistant_proposed"
        result = select_frame(self.frame, "video", self.selector)
        self.assertTrue(labels_complete(result))
        self.assertFalse(result["complete"])

    def test_resume_prioritizes_pending_cards_without_confirming_empty_pages(self):
        empty = {"objects": [], "complete": False}
        confirmed = {"objects": [{"label_provenance": "human_reviewed"}]}
        pending = {"objects": [{"label_provenance": "assistant_proposed"}]}
        self.assertEqual(first_pending_page([empty, confirmed, pending]), 2)
        self.assertFalse(labels_complete(empty))


class UpperReviewStoreTests(unittest.TestCase):
    def setUp(self):
        # Reuse fixture setup without inheriting and rerunning its eight tests.
        fixture_helpers.ReviewStoreTests.setUp(self)

    def test_restore_and_undo_preserve_label_provenance(self):
        self.store.documents[0]["frames"][0]["review_policy"] = "upper-upright-corner-1"
        self.store.save(0)
        before = copy.deepcopy(self.store.documents[0])
        self.store.delete(0, 0, 0)
        self.assertEqual(len(self.store.documents[0]["frames"][0]["excluded_objects"]), 1)
        self.store.restore(0, 0, 0)
        self.assertEqual(self.store.documents[0]["frames"][0]["objects"][-1]["label_provenance"], "assistant_proposed")
        self.store.undo()
        self.store.undo()
        self.assertEqual(self.store.documents[0], before)

    def test_explicit_confirmation_accepts_new_crop_and_scope(self):
        obj = self.store.documents[0]["frames"][0]["objects"][0]
        obj.update(bbox_provenance="assistant_upper_corner_crop", upper_corner_selection={"provenance": "assistant_proposed"})
        self.store.save(0)
        self.store.confirm(0, 0, "Fixture", selected=0)
        obj = self.store.documents[0]["frames"][0]["objects"][0]
        self.assertEqual(obj["bbox_provenance"], "human_reviewed")
        self.assertEqual(obj["upper_corner_selection"]["provenance"], "human_reviewed")


@unittest.skipUnless(cv2_available(), "optional image dependencies unavailable")
class UpperEdgeGeometryTests(unittest.TestCase):
    def test_assistant_crop_cannot_export_as_human_reviewed_training_truth(self):
        from blackjack_lab.vision.frame_annotations import build_annotation_queue
        fixture = fixture_helpers.AssistedPreparationTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        data = {"schema": "original-frame-annotations-1", "session": "fixture",
                "source_sha256": "video-fixture", "frames": [{"file": "a.png", "sha256": fixture.digest,
                "round_id": 0, "split": "train", "objects": [{"rank": "Q", "bbox": [30,25,24,24],
                "label_provenance": "human_reviewed", "reviewed_by": "Fixture",
                "bbox_provenance": "assistant_upper_corner_crop", "physical_card_id": "fixture-card",
                "rejection_reason": "upper crop proposal"}]}]}
        annotation = fixture.root / "annotations.json"
        annotation.write_text(json.dumps(data), encoding="utf-8")
        queue = build_annotation_queue(fixture.root, annotation, fixture.root / "queue")
        self.assertEqual(queue[0].label_provenance, "assistant_proposed")

    def test_each_card_uses_its_own_top_even_in_bottom_half_of_image(self):
        from blackjack_lab.vision.deps import load_numpy
        np = load_numpy()
        image = np.zeros((360, 200, 3), np.uint8)
        image[20:100, 20:130] = 245
        image[230:310, 30:140] = 245
        selector = UpperCornerSelector(image)
        self.assertTrue(selector.assess([25, 25, 20, 15])["keep"])
        self.assertFalse(selector.assess([95, 80, 20, 15])["keep"])
        self.assertTrue(selector.assess([35, 235, 20, 15])["keep"])
        self.assertFalse(selector.assess([105, 290, 20, 15])["keep"])

    def test_whole_card_and_out_of_bounds_boxes_cannot_be_assumed_upright(self):
        from blackjack_lab.vision.deps import load_numpy
        selector = UpperCornerSelector(load_numpy().full((100, 160, 3), 245, dtype="uint8"))
        self.assertFalse(selector.assess([10, 10, 120, 70])["keep"])
        with self.assertRaises(ValueError):
            selector.assess([-1, 10, 10, 10])


if __name__ == "__main__":
    unittest.main()
