"""Read-only video replay against sampled human truth; no ledger writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.model_adapter import TrainedModelAdapter, load_style, prepare_style_image
from blackjack_lab.vision.pipeline import recognize_loaded
from blackjack_lab.vision.video_io import VideoReader
from blackjack_lab.vision.video_timeline_evaluation import evaluate_timeline, style_offset


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video")
    parser.add_argument("annotations", help="original-frame-annotations-1，含frame_index/source_rgb_sha256")
    parser.add_argument("--model", required=True)
    parser.add_argument("--layout", required=True, help="独立选择的静态/normalized样式JSON")
    parser.add_argument("--style-id", required=True)
    parser.add_argument("--first-frame", type=int, required=True)
    parser.add_argument("--last-frame", type=int, required=True)
    parser.add_argument("--round-bindings", help="可选的独立operator-round-bindings-1 JSON；不会从真值生成")
    parser.add_argument("--iou-threshold", type=float, default=0.30)
    parser.add_argument("--output", required=True, help="新的本地JSON，不覆盖旧报告")
    args = parser.parse_args(argv)
    output = Path(args.output)
    if output.exists():
        raise ContractError("Output exists; choose a new report")
    annotation_bytes = Path(args.annotations).read_bytes()
    style_bytes = Path(args.layout).read_bytes()
    annotation = json.loads(annotation_bytes)
    binding_bytes = Path(args.round_bindings).read_bytes() if args.round_bindings else None
    bindings = json.loads(binding_bytes) if binding_bytes else None
    style = load_style(args.layout)
    if style.style_id != args.style_id:
        raise ContractError("Selected style ID differs from style file")
    adapter = TrainedModelAdapter(args.model, style_id=args.style_id)

    def predict(loaded):
        layout, canvas = prepare_style_image(loaded, style)
        result = recognize_loaded(canvas, layout=layout, adapter=adapter)
        return result, style_offset(loaded, style)

    with VideoReader(args.video) as reader:
        if reader.asset.fps <= 0 or reader.asset.frame_count <= 0:
            raise ContractError("Timeline requires measured video FPS and frame count")
        if args.last_frame >= reader.asset.frame_count:
            raise ContractError("Requested interval exceeds declared source frame count")
        report = evaluate_timeline(annotation, source_sha256=reader.asset.sha256,
            first_frame=args.first_frame, last_frame=args.last_frame, read_frame=reader.seek,
            recognize_frame=predict, time_ms=reader.asset.time_ms, round_bindings=bindings,
            iou_threshold=args.iou_threshold)
    report.update(model_id=adapter.model_id, model_digest=adapter.digest,
        style_id=style.style_id, style_sha256=hashlib.sha256(style_bytes).hexdigest(),
        feature_version=adapter.feature_version, extraction_version=adapter.extraction_version,
        training_digest=adapter.training_digest, annotation_sha256=hashlib.sha256(annotation_bytes).hexdigest(),
        operator_binding_sha256=hashlib.sha256(binding_bytes).hexdigest() if binding_bytes else None,
        prediction_entry="VideoReader -> pipeline.recognize_loaded -> FrameTracker")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    print(json.dumps({key: report[key] for key in ("valid", "scope", "incomplete_reasons",
        "expected_decode_frames", "n_annotation_frames", "n_truth_physical_cards", "metrics")},
        ensure_ascii=False, indent=2))
    print(f"报告 {output}")
    return 0 if report["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
