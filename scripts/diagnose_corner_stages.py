"""Freeze every raw/geometry/classifier stage, then attach GT for evaluation only."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from blackjack_lab.vision.contracts import ContractError
from blackjack_lab.vision.corner_diagnostics import trace_corner_stages
from blackjack_lab.vision.frame_annotations import material_identity
from blackjack_lab.vision.frame_evaluation import _match
from blackjack_lab.vision.image_io import load_image, rgb_to_bgr
from blackjack_lab.vision.model_adapter import TrainedModelAdapter, load_style
from blackjack_lab.vision.pipeline import recognize_loaded


def save_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, help="JSON list: session, annotations")
    parser.add_argument("--model", required=True)
    parser.add_argument("--layout", required=True)
    parser.add_argument("--corner-policy", help="Explicit version for frozen A/B")
    parser.add_argument("--focus", help="Existing missed-corner diagnostic; no invented targets")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    out = Path(args.output)
    if out.exists():
        raise ContractError("Diagnostic output already exists; originals must be preserved")
    layout = load_style(args.layout)
    adapter = TrainedModelAdapter(args.model, style_id=layout.style_id,
                                  corner_policy_version=args.corner_policy)
    cases_bytes = Path(args.cases).read_bytes()
    cases = json.loads(cases_bytes)
    focus_bytes = Path(args.focus).read_bytes() if args.focus else b""
    focus = json.loads(focus_bytes).get("rows", []) if focus_bytes else []
    out.mkdir(parents=True)
    (out / "originals").mkdir()
    (out / "contexts").mkdir()
    (out / "inputs").mkdir()
    totals, frames, targets, inputs = Counter(), [], [], []
    for case_index, case in enumerate(cases):
        session = Path(case["session"])
        manifest_bytes = (session / "manifest.json").read_bytes()
        manifest = json.loads(manifest_bytes)
        annotation_bytes = Path(case["annotations"]).read_bytes()
        annotation = json.loads(annotation_bytes)
        source_sha = material_identity(session, manifest)
        if annotation.get("source_sha256") != source_sha:
            raise ContractError("Annotation and source identity differ")
        (out / "inputs" / f"annotations-{case_index}.json").write_bytes(annotation_bytes)
        (out / "inputs" / f"manifest-{case_index}.json").write_bytes(manifest_bytes)
        inputs.append({"case": case, "source_sha256": source_sha,
                       "annotation_sha256": hashlib.sha256(annotation_bytes).hexdigest(),
                       "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()})
        known = {f["file"] for f in manifest["frames"]}
        for frame in annotation["frames"]:
            path = (session / "frames" / frame["file"]).resolve()
            if (frame["file"] not in known or
                    not path.is_relative_to((session / "frames").resolve())):
                raise ContractError("Frame absent from manifest or escapes source directory")
            loaded = load_image(path, layout)
            if loaded.sha256 != frame.get("sha256"):
                raise ContractError("Original frame bytes differ from human annotation")
            trace = trace_corner_stages(loaded, adapter, source_sha)
            # Compare traces to the real entry point before looking at answers.
            actual = recognize_loaded(loaded, layout=layout, adapter=adapter)
            kept = [r for r in trace["candidates"] if r["selection"]["keep"]]
            expected = [(r["bbox"], r["production_prediction"]["rank"]
                         if r["production_prediction"]["accepted"] else None) for r in kept]
            observed = [([o.bbox[k] for k in ("x", "y", "w", "h")], o.accepted_rank())
                        for o in actual.observations]
            if expected != observed:
                raise ContractError("Diagnostic path differs from production outputs")
            objects = frame.get("objects", [])
            assignments, _ = _match(objects, trace["candidates"], .30)
            trace.update(file=frame["file"], source_index=case.get("source_index", case_index),
                         original_path=str(path), production_entry_verified=True,
                         evaluation_matches=[{"truth": obj, "raw_candidate_index": assignments.get(i)}
                                             for i, obj in enumerate(objects)])
            original = out / "originals" / (loaded.sha256 + ".png")
            if not original.exists():
                shutil.copyfile(path, original)
            totals["frames"] += 1
            totals["raw"] += len(trace["candidates"])
            totals["retained"] += len(kept)
            totals["excluded"] += len(trace["candidates"])-len(kept)
            totals["geometry_review"] += len(actual.geometry_review)
            for target in focus:
                if (target["source_index"] != trace["source_index"] or
                        target["frame"] != frame["file"]):
                    continue
                matches = [r for r in trace["candidates"] if r["bbox"] == target["nearest_raw_bbox"]]
                truth_matches = [o for o in objects if o["bbox"] == target["bbox"] and o["rank"] == target["truth"]]
                if len(matches) != 1 or len(truth_matches) != 1:
                    raise ContractError("Existing focus target not reproduced from frozen originals")
                row = {**matches[0], "evaluation_truth": truth_matches[0],
                       "frame": frame["file"], "original": str(original.resolve()),
                       "previous_diagnostic": target}
                from blackjack_lab.vision.deps import load_cv2
                bgr = rgb_to_bgr(loaded)
                x, y, w, h = row["bbox"]
                left, top = max(0, x-65), max(0, y-55)
                crop = bgr[top:min(loaded.height,y+h+70), left:min(loaded.width,x+w+85)].copy()
                cv2 = load_cv2()
                cv2.rectangle(crop, (x-left,y-top), (x+w-left,y+h-top), (0,180,255), 1)
                ok, encoded = cv2.imencode(".png", crop)
                if not ok:
                    raise ContractError("Cannot save original context evidence")
                context = out / "contexts" / (row["candidate_id"] + ".png")
                context.write_bytes(encoded.tobytes())
                row["context"] = str(context.resolve())
                targets.append(row)
            frames.append(trace)
    if len(targets) != len(focus):
        raise ContractError("Not all existing focus targets were reproduced")
    report = {"schema": "corner-stage-diagnostic-1", "valid": True,
              "counts": dict(totals), "focus_count": len(targets),
              "inputs": inputs, "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
              "focus_input_sha256": hashlib.sha256(focus_bytes).hexdigest() if focus_bytes else None,
              "model_id": adapter.model_id, "model_digest": adapter.digest,
              "extraction_version": adapter.extraction_version,
              "thresholds": {k: getattr(adapter.model,k) for k in ("k","min_vote","min_margin","min_similarity")},
              "weights_changed": False, "gt_used_for_recognition": False,
              "independent_acceptance": False, "writes_ledger": False,
              "frames": frames, "focus": targets}
    save_json(out / "diagnostic.json", report)
    lines = ["# 逐阶段诊断", "", "排除项的分类结果仅为离线反事实，不是运行输出或识别成绩。", "",
             "|序号|原帧 / 位置|人审答案|几何原因|上 / 下距离|反事实分类|证据|", "|---|---|---|---|---|---|---|"]
    for i, row in enumerate(targets, 1):
        guess = row["counterfactual_prediction"] or row["production_prediction"]
        selection = row["selection"]
        lines.append(f"|{i}|{row['frame']} {row['bbox']}|{row['evaluation_truth']['rank']}|"
                     f"{selection['reason']}|{selection.get('edge_above_px')} / {selection.get('edge_below_px')}|"
                     f"{guess['rank'] if guess['accepted'] else '拒识'} {guess['rejection_reason']}|"
                     f"[上下文](<{row['context']}>) / [原图](<{row['original']}>)|")
    (out / "逐项原因.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    print(json.dumps({"counts": dict(totals), "focus_count":len(targets), "model_id":adapter.model_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
