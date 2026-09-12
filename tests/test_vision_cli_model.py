"""CLI wiring with synthetic pixels and the actual shared model adapter.

The capture-source double is explicit: these tests do not claim WGC validation.
"""
import contextlib
import io
import json
import unittest
from unittest.mock import patch

from blackjack_lab.capture.frame_intake import FrameIntake
from blackjack_lab.vision.deps import cv2_available
from blackjack_lab.vision.image_io import write_png_rgb
from blackjack_lab.vision.live_input import LiveStyle, NormalizedBox
from tests import test_vision_model_adapter as fixtures


@unittest.skipUnless(cv2_available(), "optional vision dependencies unavailable")
class TestVisionModelCli(unittest.TestCase):
    setUp = fixtures.TestModelAdapter.setUp

    def style_file(self):
        style = LiveStyle("test-style", {"seat": NormalizedBox(0, 0, 1, 1, "玩家1")})
        return style.save(self.root / "style.json")

    def test_image_cli_explicit_model_and_style(self):
        from scripts import vision_demo
        image, output = self.root / "frame.png", self.root / "result.json"
        write_png_rgb(image, 240, 130, self.loaded.rgb)
        with contextlib.redirect_stdout(io.StringIO()):
            code = vision_demo.main([str(image), "--model", str(self.root / "model"),
                                     "--style", str(self.style_file()), "--json", str(output)])
        self.assertEqual(code, 0)
        result = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(result["model_id"], self.adapter.model_id)
        self.assertEqual([o["accepted_rank"] for o in result["observations"]], ["8", "8"])
        self.assertFalse(result["writes_ledger"])

    def test_video_probe_selects_the_same_model(self):
        from scripts import probe_navy_live_video
        class FakeVideo:
            def __init__(self, path): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def seek(inner, index): return self.loaded
        with patch("blackjack_lab.vision.video_io.VideoReader", FakeVideo), contextlib.redirect_stdout(io.StringIO()):
            code = probe_navy_live_video.main([
                str(self.root / "synthetic.mp4"), "--frame", "0", "--output", str(self.root / "replay"),
                "--model", str(self.root / "model"), "--style", str(self.style_file())])
        self.assertEqual(code, 0)
        result = json.loads((self.root / "replay" / "candidates.json").read_text(encoding="utf-8"))
        self.assertEqual(result["model_id"], self.adapter.model_id)
        self.assertEqual([o["accepted_rank"] for o in result["observations"]], ["8", "8"])

    def test_capture_probe_reuses_intake_and_shared_model_with_explicit_fake_source(self):
        from scripts import live_capture_probe
        class FakeCapture:
            def __init__(inner):
                inner.intake = FrameIntake("window:synthetic", target_fps=1000)
            def start(inner): inner.intake.mark_started()
            def stop(inner): inner.intake.mark_stopped()
            def token(inner): return inner.intake.token()
            def status(inner): return inner.intake.status()
            def report(inner): return inner.intake.report()
            def latest(inner):
                inner.intake.offer(self.bgr)
                return inner.intake.latest()
        source = FakeCapture()
        with (patch.object(live_capture_probe, "wgc_available", return_value=True),
              patch.object(live_capture_probe, "open_window_source", return_value=source),
              contextlib.redirect_stdout(io.StringIO())):
            code = live_capture_probe.main([
                "--window", "123", "--seconds", "0.15", "--output", str(self.root / "live"),
                "--model", str(self.root / "model"), "--style", str(self.style_file())])
        self.assertEqual(code, 0)
        report = json.loads((self.root / "live" / "capture-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["recognition"]["model_id"], self.adapter.model_id)
        self.assertGreaterEqual(report["recognition"]["frames"], 1)
        result = json.loads((self.root / "live" / "latest-candidates.json").read_text(encoding="utf-8"))
        self.assertEqual([o["accepted_rank"] for o in result["observations"]], ["8", "8"])
        self.assertFalse(result["writes_ledger"])

    def test_model_without_explicit_style_rejected_before_capture(self):
        from scripts import live_capture_probe, vision_demo
        for invoke in (
            lambda: live_capture_probe.main(["--window", "123", "--model", str(self.root / "model")]),
            lambda: vision_demo.main(["frame.png", "--model", str(self.root / "model")]),
        ):
            with self.subTest(invoke=invoke), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as failure:
                    invoke()
                self.assertEqual(failure.exception.code, 2)

    def test_failed_next_frame_withdraws_previous_runtime_result(self):
        from blackjack_lab.vision.model_adapter import RecognitionRuntime
        runtime = RecognitionRuntime(self.adapter)
        runtime.recognize_loaded(self.loaded, layout=fixtures.layout(), source_key="one")
        with patch.object(self.adapter, "recognize", side_effect=ValueError("decode failed")):
            with self.assertRaises(ValueError):
                runtime.recognize_loaded(self.loaded, layout=fixtures.layout(), source_key="one")
        self.assertIsNone(runtime.current_result)
