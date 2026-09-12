"""Stage evidence must not turn counterfactual classifications into outputs."""
import contextlib
import hashlib
import inspect
import io
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.corner_diagnostics import trace_corner_stages
from blackjack_lab.vision.deps import cv2_available, load_cv2
from blackjack_lab.vision.image_io import write_png_rgb
from blackjack_lab.vision.model_adapter import TrainedModelAdapter
from blackjack_lab.vision.rank_classifier import RankClassifier
from blackjack_lab.vision.real_cards import extract_glyphs
from tests import test_vision_model_adapter as fixtures


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class CornerStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        bgr, loaded = fixtures.scene()
        cv2 = load_cv2()
        for x in (10,130):
            cv2.putText(bgr, "8", (x+53,112), cv2.FONT_HERSHEY_SIMPLEX, 1,
                        (0,0,0), 3, cv2.LINE_8)
        rgb = bgr[:,:,::-1].copy().tobytes()
        self.loaded = replace(loaded, rgb=rgb, sha256=hashlib.sha256(rgb).hexdigest())
        self.model = self.root / "model"
        RankClassifier(k=1, min_vote=1, min_margin=0, min_similarity=0,
                       orientation_policy="upright_upper").fit(
            [(extract_glyphs(bgr)[0].mask,"8")], style_id="test-style",
            label_review_status="synthetic").save(self.model)
        self.adapter = TrainedModelAdapter(self.model, style_id="test-style")

    def test_excluded_predictions_remain_counterfactual_with_no_gt_input(self):
        before = {p.name:p.read_bytes() for p in self.model.iterdir()}
        trace = trace_corner_stages(self.loaded, self.adapter, "fixture-source")
        self.assertEqual(list(inspect.signature(trace_corner_stages).parameters),
                         ["loaded", "adapter", "source_sha256"])
        self.assertEqual(len(trace["candidates"]), 4)
        self.assertEqual(sum(r["sent_to_classifier_in_production"] for r in trace["candidates"]), 2)
        for row in trace["candidates"]:
            self.assertFalse(row["counterfactual_is_runtime_output"])
            if not row["selection"]["keep"]:
                self.assertIsNone(row["production_prediction"])
                self.assertIsNotNone(row["counterfactual_prediction"])
            self.assertEqual(row["frame_sha256"], self.loaded.sha256)
        self.assertEqual(before, {p.name:p.read_bytes() for p in self.model.iterdir()})

    def test_cli_copies_originals_and_fails_on_missing_focus_or_changed_frame(self):
        from scripts.diagnose_corner_stages import main
        session = self.root / "session"
        (session/"frames").mkdir(parents=True)
        path = session/"frames/a.png"
        write_png_rgb(path,240,130,self.loaded.rgb)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        (session/"manifest.json").write_text(json.dumps({"source_sha256":"source",
                                                        "frames":[{"file":"a.png"}]}))
        annotation = self.root/"annotations.json"
        truth = {"source_sha256":"source","frames":[{"file":"a.png","sha256":digest,
                                                      "objects":[{"bbox":[26,31,17,20],"rank":"8"}]}]}
        annotation.write_text(json.dumps(truth))
        cases = self.root/"cases.json"
        cases.write_text(json.dumps([{"session":str(session),"annotations":str(annotation)}]))
        layout = self.root/"layout.json"
        layout.write_text(json.dumps({"layout_profile_id":"test","style_id":"test-style",
            "canvas":{"width":240,"height":130},"felt_kind":"navy",
            "regions":{"seat":{"x":0,"y":0,"w":240,"h":130,"seat_hint":"玩家1"}}}), encoding="utf-8")
        def argv(name):
            return ["--cases",str(cases),"--model",str(self.model),"--layout",str(layout),
                    "--output",str(self.root/name)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(argv("valid")),0)
        self.assertEqual((self.root/"valid/originals"/f"{digest}.png").read_bytes(),path.read_bytes())
        with self.assertRaisesRegex(ContractError,"already exists"):
            main(argv("valid"))
        focus = self.root/"missing-focus.json"
        focus.write_text(json.dumps({"rows":[{"source_index":0,"frame":"absent.png"}]}))
        with self.assertRaisesRegex(ContractError,"Not all existing focus"):
            main(argv("missing-focus") + ["--focus",str(focus)])
        truth["frames"][0]["sha256"] = "changed"
        annotation.write_text(json.dumps(truth))
        with self.assertRaisesRegex(ContractError,"frame bytes"):
            main(argv("changed"))
