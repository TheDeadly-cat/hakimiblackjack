"""Synthetic engineering fixtures only; these tests are not real-video accuracy evidence."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.deps import ImageRejected, cv2_available
from blackjack_lab.vision.glyph_dataset import GlyphItem, load_queue, save_queue


@unittest.skipUnless(cv2_available(), "optional video/image dependencies unavailable")
class MaterialReviewTests(unittest.TestCase):
    def setUp(self):
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        self.cv2, self.np = load_cv2(), load_numpy()

    def session(self, root, count=3, source_digest=None):
        root = Path(root)
        (root / "frames").mkdir(parents=True)
        frames = []
        for i in range(count):
            image = self.np.full((64, 96, 3), 245, dtype=self.np.uint8)
            self.cv2.putText(image, "Q", (10, 35), self.cv2.FONT_HERSHEY_SIMPLEX,
                             .7, (0, 0, 0), 2)
            name = f"frame-{i}.png"
            self.assertTrue(self.cv2.imwrite(str(root / "frames" / name), image))
            payload = (root / "frames" / name).read_bytes()
            frames.append({"file": name, "sha256": hashlib.sha256(payload).hexdigest(),
                           "elapsed_s": i * 10., "white_pixels": 2000,
                           "white_delta": -20000, "signature": f"synthetic-frame-{i}"})
        manifest = {"session": "synthetic-review-fixture", "frames": frames}
        if source_digest is not None:
            manifest["source_sha256"] = source_digest
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return manifest

    def annotations(self, root, *, provenance="assistant_proposed"):
        from blackjack_lab.vision.frame_annotations import ANNOTATION_SCHEMA, material_identity, read_frame
        _, digest = read_frame(root, "frame-0.png")
        return {"schema": ANNOTATION_SCHEMA, "session": "synthetic-review-fixture",
                "source_sha256": material_identity(root), "role": "development",
                "frames": [{"file": "frame-0.png", "sha256": digest, "round_id": 0,
                            "split": "train", "complete": False, "objects": [{
                                "rank": "Q", "bbox": [8, 14, 22, 26],
                                "physical_card_id": "synthetic-q-1", "rejection_reason": "test box",
                                "label_provenance": provenance}]}]}

    def write_annotations(self, root, data):
        path = Path(root) / "annotations.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def video(self, path):
        writer = self.cv2.VideoWriter(str(path), self.cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
        self.assertTrue(writer.isOpened(), "MJPG writer required by this engineering test")
        for i in range(6):
            frame = self.np.full((64, 96, 3), i * 30, dtype=self.np.uint8)
            writer.write(frame)
        writer.release()

    def test_source_digest_reads_entire_video_not_equal_prefix_and_size(self):
        from blackjack_lab.vision.video_io import VideoReader
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = root / "base.avi"
            self.video(original)
            # Keep a valid AVI with a shared >1 MiB prefix and equal file sizes.
            prefix = original.read_bytes() + bytes(1024 * 1024 + 100)
            identities = []
            for tail in (b"a", b"b"):
                path = root / (tail.decode() + ".avi")
                payload = prefix + tail
                path.write_bytes(payload)
                with VideoReader(path) as reader:
                    identities.append(reader.asset.sha256)
                    self.assertEqual(reader.asset.sha256, hashlib.sha256(payload).hexdigest())
            self.assertNotEqual(*identities)

    def test_capture_identity_ignores_folder_rename_and_detects_changed_frame(self):
        from blackjack_lab.vision.frame_annotations import material_identity
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root / "one")
            self.session(root / "renamed")
            self.assertEqual(material_identity(root / "one"), material_identity(root / "renamed"))
            path = root / "renamed" / "frames" / "frame-2.png"
            path.write_bytes(path.read_bytes() + b"changed")
            self.assertNotEqual(material_identity(root / "one"), material_identity(root / "renamed"))

    def test_original_frame_wrong_source_or_changed_frame_rejected_before_writes(self):
        from blackjack_lab.vision.frame_annotations import build_annotation_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            original = self.annotations(root)
            for mutation in ("source", "frame"):
                data = copy.deepcopy(original)
                if mutation == "source":
                    data["source_sha256"] = "0" * 64
                else:
                    data["frames"][0]["sha256"] = "0" * 64
                path = self.write_annotations(root, data)
                out = root / mutation
                with self.assertRaises(ContractError):
                    build_annotation_queue(root, path, out)
                self.assertFalse(out.exists())

    def test_original_frame_box_cannot_escape_frame(self):
        from blackjack_lab.vision.frame_annotations import build_annotation_queue, read_frame
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            data = self.annotations(root)
            for index, box in enumerate(([-1, 1, 10, 10], [80, 1, 30, 10], [1, 1, 0, 8], [1.2, 1, 8, 8])):
                data["frames"][0]["objects"][0]["bbox"] = box
                with self.assertRaises(ImageRejected):
                    build_annotation_queue(root, self.write_annotations(root, data), root / f"bad-{index}")
            with self.assertRaises(ImageRejected):
                read_frame(root, "../manifest.json")

    def test_annotation_session_split_and_round_contracts_are_enforced(self):
        from blackjack_lab.vision.frame_annotations import build_annotation_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            original = self.annotations(root)
            for index, (key, value) in enumerate((("session", "wrong-session"),
                                                 ("round_id", -1), ("round_id", True),
                                                 ("split", "invalid-split"))):
                data = copy.deepcopy(original)
                if key == "session":
                    data[key] = value
                else:
                    data["frames"][0][key] = value
                with self.assertRaises(ContractError):
                    build_annotation_queue(root, self.write_annotations(root, data), root / f"invalid-{index}")

    def test_assistant_proposal_never_becomes_human_reviewed_on_export(self):
        from blackjack_lab.vision.frame_annotations import build_annotation_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            data = self.annotations(root)
            queue = build_annotation_queue(root, self.write_annotations(root, data), root / "queue")
            self.assertEqual(queue[0].label, "Q")
            self.assertEqual(queue[0].label_provenance, "assistant_proposed")
            self.assertEqual(load_queue(root / "queue")[0].label_provenance, "assistant_proposed")
            data["frames"][0]["objects"][0]["label_provenance"] = "human_reviewed"
            with self.assertRaises(ContractError):
                build_annotation_queue(root, self.write_annotations(root, data), root / "no-reviewer")
            data["frames"][0]["objects"][0]["reviewed_by"] = "synthetic test reviewer"
            queue = build_annotation_queue(root, self.write_annotations(root, data), root / "human-queue")
            self.assertEqual(queue[0].label_provenance, "human_reviewed")

    def test_ui_split_preserves_other_split_and_undo_restores_provenance(self):
        from PIL import ImageTk
        from scripts import label_glyphs
        snapshots = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            items = [GlyphItem("a" * 16, "s", "f1", "sig1", 0, "train", (0,0,8,8),
                               "black", "frames/frame-0.png", "m1", label="K", label_provenance="assistant_proposed"),
                     GlyphItem("b" * 16, "s", "f2", "sig2", 1, "holdout", (0,0,8,8),
                               "black", "frames/frame-1.png", "m2", label="A", label_provenance="human_reviewed")]
            save_queue(items, root)

            class Widget:
                def __init__(self, *args, **kwargs): pass
                def pack(self, *args, **kwargs): pass
                def configure(self, *args, **kwargs): pass
                def set(self, *args, **kwargs): pass

            class Root(Widget):
                def title(self, *args): pass
                def bind(self, name, callback): self.callback = callback
                def mainloop(self):
                    self.callback(SimpleNamespace(keysym="q", char="q"))
                    snapshots.append(load_queue(root))
                    self.callback(SimpleNamespace(keysym="BackSpace", char=""))
                    snapshots.append(load_queue(root))

            fake_tk = SimpleNamespace(Tk=Root, Label=Widget, StringVar=Widget)
            def photo_image(image):
                image.load()  # PhotoImage normally consumes the pixels and closes PNG input.
                return object()
            with patch.dict("sys.modules", {"tkinter": fake_tk}), patch.object(ImageTk, "PhotoImage", photo_image):
                self.assertEqual(label_glyphs.cmd_ui(SimpleNamespace(queue=str(root), split="train")), 0)
            after = load_queue(root)
        self.assertEqual(len(after), 2)
        self.assertEqual([(i.label, i.label_provenance) for i in snapshots[0]],
                         [("Q", "human_reviewed"), ("A", "human_reviewed")])
        self.assertEqual([(i.label, i.label_provenance) for i in snapshots[1]],
                         [("K", "assistant_proposed"), ("A", "human_reviewed")])

    def test_batch_apply_does_not_claim_human_verification(self):
        from scripts import label_glyphs
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            item = GlyphItem("a" * 16, "s", "f1", "sig1", 0, "train", (0,0,8,8),
                             "black", "c1", "m1", label="K", label_provenance="human_reviewed")
            save_queue([item], root)
            labels = root / "labels.json"
            labels.write_text(json.dumps({item.crop_id: "Q"}), encoding="utf-8")
            label_glyphs.cmd_apply(SimpleNamespace(queue=str(root), labels=str(labels)))
            after = load_queue(root)
            self.assertEqual(after[0].label, "Q")
            self.assertNotEqual(after[0].label_provenance, "human_reviewed")

    def test_short_encoded_video_converts_to_readable_valid_manifest(self):
        from scripts.video_to_material import convert
        from blackjack_lab.vision.frame_annotations import read_frame, material_identity
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.avi"
            self.video(path)
            before = path.read_bytes()
            manifest = convert(path, root / "material", interval_s=.2)
            self.assertTrue(manifest["valid"])
            self.assertFalse(manifest["truncated"])
            self.assertEqual(len(manifest["frames"]), 3)
            self.assertEqual(manifest["source_sha256"], hashlib.sha256(before).hexdigest())
            self.assertEqual(material_identity(root / "material"), manifest["source_sha256"])
            for row in manifest["frames"]:
                image, digest = read_frame(root / "material", row["file"])
                self.assertEqual(image.shape, (64, 96, 3))
                self.assertEqual(digest, row["sha256"])
            self.assertEqual(path.read_bytes(), before)

    def test_video_decode_failure_count_and_sampling_cap_are_not_valid(self):
        from scripts.video_to_material import convert
        from blackjack_lab.vision.video_io import VideoReader, VideoRejected
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "source.avi"
            self.video(path)
            original_seek = VideoReader.seek
            def fail_middle(reader, index):
                if index == 2:
                    raise VideoRejected("synthetic decode failure")
                return original_seek(reader, index)
            with patch.object(VideoReader, "seek", fail_middle):
                report = convert(path, root / "decode-failure", interval_s=.2)
            self.assertFalse(report["valid"])
            self.assertEqual(len(report["decode_errors"]), 1)
            self.assertEqual(report["decode_errors"][0]["frame_id"], 2)
            self.assertEqual(len(report["frames"]), 2)
            capped = convert(path, root / "capped", interval_s=.2, max_frames=1)
            self.assertFalse(capped["valid"])
            self.assertTrue(capped["truncated"])

    def test_prepare_queue_records_missing_selected_frame(self):
        from scripts import prepare_glyph_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root, source_digest="a" * 64)
            (root / "frames" / "frame-1.png").unlink()
            out = root / "prepared"
            code = prepare_glyph_queue.main([str(root), "--output", str(out)])
            report = json.loads((out / "split.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertFalse(report["valid"])
            self.assertEqual(len(report["unreadable_frames"]), 1)
            self.assertEqual(report["unreadable_frames"][0]["frame"], "frame-1.png")

    def test_missing_capture_identity_fails_explicitly_without_new_queue(self):
        from scripts import prepare_glyph_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.session(root)
            (root / "frames" / "frame-1.png").unlink()
            out = root / "prepared"
            code = prepare_glyph_queue.main([str(root), "--output", str(out)])
            report = json.loads((out / "preparation-error.json").read_text(encoding="utf-8"))
            self.assertEqual(code, 2)
            self.assertFalse(report["valid"])
            self.assertEqual(report["stage"], "source_identity")
            self.assertFalse((out / "queue.jsonl").exists())

    def test_incomplete_video_material_cannot_be_promoted_by_queue_or_annotation(self):
        from scripts import prepare_glyph_queue, review_original_frames
        from blackjack_lab.vision.frame_annotations import build_annotation_queue
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = self.session(root, source_digest="a" * 64)
            manifest.update(valid=False, decode_errors=[{"frame_id": 99, "error": "synthetic failure"}])
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            out = root / "prepared"
            self.assertEqual(prepare_glyph_queue.main([str(root), "--output", str(out)]), 2)
            report = json.loads((out / "split.json").read_text(encoding="utf-8"))
            self.assertFalse(report["valid"])
            self.assertFalse(report["source_complete"])
            self.assertEqual(len(report["source_decode_errors"]), 1)
            annotation_path = root / "annotation-init.json"
            data = review_original_frames.initialize(root, annotation_path, step=1)
            self.assertFalse(data["source_complete"])
            with self.assertRaises(ContractError):
                build_annotation_queue(root, annotation_path, root / "manual")


if __name__ == "__main__":
    unittest.main()
