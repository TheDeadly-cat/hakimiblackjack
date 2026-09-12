# -*- coding: utf-8 -*-
"""Train explicit local queues; holdout never selects thresholds.

Reports evaluate extracted crops, not full-video acceptance. All previous input
queues, reports and models are preserved. Each call writes a new run directory.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.vision.contracts import ContractError, RANKS_13  # noqa: E402
from blackjack_lab.vision.glyph_dataset import (  # noqa: E402
    assert_no_leakage, group_summary, independent_groups, label_counts,
    labeled_only, load_queue, sample_origin_id,
)
from blackjack_lab.vision.rank_classifier import (  # noqa: E402
    MIN_MARGIN, MIN_VOTE, RankClassifier, evaluate_items, pairs_from_items,
)

THRESHOLD_GRID = {"min_vote": (2, 3), "min_margin": (0.02, 0.04, 0.06, 0.08),
                  "min_similarity": (0.75, 0.80, 0.85, 0.90)}
DEFAULT_SIMILARITY = 0.80


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _decoded_mask_digest(path):
    """Re-encoding or nonzero intensity changes cannot create a new sample."""
    from blackjack_lab.vision.deps import cv2_available, load_cv2, load_numpy
    if not cv2_available():
        return ""  # The actual training/evaluation entry requires this decoder.
    cv2, np = load_cv2(), load_numpy()
    try:
        mask = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
    except cv2.error:
        return ""  # Empty/corrupt holdout files must reach the invalid-input report.
    if mask is None:
        return ""  # evaluate_items retains the unreadable item as invalid.
    binary = np.ascontiguousarray((mask > 0).astype(np.uint8))
    digest = hashlib.sha256(json.dumps(list(binary.shape)).encode("ascii") + b"\0")
    digest.update(binary.tobytes())
    return digest.hexdigest()


def _load_inputs(args):
    """Resolve each queue's files without changing its JSONL on disk."""
    external = bool(args.validation_queue or args.holdout_queue)
    specs = []
    if args.queue:
        specs.append((args.queue, "train" if external else None))
    specs.extend((path, "train") for path in args.train_queue)
    specs.extend((path, "validation") for path in args.validation_queue)
    specs.extend((path, "holdout") for path in args.holdout_queue)
    items, sources, seen_crops = [], [], set()
    for raw_path, split in specs:
        path = Path(raw_path).resolve()
        queue_path = path / "queue.jsonl" if path.is_dir() else path
        root = queue_path.parent
        loaded = load_queue(queue_path)
        for original in loaded:
            identity = (split or original.split, original.crop_id)
            if identity in seen_crops:
                raise ContractError(f"同一裁片重复出现在输入队列：{original.crop_id}")
            seen_crops.add(identity)
            crop = (root / original.crop_file).resolve() if original.crop_file else None
            mask = (root / original.mask_file).resolve() if original.mask_file else None
            crop_sha = _sha256(crop) if crop is not None and crop.is_file() else ""
            mask_sha = _sha256(mask) if mask is not None and mask.is_file() else ""
            mask_content_sha = _decoded_mask_digest(mask) if mask_sha else ""
            if original.crop_sha256 and crop_sha and original.crop_sha256.lower() != crop_sha:
                raise ContractError(f"裁片内容与已记录 SHA256 不同：{original.crop_id}")
            if original.mask_sha256 and mask_sha and original.mask_sha256.lower() != mask_sha:
                raise ContractError(f"训练掩膜内容与已记录 SHA256 不同：{original.crop_id}")
            if (original.mask_content_sha256 and mask_content_sha
                    and original.mask_content_sha256.lower() != mask_content_sha):
                raise ContractError(f"训练掩膜像素与已记录 SHA256 不同：{original.crop_id}")
            items.append(replace(original, split=split or original.split,
                                 crop_file=str(crop) if crop else "",
                                 mask_file=str(mask) if mask else "",
                                 crop_sha256=crop_sha or original.crop_sha256,
                                 mask_sha256=mask_sha or original.mask_sha256,
                                 mask_content_sha256=mask_content_sha or original.mask_content_sha256))
        sources.append({"queue": str(queue_path), "queue_sha256": _sha256(queue_path),
                        "assigned_split": split or "preserved", "n_items": len(loaded)})
        split_path = root / "split.json"
        if split_path.is_file():
            metadata = json.loads(split_path.read_text(encoding="utf-8"))
            sources[-1]["preparation"] = {key: metadata[key] for key in (
                "valid", "source_complete", "source_decode_errors", "source_truncated",
                "unreadable_frames", "truncated_candidates") if key in metadata}
    assert_no_leakage(items)
    return items, sources


