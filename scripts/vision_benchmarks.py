# -*- coding: utf-8 -*-
"""对 synthetic-felt-v1 holdout 报告原始预测，不含人工纠正。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.vision.contracts import FACE_SHOWN, RANKS_13
from blackjack_lab.vision.holdout import (
    HOLDOUT_SEED, R3_EMPTY, R3_IDENTIFIABLE_FRAMES, R3_INTERFERENCE,
    R3_PRIMARY_PER_RANK, write_bundle, write_holdout_bundle,
)
from blackjack_lab.vision.image_io import load_image
from blackjack_lab.vision.pipeline import recognize_loaded
from blackjack_lab.vision.rank_recognizer import load_template_bank


def _iou(a: dict, b: dict) -> float:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ix1, iy1 = max(a["x"], b["x"]), max(a["y"], b["y"])
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


def evaluate(bundle: Path, *, error_dir: Path | None = None) -> dict:
    manifest = json.loads((bundle / "holdout" / "manifest.json").read_text(encoding="utf-8"))
    templates = bundle / "templates"
    bank = load_template_bank(templates)
    identifiable_total = 0
    identifiable_correct = 0
    identifiable_reject = 0
    identifiable_wrong = 0
    identifiable_miss = 0
    distractor_false = 0
    distractor_frames = 0
    per_rank = {r: Counter() for r in RANKS_13}
    false_from_empty = 0
    frames = 0
    samples = []
    started = time.perf_counter()

    for rec in manifest["records"]:
        frames += 1
        path = bundle / "holdout" / rec["file"]
        loaded = load_image(path)
        result = recognize_loaded(loaded, bank=bank, templates_dir=templates)
        labels = rec.get("labels") or []
        kind = rec.get("kind")
        preds = list(result.observations)
        used = set()
        if kind == "empty":
            distractor_frames += 1
            extra = len(preds)
            distractor_false += extra
            false_from_empty += extra
            continue
        if kind in {"back", "occluded", "unsupported"}:
            distractor_frames += 1
            for obs in preds:
                if obs.face_state_candidate == FACE_SHOWN and obs.accepted_rank():
                    distractor_false += 1
                    if error_dir and len(samples) < 24:
                        samples.append({"kind": kind, "file": rec["file"],
                                        "accepted": obs.accepted_rank()})
            continue
        truth = [lab for lab in labels if lab.get("identifiable")]
        identifiable_total += len(truth)
        for lab in truth:
            best_i, best_iou = None, 0.0
            for i, obs in enumerate(preds):
                if i in used:
                    continue
                iou = _iou(lab["bbox"], obs.bbox)
                if iou > best_iou:
                    best_i, best_iou = i, iou
            rank = lab["rank"]
            if best_i is None or best_iou < 0.3:
                identifiable_miss += 1
                per_rank[rank]["miss"] += 1
                if error_dir and len(samples) < 40:
                    samples.append({"kind": "miss", "file": rec["file"], "truth": rank})
                continue
            used.add(best_i)
            obs = preds[best_i]
            accepted = obs.accepted_rank()
            if accepted == rank:
                identifiable_correct += 1
                per_rank[rank]["correct"] += 1
            elif accepted is None:
                identifiable_reject += 1
                per_rank[rank]["reject"] += 1
                if error_dir and len(samples) < 40:
                    samples.append({"kind": "reject", "file": rec["file"], "truth": rank,
                                    "reason": obs.reject_reason})
            else:
                identifiable_wrong += 1
                per_rank[rank]["wrong"] += 1
                per_rank[rank][f"as_{accepted}"] += 1
                if error_dir and len(samples) < 40:
                    samples.append({"kind": "wrong", "file": rec["file"],
                                    "truth": rank, "accepted": accepted})
        for i, obs in enumerate(preds):
            if i not in used and obs.accepted_rank():
                distractor_false += 1

    elapsed = time.perf_counter() - started
    accepted = identifiable_correct + identifiable_wrong
    per_rank_totals = {
        rank: sum(per_rank[rank][k] for k in ("correct", "reject", "miss", "wrong"))
        for rank in RANKS_13
    }
    accuracy = identifiable_correct / accepted if accepted else None
    recall = identifiable_correct / identifiable_total if identifiable_total else None
    gates = {
        "min_identifiable_instances": {"min": 1000, "actual": identifiable_total,
                                       "met": identifiable_total >= 1000},
        "min_per_rank": {"min": 50, "actual": per_rank_totals,
                         "met": all(n >= 50 for n in per_rank_totals.values())},
        "min_interference_frames": {"min": 200, "actual": distractor_frames,
                                    "met": distractor_frames >= 200},
        "accepted_rank_accuracy": {"min": 0.99, "actual": accuracy,
                                   "met": accuracy is not None and accuracy >= 0.99},
        "suggested_e2e_recall": {"min": 0.95, "actual": recall,
                                 "met": recall is not None and recall >= 0.95,
                                 "note": "建议门槛，不是取消人工确认的依据"},
    }
    report = {
        "style_id": "synthetic-felt-v1",
        "platform_claim": "none",
        "split": f"holdout-seed-{HOLDOUT_SEED}",
        "human_corrected_not_counted": True,
        "templates_not_taken_from_holdout_images": True,
        "match_score_is_not_probability": True,
        "n_frames": frames,
        "n_identifiable": identifiable_total,
        "n_distractor_frames": distractor_frames,
        "raw_rank_accuracy_among_accepted": accuracy,
        "end_to_end_identifiable_recall": recall,
        "identifiable_miss_rate": identifiable_miss / identifiable_total if identifiable_total else None,
        "identifiable_reject_rate": identifiable_reject / identifiable_total if identifiable_total else None,
        "identifiable_wrong_accept": identifiable_wrong,
        "false_detections_accepted": distractor_false,
        "false_detections_per_frame": distractor_false / frames if frames else None,
        "false_from_empty_frames": false_from_empty,
        "per_rank": {k: dict(v) for k, v in per_rank.items()},
        "per_rank_totals": per_rank_totals,
        "recognize_seconds": elapsed,
        "recognize_seconds_per_frame": elapsed / frames if frames else None,
        "r3_plan": {
            "identifiable_frames": R3_IDENTIFIABLE_FRAMES,
            "primary_per_rank": R3_PRIMARY_PER_RANK,
            "interference": R3_INTERFERENCE,
            "empty": R3_EMPTY,
        },
        "suggested_gates": gates,
        "size_and_accuracy_gates_met": all(
            gates[name]["met"] for name in (
                "min_identifiable_instances", "min_per_rank",
                "min_interference_frames", "accepted_rank_accuracy")),
        "note": "仅对自建 synthetic-felt-v1 有效；不能外推真实平台。匹配度不是正确概率。",
    }
    if error_dir is not None:
        error_dir.mkdir(parents=True, exist_ok=True)
        (error_dir / "samples.json").write_text(
            json.dumps(samples, ensure_ascii=False, indent=2), encoding="utf-8")
        report["error_samples"] = len(samples)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="识牌 holdout 原始预测统计")
    parser.add_argument("--bundle", type=Path, default=ROOT / "fixtures" / "vision" / "synthetic-v1")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--json", type=Path)
    parser.add_argument("--errors", type=Path)
    args = parser.parse_args()
    if args.rebuild or not (args.bundle / "templates" / "A.png").is_file():
        write_bundle(args.bundle)
    if args.rebuild or not (args.bundle / "holdout" / "manifest.json").is_file():
        write_holdout_bundle(args.bundle)
    report = evaluate(args.bundle, error_dir=args.errors)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
