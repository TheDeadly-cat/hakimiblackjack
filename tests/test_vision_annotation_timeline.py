"""Encoded synthetic-video timeline and manual annotation provenance contracts.

The Tk interactions below are explicit synthetic test actions. They do not
establish human labels or recognition accuracy for any real user material.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from blackjack_lab.vision.deps import cv2_available, load_cv2, load_numpy
from blackjack_lab.vision.frame_annotations import read_frame
from blackjack_lab.vision.video_io import VideoReader
from scripts import review_original_frames, video_to_material


@unittest.skipUnless(cv2_available(), "optional image/video annotation dependencies unavailable")
class AnnotationTimelineContracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cv2, self.np = load_cv2(), load_numpy()

    def make_video(self):
        path = self.root / "synthetic.avi"
        writer = self.cv2.VideoWriter(str(path), self.cv2.VideoWriter_fourcc(*"MJPG"), 10, (96, 64))
        self.assertTrue(writer.isOpened(), "This integration test needs the pinned OpenCV MJPG writer")
        try:
            for index in range(6):
                image = self.np.full((64, 96, 3), (20 + index * 5, 65, 170), dtype=self.np.uint8)
                self.cv2.rectangle(image, (15, 12), (55, 43), (245, 210 - index * 5, 70), -1)
                writer.write(image)
        finally:
            writer.release()
        return path

    def converted(self):
        video = self.make_video()
        session = self.root / "material"
        manifest = video_to_material.convert(video, session, interval_s=.2, roi=[11, 7, 73, 53])
        annotation_path = self.root / "annotations.json"
        annotations = review_original_frames.initialize(session, annotation_path, step=1)
        return video, session, manifest, annotation_path, annotations

    def test_roi_conversion_initialization_preserves_three_distinct_hash_spaces(self):
        video, session, manifest, _, annotations = self.converted()
        self.assertTrue(manifest["valid"])
        self.assertTrue(manifest["source_stable_during_conversion"])
        self.assertEqual(annotations["coordinate_space"], "material_roi")
        self.assertEqual(annotations["source_roi"], [11, 7, 73, 53])
        self.assertEqual([r["frame_index"] for r in annotations["frames"]], [0, 2, 4])
        self.assertEqual(annotations["source_sha256"], hashlib.sha256(video.read_bytes()).hexdigest())
        with VideoReader(video) as reader:
            for material, annotation in zip(manifest["frames"], annotations["frames"]):
                loaded = reader.seek(annotation["frame_index"])
                full_rgb = self.np.frombuffer(loaded.rgb, self.np.uint8).reshape(64, 96, 3)
                expected_roi_rgb = full_rgb[7:53, 11:73]
                bgr, png_digest = read_frame(session, annotation["file"])
                material_rgb = bgr[:, :, ::-1]
                self.np.testing.assert_array_equal(material_rgb, expected_roi_rgb)
                source_rgb_hash = hashlib.sha256(loaded.rgb).hexdigest()
                material_rgb_hash = hashlib.sha256(material_rgb.tobytes()).hexdigest()
                self.assertEqual(material["source_rgb_sha256"], source_rgb_hash)
                self.assertEqual(annotation["source_rgb_sha256"], source_rgb_hash)
                self.assertEqual(annotation["material_rgb_sha256"], material_rgb_hash)
                self.assertEqual(annotation["sha256"], png_digest)
                self.assertEqual(material["sha256"], png_digest)
                self.assertEqual(material["signature"], hashlib.sha256(bgr.tobytes()).hexdigest())
                self.assertEqual(len({source_rgb_hash, material_rgb_hash, png_digest}), 3)
                self.assertNotEqual(material["signature"], material_rgb_hash, "BGR signature is not RGB identity")
                self.assertEqual(annotation["elapsed_s"], annotation["frame_index"] / reader.asset.fps)
                self.assertFalse(annotation["complete"])
                self.assertEqual(annotation["round_provenance"], "automatic_white_pixel_segmentation")
                self.assertNotIn("reviewed_by", annotation)

    def test_capture_frame_counter_cannot_be_mislabeled_as_video_index(self):
        _, session, manifest, _, _ = self.converted()
        # A WGC frame counter and elapsed capture clock are not video positions.
        manifest.pop("schema")
        manifest["session"] = "synthetic-wgc-material"
        manifest["frames"][0]["frame_id"] = 7654321
        manifest["frames"][0]["source_rgb_sha256"] = "a" * 64
        (session / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        initialized = review_original_frames.initialize(session, self.root / "capture-annotations.json", step=1)
        for row in initialized["frames"]:
            self.assertNotIn("frame_index", row)
            self.assertNotIn("source_rgb_sha256", row)
            self.assertNotIn("elapsed_s", row)
            self.assertIn("material_rgb_sha256", row)
            self.assertFalse(row["complete"])

    def test_source_changed_during_conversion_stays_explicitly_incomplete(self):
        video = self.make_video()
        original = VideoReader.seek
        mutated = [False]
        def read_and_change(reader, index):
            loaded = original(reader, index)
            if not mutated[0]:
                # The source writer appends harmless trailing data during this
                # synthetic run. Never alter a user's real source in a test.
                with video.open("ab") as stream:
                    stream.write(b"synthetic-source-writer-change")
                mutated[0] = True
            return loaded
        with patch.object(VideoReader, "seek", read_and_change):
            manifest = video_to_material.convert(video, self.root / "changed-material", interval_s=.2)
        self.assertFalse(manifest["valid"])
        self.assertFalse(manifest["source_stable_during_conversion"])
        self.assertTrue(any(row.get("stage") == "source_identity" for row in manifest["decode_errors"]))
        annotations = review_original_frames.initialize(
            self.root / "changed-material", self.root / "changed-annotations.json", step=1)
        self.assertFalse(annotations["source_complete"])

    @staticmethod
    def widgets(parent):
        for child in parent.winfo_children():
            yield child
            yield from AnnotationTimelineContracts.widgets(child)

    def test_ui_show_and_navigation_do_not_promote_proposals_or_round_truth(self):
        import tkinter as tk
        _, session, _, path, annotations = self.converted()
        annotations["frames"][0]["objects"] = [{
            "bbox": [4, 4, 12, 20], "rank": "Q", "physical_card_id": "synthetic-q-proposal",
            "label_provenance": "assistant_proposed", "rejection_reason": "synthetic fixture"}]
        path.write_text(json.dumps(annotations), encoding="utf-8")
        before = copy.deepcopy(annotations)
        alerts = []
        shown = []
        def drive(root):
            root.withdraw()
            root.update_idletasks()
            buttons = {w.cget("text"): w for w in self.widgets(root) if isinstance(w, tk.ttk.Button)}
            for widget in self.widgets(root):
                if isinstance(widget, tk.ttk.Label) and widget.cget("textvariable"):
                    shown.append(str(root.getvar(widget.cget("textvariable"))))
            buttons["本帧全部核对完成"].invoke()  # no reviewer: must refuse
            buttons["下一帧"].invoke()
            buttons["上一帧"].invoke()
            root.destroy()
        with (patch.object(tk.Tk, "mainloop", drive),
              patch("tkinter.messagebox.showinfo", side_effect=lambda *a, **kw: alerts.append(a))):
            review_original_frames.review_ui(session, path)
        self.assertTrue(alerts)
        self.assertTrue(any("原视频帧 0" in text for text in shown))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), before)

    def test_ui_round_truth_changes_only_on_explicit_complete_action(self):
        import tkinter as tk
        _, session, _, path, annotations = self.converted()
        initial = copy.deepcopy(annotations["frames"][0])
        def drive(root):
            root.withdraw()
            entries = [w for w in self.widgets(root) if type(w) is tk.ttk.Entry]
            reviewer_entry, round_entry = entries[0], entries[-1]
            reviewer_entry.insert(0, "synthetic-test-operator")
            round_entry.delete(0, "end")
            round_entry.insert(0, "7")
            # Merely filling inputs does not write or promote the original record.
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["frames"][0], initial)
            buttons = {w.cget("text"): w for w in self.widgets(root) if isinstance(w, tk.ttk.Button)}
            buttons["本帧全部核对完成"].invoke()
            root.destroy()
        with patch.object(tk.Tk, "mainloop", drive):
            review_original_frames.review_ui(session, path)
        row = json.loads(path.read_text(encoding="utf-8"))["frames"][0]
        self.assertEqual(row["round_id"], 7)
        self.assertEqual(row["round_provenance"], "human_reviewed")
        self.assertTrue(row["complete"])
        self.assertEqual(row["reviewed_by"], "synthetic-test-operator")
        for name in ("frame_index", "source_rgb_sha256", "material_rgb_sha256", "sha256"):
            self.assertEqual(row[name], initial[name])

    def test_ui_legacy_annotation_without_timeline_fields_does_not_invent_them(self):
        import tkinter as tk
        _, session, _, path, annotations = self.converted()
        annotations.pop("coordinate_space")
        annotations.pop("source_roi")
        for row in annotations["frames"]:
            for name in ("frame_index", "source_rgb_sha256", "material_rgb_sha256", "elapsed_s"):
                row.pop(name)
        path.write_text(json.dumps(annotations), encoding="utf-8")
        shown = []
        def drive(root):
            root.withdraw()
            for widget in self.widgets(root):
                if isinstance(widget, tk.ttk.Label) and widget.cget("textvariable"):
                    shown.append(str(root.getvar(widget.cget("textvariable"))))
            root.destroy()
        with patch.object(tk.Tk, "mainloop", drive):
            review_original_frames.review_ui(session, path)
        self.assertTrue(any("原视频帧 未知" in text for text in shown))
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), annotations)