def _training_digest(items):
    """Freeze exact training masks, origin identities and labels, not holdout."""
    rows = []
    for item in items:
        path = Path(item.mask_file)
        rows.append({"crop_id": item.crop_id, "origin_id": sample_origin_id(item),
                     "session": item.session, "round_id": item.round_id,
                     "source_sha256": item.source_sha256, "frame_sha256": item.frame_sha256,
                     "crop_sha256": item.crop_sha256,
                     "mask_sha256": _sha256(path) if path.is_file() else None,
                     "label": item.label, "label_provenance": item.label_provenance})
    rows.sort(key=lambda row: (row["session"], row["round_id"], row["crop_id"]))
    frozen = json.dumps(rows, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(frozen.encode("utf-8")).hexdigest()


def _validation_split(train):
    """Prefer whole sessions; preserve transitive crop/card/frame identities."""
    groups = independent_groups(train)
    sessions = sorted({item.session for item in train})
    mode = "connected_round_groups_same_session"
    if len(sessions) > 1:
        parents = {session: session for session in sessions}

        def find(session):
            while parents[session] != session:
                parents[session] = parents[parents[session]]
                session = parents[session]
            return session

        source_sessions = {}
        for item in train:
            if item.source_sha256:
                other = source_sessions.setdefault(item.source_sha256, item.session)
                parents[find(item.session)] = find(other)
        for group in groups:
            first = train[group[0]].session
            for index in group[1:]:
                parents[find(train[index].session)] = find(first)
        buckets = {}
        for index, item in enumerate(train):
            buckets.setdefault(find(item.session), []).append(index)
        if len(buckets) > 1:
            groups = [buckets[key] for key in sorted(buckets)]
            mode = "whole_session_source_groups"
        else:
            mode = "connected_round_groups_same_source"
    if len(groups) < 5 and mode != "whole_session_source_groups":
        return train, [], {"mode": mode, "reason": "fewer_than_five_independent_groups"}
    n_val = max(1, round(len(groups) * 0.2))
    val_indices = {index for group in groups[-n_val:] for index in group}
    fit_items = [item for index, item in enumerate(train) if index not in val_indices]
    val_items = [item for index, item in enumerate(train) if index in val_indices]
    assert_no_leakage([replace(item, split="train") for item in fit_items] +
                      [replace(item, split="validation") for item in val_items])
    return fit_items, val_items, {"mode": mode}


def _tune_on_train(train, root, validation=None, *, style_id="navy-live-felt-v1"):
    if validation:
        fit_items, val_items, split_info = train, validation, {"mode": "explicit_validation_queue"}
    else:
        fit_items, val_items, split_info = _validation_split(train)
    audit = {**split_info, "fit": group_summary(fit_items),
             "validation": group_summary(val_items), "threshold_grid": THRESHOLD_GRID,
             "objective": "all_output_precision>=0.90, then correct-5*(wrong+junk_as_rank)",
             "holdout_used_for_selection": False, "trials": []}
    default = {"min_vote": MIN_VOTE, "min_margin": MIN_MARGIN,
               "min_similarity": DEFAULT_SIMILARITY}
    if len(fit_items) < 8 or len(val_items) < 4:
        audit["status"] = "defaults_insufficient_internal_validation"
        return default, audit
    pairs = pairs_from_items(fit_items, root)
    model = RankClassifier(**default).fit(pairs, style_id=style_id)
    best, best_score = default, float("-inf")
    for vote in THRESHOLD_GRID["min_vote"]:
        for margin in THRESHOLD_GRID["min_margin"]:
            for similarity in THRESHOLD_GRID["min_similarity"]:
                thresholds = {"min_vote": vote, "min_margin": margin, "min_similarity": similarity}
                model.min_vote, model.min_margin, model.min_similarity = vote, margin, similarity
                report = evaluate_items(model, val_items, root)
                if not report["valid"]:
                    raise ContractError("内部验证存在缺失/不可读文件，拒绝选择门槛")
                precision = report["all_output_precision"]
                score = (-1.0 if precision is None else
                         -100.0 + precision if precision < 0.90 else
                         float(report["accepted_correct"]) - 5.0 * (
                             report["accepted_wrong"] + report["junk_as_rank"]))
                audit["trials"].append({**thresholds, "score": score,
                    "all_output_precision": precision,
                    "extracted_identifiable_recall": report["extracted_identifiable_recall"],
                    "accepted_correct": report["accepted_correct"],
                    "accepted_wrong": report["accepted_wrong"], "junk_as_rank": report["junk_as_rank"]})
                if score > best_score:
                    best, best_score = thresholds, score
    audit.update(status="selected_on_internal_validation", selected_score=best_score)
    return best, audit


def _print_report(title, report):
    print(f"\n{title}  valid={report['valid']}  缺失/不可读={report['n_invalid']}")
    print(f"  已标注 {report['n_labeled']}  点数 {report['n_identifiable']}  junk {report['n_junk']}")
    print(f"  全输出precision {report['all_output_precision']}  "
          f"已提取可辨认裁片recall {report['extracted_identifiable_recall']}")
    print(f"  点数条件准确率 {report['raw_rank_accuracy_among_accepted']}  "
          f"正确 {report['accepted_correct']}  错认 {report['accepted_wrong']}  "
          f"拒识 {report['rejected_identifiable']}  junk误收 {report['junk_as_rank']}")


def _final_source_status(train, validation, holdout):
    selection = list(train) + list(validation)
    reasons = []
    if not holdout:
        reasons.append("no_labeled_holdout")
    if any(not item.source_sha256 for item in selection + list(holdout)):
        reasons.append("source_sha256_missing")
    if {i.source_sha256 for i in selection if i.source_sha256} & {
            i.source_sha256 for i in holdout if i.source_sha256}:
        reasons.append("source_shared_with_training_or_validation")
    if {i.session for i in selection} & {i.session for i in holdout}:
        reasons.append("session_shared_with_training_or_validation")
    return {"source_independent": not reasons, "reasons": reasons,
            "independent_human_review_verified": False, "end_to_end_evaluated": False,
            "final_acceptance_eligible": False,
            "note": "Crop evaluation cannot establish detector-blind original-frame truth or independent human review."}


def main(argv=None):
    parser = argparse.ArgumentParser(description="多会话点数训练与可审计裁片评测；不会覆盖旧结果")
    parser.add_argument("queue", nargs="?", help="原队列；没有外部验证/留出时保留split")
    parser.add_argument("--train-queue", action="append", default=[], help="可重复：指定训练队列")
    parser.add_argument("--validation-queue", action="append", default=[], help="可重复：仅用于选门槛")
    parser.add_argument("--holdout-queue", action="append", default=[], help="可重复：不参与选门槛")
    parser.add_argument("--holdout-role", choices=("development", "final"), default="development")
    parser.add_argument("--output", help="全新运行目录；默认主队列/training-runs/<唯一ID>")
    parser.add_argument("--model", help="全新模型输出目录（拒绝覆盖）")
    parser.add_argument("--report", help="全新报告文件（拒绝覆盖）")
    parser.add_argument("--style-id", default="navy-live-felt-v1")
    parser.add_argument("--min-per-rank", type=int, default=3)
    args = parser.parse_args(argv)
    if not args.queue and not args.train_queue:
        parser.error("至少提供 queue 或 --train-queue")
    primary = Path(args.queue or args.train_queue[0]).resolve()
    if primary.is_file():
        primary = primary.parent
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid4().hex[:8]
    run_dir = Path(args.output).resolve() if args.output else primary / "training-runs" / run_id
    model_dir = Path(args.model).resolve() if args.model else run_dir / "model"
    report_path = Path(args.report).resolve() if args.report else run_dir / "holdout-report.json"
    if run_dir.exists() or model_dir.exists() or report_path.exists():
        print("输出目录、模型或报告已存在，拒绝覆盖；请使用新的输出路径。")
        return 2
    all_items, source_queues = _load_inputs(args)
    incomplete_sources = [source["queue"] for source in source_queues if (
        source.get("preparation", {}).get("valid") is False or
        source.get("preparation", {}).get("source_complete") is False or
        any(source.get("preparation", {}).get(key) for key in (
            "source_decode_errors", "source_truncated", "unreadable_frames", "truncated_candidates")))]
    train = labeled_only(all_items, split="train")
    validation = labeled_only(all_items, split="validation")
    holdout = labeled_only(all_items, split="holdout")
    if not train:
        print("训练集没有标签。先运行 scripts/label_glyphs.py")
        return 1
    if args.validation_queue and not validation:
        raise ContractError("指定验证队列没有标签，不能悄悄改用其他验证划分")
    counts = label_counts(train)
    print(f"训练标注 {counts}")
    insufficient = [rank for rank in RANKS_13 if counts.get(rank, 0) < args.min_per_rank]
    if insufficient:
        print(f"点数样本不足 {args.min_per_rank}：{insufficient}；不能外推为13点可靠识别。")
    source_status = _final_source_status(train, validation, holdout)
    if args.holdout_role == "final" and not source_status["source_independent"]:
        raise ContractError(f"final 留出必须整源独立：{source_status['reasons']}")
    thresholds, tuning = _tune_on_train(train, Path(), validation, style_id=args.style_id)
    print(f"训练内部选择门槛 {thresholds}（未看留出集）")
    digest = _training_digest(train)
    model = RankClassifier(**thresholds).fit(
        pairs_from_items(train, Path()), origin=f"local labeled glyphs; run {run_id}",
        style_id=args.style_id, training_digest=digest)
    train_report = evaluate_items(model, train, Path())
    _print_report("训练自检（不是验收）", train_report)
    holdout_report = evaluate_items(model, holdout, Path()) if holdout else None
    if holdout_report:
        _print_report("留出裁片原始预测（不是完整视频端到端验收）", holdout_report)
    report = {
        "schema": "0.3e-training-run-2", "run_id": run_id, "source_queues": source_queues,
        "model": str(model_dir), "model_id": model.model_id, "style_id": model.style_id,
        "training_digest": digest, "model_training_digest": model.training_digest,
        "n_train_labeled": len(train), "n_validation_labeled": len(validation),
        "n_holdout_labeled": len(holdout), "train_label_counts": counts,
        "holdout_label_counts": label_counts(holdout), "insufficient_training_ranks": insufficient,
        "groups": {"train": group_summary(train), "validation": group_summary(validation),
                   "holdout": group_summary(holdout)},
        "thresholds": thresholds, "tuning": tuning,
        "train_self_check": {key: value for key, value in train_report.items() if key != "rows"},
        "holdout": holdout_report, "holdout_role": args.holdout_role,
        "evaluation_complete": bool(holdout_report and holdout_report["valid"] and not incomplete_sources),
        "incomplete_source_queues": incomplete_sources,
        "final_source_status": source_status, "auto_confirm_enabled": False,
        "note": "反复用于开发的旧63条仍属开发集；只评已提取裁片。最终验收另需独立原帧人工真值与完整事件评测。",
    }
    run_dir.mkdir(parents=True, exist_ok=False)
    model.save(model_dir)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(f"\n模型 {model_dir}\n报告 {report_path}\n自动确认未打开。")
    return 0 if report["evaluation_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
