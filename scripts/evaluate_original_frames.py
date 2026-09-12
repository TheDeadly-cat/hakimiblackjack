"""Evaluate an explicit model on reviewed original frames via the shared pipeline.

This is sampled-frame recognition evidence, not complete-video/ledger acceptance.
Local originals, annotations and model files are read-only; output never overwrites.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.frame_annotations import material_identity
from blackjack_lab.vision.frame_evaluation import evaluate_annotated_frames
from blackjack_lab.vision.image_io import load_image
from blackjack_lab.vision.live_input import LiveStyle, capture_crop_pixels
from blackjack_lab.vision.model_adapter import TrainedModelAdapter, load_style, prepare_style_image
from blackjack_lab.vision.pipeline import recognize_loaded


def crop_offset(width, height, layout):
    """Inverse translation of table_crop.apply_layout_crops, without resizing."""
    if isinstance(layout, LiveStyle):
        crop = capture_crop_pixels(layout, width, height)
        return (crop[0], crop[1]) if crop else (0, 0)
    offset_x = offset_y = 0
    if (layout.source_crop and layout.source_frame_width and layout.source_frame_height
            and (width, height) == (layout.source_frame_width, layout.source_frame_height)):
        x, y, width, height = layout.source_crop
        offset_x += x
        offset_y += y
    if layout.felt_crop and (width, height) != (layout.canvas_width, layout.canvas_height):
        x, y, w, h = layout.felt_crop
        if x + w <= width and y + h <= height:
            offset_x += x
            offset_y += y
    return offset_x, offset_y


def make_predictor(session, annotation, manifest, layout, adapter):
    frames_root = (Path(session) / "frames").resolve()
    known = {frame["file"] for frame in manifest.get("frames", [])}

    def predict(frame):
        if frame["file"] not in known:
            raise ContractError("Annotated frame absent from frozen source manifest")
        path = (frames_root / frame["file"]).resolve()
        if not path.is_relative_to(frames_root):
            raise ContractError("Frame path escapes source directory")
        loaded = load_image(path, None if isinstance(layout, LiveStyle) else layout)
        if not frame.get("sha256") or loaded.sha256 != frame["sha256"]:
            raise ContractError("Original frame SHA256 differs from annotation")
        for obj in frame.get("objects", []):
            bx, by, bw, bh = obj["bbox"]
            if bx + bw > loaded.width or by + bh > loaded.height:
                raise ContractError("Truth box lies outside the original frame")
        x, y = crop_offset(loaded.width, loaded.height, layout)
        if isinstance(layout, LiveStyle):
            resolved_layout, canvas = prepare_style_image(loaded, layout)
            result = recognize_loaded(canvas, layout=resolved_layout, adapter=adapter)
        else:
            result = recognize_loaded(loaded, layout=layout, adapter=adapter)
        return [{"observation_id": obs.observation_id,
                 "bbox": {**obs.bbox, "x": obs.bbox["x"] + x, "y": obs.bbox["y"] + y},
                 "rank": obs.accepted_rank(), "region_id": obs.region_id,
                 "reject_reason": obs.reject_reason,
                 "model_id": obs.model_id, "model_digest": obs.model_digest}
                for obs in result.observations]
    return predict


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="冻结的manifest/frames会话目录")
    parser.add_argument("annotations", help="original-frame-annotations-1原帧标注")
    parser.add_argument("--model", required=True, help="明确选择本地模型目录")
    parser.add_argument("--corner-policy", help="明确指定几何版本，用于固定模型的 A/B")
    parser.add_argument("--layout", required=True, help="明确选择静态布局或normalized实时样式JSON")
    parser.add_argument("--style-id", required=True, help="必须与布局和模型一致")
    parser.add_argument("--output", required=True, help="新的JSON结果文件，拒绝覆盖")
    parser.add_argument("--iou-threshold", type=float, default=0.30)
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists():
        raise ContractError("Output already exists; choose a new evaluation report")
    annotation_bytes = Path(args.annotations).read_bytes()
    annotation = json.loads(annotation_bytes)
    manifest_bytes = (Path(args.session) / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    layout = load_style(Path(args.layout))
    if layout.style_id != args.style_id:
        raise ContractError("Explicit style does not match layout")
    adapter = TrainedModelAdapter(args.model, style_id=args.style_id,
                                  corner_policy_version=args.corner_policy)
    errors = []
    try:
        if material_identity(args.session, manifest) != annotation.get("source_sha256"):
            errors.append("annotation_source_identity_mismatch")
    except (OSError, ValueError) as exc:
        errors.append(f"source_identity_unverified:{type(exc).__name__}:{exc}")
    report = evaluate_annotated_frames(annotation,
        make_predictor(args.session, annotation, manifest, layout, adapter),
        iou_threshold=args.iou_threshold, source_manifest=manifest, source_errors=errors)
    report.update({"model_id": adapter.model_id, "model_digest": adapter.digest,
                   "feature_version": adapter.feature_version, "training_digest": adapter.training_digest,
                   "extraction_version": adapter.extraction_version, "style_id": args.style_id,
                   "layout": layout.as_dict() if isinstance(layout, LiveStyle) else asdict(layout),
                   "prediction_entry": "pipeline.recognize_loaded(adapter)",
                   "candidate_coordinates": "translated_back_to_original_frame",
                   "annotation_sha256": hashlib.sha256(annotation_bytes).hexdigest(),
                   "source_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                   "independent_test_source_verified": False,
                   "source_independence_note": "A new directory or source SHA alone does not prove the recording was untouched during model selection."})
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in (
        "valid", "incomplete_reasons", "n_annotation_frames", "n_truth_identifiable",
        "correct", "wrong", "missed_extraction", "rejected", "junk_as_rank", "duplicate_accepted",
        "all_output_precision", "sampled_frame_end_to_end_recall")}, ensure_ascii=False, indent=2))
    print(f"报告 {output}")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
