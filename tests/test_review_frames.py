"""Review-frame extraction hashes stills. It cannot attest unused video or M4 pass."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from blackjack_lab.vision.deps import cv2_available

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(cv2_available(), "需要 OpenCV 才能编解码短录像")
class ReviewFrameExtractionTest(unittest.TestCase):
    def _write_clip(self, folder, frames=4):
        from blackjack_lab.vision.synthetic import FELT, RgbCanvas

        cv2 = __import__("cv2")
        source = Path(folder) / "clip.avi"
        canvas = RgbCanvas(64, 48, FELT)
        writer = cv2.VideoWriter(
            str(source), cv2.VideoWriter_fourcc(*"MJPG"), 10, (64, 48))
        self.assertTrue(writer.isOpened())
        frame = __import__("numpy").frombuffer(bytes(canvas.buf), dtype="uint8").reshape(48, 64, 3)
        for _ in range(frames):
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        writer.release()
        return source

    def test_extract_does_not_attest_unused_or_acceptance(self):
        from blackjack_lab.analysis.evidence import LEVEL_EVIDENCE_LINKED, sha256_file
        from blackjack_lab.analysis.review_frames import extract_review_frames

        with tempfile.TemporaryDirectory() as folder:
            source = self._write_clip(folder)
            before = source.read_bytes()
            dest = Path(folder) / "review"
            result = extract_review_frames(source, dest, max_frames=2)
            self.assertEqual(before, source.read_bytes())
            self.assertFalse(result["accepted"])
            self.assertFalse(result["passed"])
            self.assertFalse(result["human_run"])
            self.assertFalse(result["independent_video"])
            self.assertFalse(result["unused_in_training"])
            self.assertFalse(result["unused_in_threshold_selection"])
            self.assertEqual(LEVEL_EVIDENCE_LINKED, result["evidence_level"])
            self.assertEqual(sha256_file(source), result["video_sha256"])
            self.assertEqual(2, len(result["frames"]))
            self.assertTrue((dest / "review-frames.json").is_file())
            for row in result["frames"]:
                self.assertEqual("review-frame-not-accepted", row["role"])
                self.assertTrue(Path(row["path"]).is_file())
                self.assertEqual(64, len(row["sha256"]))

    def test_extract_script_refuses_to_print_an_accepted_result(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._write_clip(folder)
            dest = Path(folder) / "out"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "extract_review_frames.py"),
                 "--video", str(source), "--output-dir", str(dest), "--max-frames", "2"],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads(completed.stdout)
            self.assertFalse(body["accepted"])
            self.assertFalse(body["independent_video"])
            self.assertFalse(body["unused_in_training"])
            self.assertEqual(2, body["frame_count"])

    def test_extract_script_can_bind_an_unchecked_pack(self):
        with tempfile.TemporaryDirectory() as folder:
            source = self._write_clip(folder)
            dest = Path(folder) / "out"
            pack_out = Path(folder) / "pack.json"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "extract_review_frames.py"),
                 "--video", str(source), "--output-dir", str(dest), "--max-frames", "2",
                 "--pack-output", str(pack_out)],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertEqual(0, completed.returncode, completed.stderr)
            body = json.loads(completed.stdout)
            self.assertFalse(body["accepted"])
            pack = json.loads(pack_out.read_text(encoding="utf-8"))
            self.assertFalse(pack["accepted"])
            item = pack["items"]["authorized_shoe_video"]
            self.assertFalse(item["passed"])
            self.assertTrue(item["review_sha_match"])
            refused = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "extract_review_frames.py"),
                 "--video", str(source), "--output-dir", str(dest), "--max-frames", "1",
                 "--item", "unused_attestation", "--pack-output", str(Path(folder) / "nope.json")],
                cwd=str(ROOT), capture_output=True, text=True, timeout=30)
            self.assertNotEqual(0, refused.returncode)

    def test_attach_review_frames_matches_hash_but_does_not_pass(self):
        from blackjack_lab.analysis.acceptance_pack import build_acceptance_pack
        from blackjack_lab.analysis.evidence import LEVEL_EVIDENCE_LINKED
        from blackjack_lab.analysis.review_frames import attach_review_frames, extract_review_frames

        with tempfile.TemporaryDirectory() as folder:
            source = self._write_clip(folder)
            dest = Path(folder) / "review"
            result = extract_review_frames(source, dest, max_frames=2)
            pack = attach_review_frames(build_acceptance_pack(), result)
            item = pack["items"]["authorized_shoe_video"]
            self.assertTrue(item["review_sha_match"])
            self.assertEqual(LEVEL_EVIDENCE_LINKED, item["evidence_level"])
            self.assertFalse(item["passed"])
            self.assertFalse(pack["accepted"])
            self.assertEqual(5, len(pack["human_blockers"]))
            with self.assertRaises(ValueError):
                attach_review_frames(pack, result, item_id="unused_attestation")


if __name__ == "__main__":
    unittest.main()
