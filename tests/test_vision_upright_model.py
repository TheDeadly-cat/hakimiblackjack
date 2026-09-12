"""Persisted upright policy and crop provenance; synthetic wiring, not accuracy."""
import contextlib
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.deps import ImageRejected, cv2_available, load_cv2
from blackjack_lab.vision.glyph_dataset import save_queue
from blackjack_lab.vision.model_adapter import TrainedModelAdapter
from blackjack_lab.vision.rank_classifier import RankClassifier, UPRIGHT_ANGLES
from blackjack_lab.vision import rank_classifier
from blackjack_lab.vision.real_cards import extract_glyphs
from tests import test_vision_model_adapter as fixtures
from tests import test_vision_dataset_review as dataset_fixtures


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class UprightModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.bgr, self.loaded = fixtures.scene()
        self.mask = extract_glyphs(self.bgr)[0].mask

    def model(self, policy="upright_upper"):
        return RankClassifier(k=1, min_vote=1, min_margin=0, min_similarity=0,
                              orientation_policy=policy).fit(
            [(self.mask, "8")], style_id="test-style", label_review_status="synthetic")

    def test_saved_policy_controls_training_and_default_inference(self):
        model = self.model()
        self.assertEqual(len(model.features), len(UPRIGHT_ANGLES))
        model.save(self.root / "model")
        loaded = RankClassifier.load(self.root / "model")
        self.assertEqual(model.model_id, loaded.model_id)
        self.assertEqual(loaded.orientation_policy, "upright_upper")
        with patch.object(rank_classifier, "features_from_mask",
                          wraps=rank_classifier.features_from_mask) as feature:
            loaded.predict_mask(self.mask)
        self.assertEqual([call.args[1] for call in feature.call_args_list], list(UPRIGHT_ANGLES))

    def test_no_explicit_flip_can_override_upright_policy(self):
        model = self.model()
        for angles in ((180,), (0, 165), (90,), (float("nan"),), ()):
            with self.subTest(angles=angles):
                with self.assertRaises(ImageRejected):
                    model.predict_mask(self.mask, angles=angles)
                with self.assertRaises(ImageRejected):
                    model.fit([(self.mask, "8")], angles=angles)

    def test_policy_tampering_breaks_identity_and_legacy_default_still_loads(self):
        for policy in ("all", "upright_upper"):
            with self.subTest(policy=policy):
                directory = self.root / policy
                model = self.model(policy)
                model.save(directory)
                self.assertEqual(RankClassifier.load(directory).model_id, model.model_id)
                path = directory / "manifest.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                if policy == "all":
                    self.assertNotIn("orientation_policy", manifest)
                    manifest["orientation_policy"] = "upright_upper"
                else:
                    del manifest["orientation_policy"]
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaises(ImageRejected):
                    RankClassifier.load(directory)

    def test_adapter_filters_lower_duplicates_and_keeps_two_equal_upper_ranks(self):
        cv2 = load_cv2()
        for x in (10, 130):
            cv2.putText(self.bgr, "8", (x+53, 112), cv2.FONT_HERSHEY_SIMPLEX,
                        1.0, (0, 0, 0), 3, cv2.LINE_8)
        rgb = self.bgr[:, :, ::-1].copy().tobytes()
        loaded = replace(self.loaded, rgb=rgb, sha256=fixtures.sha256_bytes(rgb))
        self.assertEqual(len(extract_glyphs(self.bgr)), 4)
        for policy, count in (("all", 4), ("upright_upper", 2)):
            with self.subTest(policy=policy):
                directory = self.root / policy
                self.model(policy).save(directory)
                adapter = TrainedModelAdapter(directory, style_id="test-style")
                with patch.object(adapter.model, "predict_mask", wraps=adapter.model.predict_mask) as predict:
                    result = fixtures.recognize_loaded(loaded, layout=fixtures.layout(), adapter=adapter)
                self.assertEqual(predict.call_count, count)
                self.assertEqual(len(result.observations), count)
                self.assertEqual(len({o.observation_id for o in result.observations}), count)
                self.assertFalse(result.as_dict()["writes_ledger"])
                if policy == "upright_upper":
                    self.assertTrue(all(o.bbox["y"] < 60 for o in result.observations))
                    from blackjack_lab.vision.corner_policy import UPPER_CORNER_POLICY
                    self.assertIn(UPPER_CORNER_POLICY, adapter.extraction_version)

    def test_training_cli_keeps_human_status_and_saved_policy(self):
        cv2 = load_cv2()
        cv2.imwrite(str(self.root / "mask.png"), self.mask)
        row = replace(dataset_fixtures.item("train"), mask_file="mask.png",
                      label_provenance="human_reviewed")
        save_queue([row], self.root)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = dataset_fixtures.SCRIPT.main([
                str(self.root), "--output", str(self.root / "run"),
                "--orientation-policy", "upright_upper", "--require-human-reviewed"])
        self.assertEqual(rc, 2, "Train-only run cannot claim holdout acceptance")
        loaded = RankClassifier.load(self.root / "run" / "model")
        self.assertEqual(loaded.orientation_policy, "upright_upper")
        self.assertEqual(loaded.label_review_status, "human_reviewed")


class HumanOnlyTrainingTests(unittest.TestCase):
    def test_unreviewed_input_in_any_split_is_refused_before_fit(self):
        for split in ("train", "validation", "holdout"):
            with self.subTest(split=split), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                rows = [dataset_fixtures.item("human", label_provenance="human_reviewed"),
                        dataset_fixtures.item("proposal", "other", split=split,
                                              label_provenance="assistant_proposed")]
                with patch.object(dataset_fixtures.SCRIPT, "_load_inputs", return_value=(rows, [])), \
                        patch.object(dataset_fixtures.SCRIPT, "_tune_on_train") as tune:
                    with self.assertRaisesRegex(ContractError, "未复核标签"):
                        dataset_fixtures.SCRIPT.main([
                            "fixture", "--output", str(root / "run"), "--require-human-reviewed"])
                tune.assert_not_called()
                self.assertFalse((root / "run").exists())
