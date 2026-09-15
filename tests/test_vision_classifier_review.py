"""Review regressions: independent evidence, truthful denominators and model identity."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.vision.deps import ImageRejected, cv2_available
from blackjack_lab.vision.glyph_dataset import GlyphItem, LABEL_RANKS


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class ClassifierReviewTests(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        self.np, self.cv2 = load_numpy(), load_cv2()

    def model(self, *, min_vote=2):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        a = self.np.zeros((28, 20), dtype=self.np.uint8)
        b = a.copy()
        a[3:25, 3:7] = 255
        a[3:7, 3:18] = 255
        b[3:7, 3:18] = 255
        b[20:25, 3:18] = 255
        model = RankClassifier(min_vote=min_vote)
        return model.fit([(a, "A", "physical-a"), (b, "K", "physical-b")]), a, b

    def vector_model(self, similarities, labels, origins, **kwargs):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        model = RankClassifier(**kwargs)
        model.features = self.np.asarray([
            [s, (1.0 - s * s) ** 0.5] for s in similarities], dtype=self.np.float32)
        model.label_ids = self.np.asarray([LABEL_RANKS.index(s) for s in labels], dtype=self.np.int32)
        model.origin_ids = self.np.asarray(origins, dtype=self.np.int32)
        return model

    def test_augmented_vectors_cannot_supply_independent_votes(self):
        model = self.vector_model([1, .99, .98, .6], ["A", "A", "A", "K"], [0, 0, 0, 1])
        guess = model._nearest(self.np.asarray([1., 0.], dtype=self.np.float32))
        self.assertFalse(guess.accepted)
        self.assertEqual(guess.independent_votes, 1)
        self.assertEqual(guess.independent_neighbors, 2)
        self.assertEqual(guess.rejection_reason, "insufficient_independent_votes")

    def test_margin_uses_other_class_outside_top_k(self):
        model = self.vector_model([1, .99, .98, .97], ["A", "A", "A", "K"], [0, 1, 2, 3])
        guess = model._nearest(self.np.asarray([1., 0.], dtype=self.np.float32))
        self.assertFalse(guess.accepted)
        self.assertEqual(guess.independent_votes, 3)
        self.assertAlmostEqual(guess.margin, .02, places=5)
        self.assertAlmostEqual(guess.nearest_other_similarity, .97, places=5)

    def test_absolute_similarity_rejects_large_relative_margin(self):
        model = self.vector_model([.6, .59, .1], ["A", "A", "K"], [0, 1, 2])
        guess = model._nearest(self.np.asarray([1., 0.], dtype=self.np.float32))
        self.assertGreater(guess.margin, .4)
        self.assertFalse(guess.accepted)
        self.assertEqual(guess.rejection_reason, "low_similarity")

    def test_single_class_cannot_invent_a_competing_margin(self):
        model = self.vector_model([1, .99], ["A", "A"], [0, 1], min_margin=0)
        guess = model._nearest(self.np.asarray([1., 0.], dtype=self.np.float32))
        self.assertFalse(guess.accepted)
        self.assertIsNone(guess.nearest_other_similarity)

    def test_fit_keeps_origin_groups_and_rejects_conflicting_labels(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        _, a, b = self.model()
        m = RankClassifier().fit([(a, "A"), (a, "A"), (b, "K")], angles=(0, 15, -15))
        self.assertEqual(m.n_by_class["A"], 2)
        self.assertEqual(m.n_origins_by_class["A"], 1)
        self.assertFalse(m.predict_mask(a, angles=(0,)).accepted)
        with self.assertRaises(ImageRejected):
            RankClassifier().fit([(a, "A"), (b, "K")], sample_ids=["same", "same"])

    def test_explicit_origin_renames_cannot_make_duplicate_content_independent(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        _, a, b = self.model()
        model = RankClassifier().fit([(a, "A", "renamed-one"),
                                      (a.copy(), "A", "renamed-two"), (b, "K", "other")])
        self.assertEqual(model.n_by_class["A"], 2)
        self.assertEqual(model.n_origins_by_class["A"], 1)
        guess = model.predict_mask(a, angles=(0,))
        self.assertFalse(guess.accepted)
        self.assertEqual(guess.independent_votes, 1)
        with tempfile.TemporaryDirectory() as tmp:
            path = model.save(Path(tmp) / "model")
            loaded = RankClassifier.load(path)
            self.assertEqual(loaded.n_origins_by_class["A"], 1)
            self.assertFalse(loaded.predict_mask(a, angles=(0,)).accepted)

    def test_duplicate_content_conflicting_labels_are_refused(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        _, a, _ = self.model()
        with self.assertRaises(ImageRejected):
            RankClassifier().fit([(a, "A", "renamed-one"), (a.copy(), "K", "renamed-two")])

    def test_content_and_declared_origin_aliases_merge_transitively(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        _, a, b = self.model()
        altered = a.copy()
        altered[0, 0] = 255
        model = RankClassifier().fit([(a, "A", "physical-one"),
                                      (altered, "A", "physical-one"),
                                      (altered.copy(), "A", "renamed-two"), (b, "K", "other")])
        self.assertEqual(model.n_origins_by_class["A"], 1)
        self.assertEqual(len(set(model.origin_ids[model.label_ids == LABEL_RANKS.index("A")].tolist())), 1)

    def test_rotation_preserves_edge_strokes_and_six_nine_upright_only(self):
        from blackjack_lab.vision.rank_classifier import rotate_mask, angles_for_label
        mask = self.np.ones((30, 20), dtype=self.np.uint8) * 255
        rotated = rotate_mask(mask, 30)
        self.assertGreater(rotated.shape[0], mask.shape[0])
        self.assertGreater(rotated.shape[1], mask.shape[1])
        self.assertGreaterEqual(self.np.count_nonzero(rotated), .95 * mask.size)
        for rank in ("6", "9"):
            self.assertEqual(angles_for_label(rank, (0, 180, 165)), (0,))

    def test_model_roundtrip_identity_and_corruption_rejection(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        model, _, _ = self.model()
        with tempfile.TemporaryDirectory() as tmp:
            path = model.save(Path(tmp) / "model")
            loaded = RankClassifier.load(path)
            self.assertEqual(loaded.model_id, model.model_id)
            self.assertEqual(loaded.training_digest, model.training_digest)
            self.assertEqual(loaded.label_review_status, "unverified")
            with self.assertRaises(ImageRejected):
                model.save(path)
            blob = path / "model.npz"
            blob.write_bytes(blob.read_bytes() + b"corrupted")
            with self.assertRaises(ImageRejected):
                RankClassifier.load(path)

    def test_manifest_schema_and_style_tampering_are_rejected(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        model, _, _ = self.model()
        with tempfile.TemporaryDirectory() as tmp:
            path = model.save(Path(tmp) / "model")
            manifest_path = path / "manifest.json"
            original = json.loads(manifest_path.read_text(encoding="utf-8"))
            for key, value in (("schema", "0.3e-rank-knn-1"), ("style_id", "another-table")):
                manifest_path.write_text(json.dumps(dict(original, **{key: value})), encoding="utf-8")
                with self.assertRaises(ImageRejected):
                    RankClassifier.load(path)

    def test_training_digest_binds_masks_labels_origins_and_thresholds(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        _, a, b = self.model()
        altered = a.copy()
        altered[0, 0] = 255
        variants = [
            RankClassifier().fit([(a, "A", "a"), (b, "K", "b")]),
            RankClassifier().fit([(altered, "A", "a"), (b, "K", "b")]),
            RankClassifier().fit([(a, "2", "a"), (b, "K", "b")]),
            RankClassifier().fit([(a, "A", "another-a"), (b, "K", "b")]),
            RankClassifier(min_similarity=.9).fit([(a, "A", "a"), (b, "K", "b")]),
        ]
        self.assertEqual(len({m.training_digest for m in variants}), len(variants))

    def test_malformed_blob_rejected_even_with_matching_file_hash(self):
        from blackjack_lab.vision.rank_classifier import RankClassifier
        model, _, _ = self.model()
        with tempfile.TemporaryDirectory() as tmp:
            path = model.save(Path(tmp) / "model")
            self.np.savez(path / "model.npz", features=self.np.full((1, 208), self.np.nan),
                          label_ids=self.np.asarray([0]), origin_ids=self.np.asarray([0]))
            manifest_path = path / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["blob_sha256"] = hashlib.sha256((path / "model.npz").read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ImageRejected):
                RankClassifier.load(path)

    def test_empty_evaluation_does_not_claim_completion(self):
        from blackjack_lab.vision.rank_classifier import evaluate_items
        model, _, _ = self.model()
        report = evaluate_items(model, [], Path("."))
        self.assertFalse(report["evaluation_complete"])
        self.assertEqual(report["incomplete_reason"], "no_labeled_items")

    def test_all_output_precision_counts_junk_and_missing_files_keep_denominator(self):
        from blackjack_lab.vision.rank_classifier import RankGuess, evaluate_items

        class AlwaysA:
            def predict_mask(self, mask):
                return RankGuess("A", "A", .99, .1, True)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.cv2.imwrite(str(root / "valid.png"), self.np.ones((5, 5), dtype=self.np.uint8))
            (root / "broken.png").write_text("not an image", encoding="utf-8")
            def item(id_, label, file):
                return GlyphItem(id_, "session", id_ + ".jpg", id_, 0, "holdout",
                                 (0, 0, 5, 5), "black", file, file, label=label)
            report = evaluate_items(AlwaysA(), [item("a", "A", "valid.png"),
                item("junk", "junk", "valid.png"), item("q", "Q", "missing.png"),
                item("k", "K", "broken.png")], root)
        self.assertEqual(report["n_labeled"], 4)
        self.assertEqual(report["n_identifiable"], 3)
        self.assertEqual(report["n_invalid"], 2)
        self.assertFalse(report["valid"])
        self.assertFalse(report["end_to_end_evaluated"])
        self.assertEqual(report["all_output_precision"], .5)
        self.assertEqual(report["raw_rank_accuracy_among_accepted"], 1)
        self.assertEqual(report["extracted_identifiable_recall"], 1 / 3)
        self.assertEqual(report["confusion"]["Q"]["invalid"], 1)
        self.assertEqual(set(report["confusion"]), set(LABEL_RANKS))
        self.assertNotIn("end_to_end_identifiable_recall", report)


if __name__ == "__main__":
    unittest.main()
