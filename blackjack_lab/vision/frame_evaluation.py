"""Detector-blind annotated-frame evaluation; no ledger or training imports.

A predictor accepts an annotation frame record and returns dictionaries with
bbox, rank (accepted rank only), region_id, and optional observation_id. The
production wrapper uses the shared recognition pipeline on the original image.
"""
from __future__ import annotations

from collections import Counter
from typing import Callable, Mapping

from .contracts import ContractError, RANKS_13

TRUTH_LABELS = RANKS_13 + ("junk", "unreadable")


def _bbox(raw):
    values = [raw[key] for key in ("x", "y", "w", "h")] if isinstance(raw, Mapping) else list(raw)
    if len(values) != 4 or any(type(v) not in (int, float) for v in values):
        raise ContractError("Invalid evaluation bbox")
    x, y, w, h = values
    if not all(float("-inf") < v < float("inf") for v in values) or min(x, y) < 0 or min(w, h) <= 0:
        raise ContractError("Invalid evaluation bbox dimensions")
    return tuple(values)


def intersection_over_union(a, b):
    ax, ay, aw, ah = _bbox(a)
    bx, by, bw, bh = _bbox(b)
    intersection = max(0, min(ax + aw, bx + bw) - max(ax, bx)) * max(0, min(ay + ah, by + bh) - max(ay, by))
    return intersection / (aw * ah + bw * bh - intersection)


def _match(truth, candidates, threshold):
    # Maximum-cardinality bipartite matching, deterministic with highest IoU
    # edges visited first. Labels and confidence never affect assignment.
    edges = {i: sorted(((j, intersection_over_union(obj["bbox"], cand["bbox"]))
                        for j, cand in enumerate(candidates)), key=lambda pair: (-pair[1], pair[0]))
             for i, obj in enumerate(truth)}
    edges = {i: [j for j, overlap in values if overlap >= threshold] for i, values in edges.items()}
    owner = {}

    def assign(i, visited):
        for j in edges[i]:
            if j in visited:
                continue
            visited.add(j)
            if j not in owner or assign(owner[j], visited):
                owner[j] = i
                return True
        return False

    for i in sorted(edges, key=lambda index: (len(edges[index]), index)):
        assign(i, set())
    return {i: j for j, i in owner.items()}, edges


def source_completeness_reasons(manifest):
    reasons = []
    if manifest.get("valid") is False:
        reasons.append("source_manifest_invalid")
    if manifest.get("decode_errors") or manifest.get("source_decode_errors") or manifest.get("unreadable_frames"):
        reasons.append("source_decode_errors")
    if (manifest.get("truncated") or manifest.get("source_truncated") or manifest.get("truncated_candidates")
            or manifest.get("complete") is False or manifest.get("source_complete") is False):
        reasons.append("source_incomplete_or_truncated")
    return reasons


def evaluate_annotated_frames(annotation, predictor: Callable, *, iou_threshold=0.30,
                              source_manifest=None, source_errors=()):
    """Keep declared GT denominators even when a frame or its prediction fails.

    Missing/unreviewed/incomplete evidence sets valid=false and suppresses all
    acceptance metrics. Diagnostic counts remain available for repairs.
    """
    if annotation.get("schema") != "original-frame-annotations-1":
        raise ContractError("Unknown original-frame annotation schema")
    if not 0 < iou_threshold <= 1:
        raise ContractError("IoU threshold must be in (0, 1]")
    frames = annotation.get("frames", [])
    reasons = (list(source_errors) + source_completeness_reasons(source_manifest or {})
               + source_completeness_reasons(annotation))
    if not frames:
        reasons.append("no_annotation_frames")
    if not annotation.get("source_sha256"):
        reasons.append("source_identity_missing")
    counts = Counter()
    per_rank = {rank: dict(total=0, correct=0, wrong=0, rejected=0, missed_extraction=0, invalid=0)
                for rank in RANKS_13}
    confusion = {rank: {} for rank in TRUTH_LABELS}
    rows, frame_files, physical = [], set(), set()

    def record_confusion(truth, output):
        bucket = confusion[truth]
        bucket[output] = bucket.get(output, 0) + 1

    for frame in frames:
        raw_objects = frame.get("objects")
        objects = [obj if isinstance(obj, Mapping) else {} for obj in raw_objects] if isinstance(raw_objects, list) else []
        frame_errors = []
        file = frame.get("file", "")
        if not file or file in frame_files:
            frame_errors.append("missing_or_duplicate_frame_identity")
        frame_files.add(file)
        if frame.get("complete") is not True:
            frame_errors.append("frame_annotation_incomplete")
        if not frame.get("reviewed_by"):
            frame_errors.append("frame_human_review_missing")
        object_errors = [] if isinstance(raw_objects, list) else ["missing_or_invalid_objects"]
        frame_physical = set()
        for obj in objects:
            rank = obj.get("rank")
            counts["n_truth_objects"] += 1
            if rank in RANKS_13:
                counts["n_truth_identifiable"] += 1
                per_rank[rank]["total"] += 1
            elif rank in ("junk", "unreadable"):
                counts["n_truth_" + rank] += 1
            else:
                object_errors.append("invalid_truth_rank")
            if obj.get("label_provenance") != "human_reviewed" or not obj.get("reviewed_by"):
                frame_errors.append("object_human_review_missing")
            try:
                _bbox(obj["bbox"])
            except (ContractError, KeyError, TypeError, ValueError):
                object_errors.append("invalid_truth_bbox")
            pid = obj.get("physical_card_id")
            if rank in RANKS_13 or rank == "unreadable":
                if not pid or pid in frame_physical:
                    object_errors.append("missing_or_duplicate_physical_card_identity")
                frame_physical.add(pid)
                if pid:
                    physical.add((annotation.get("source_sha256"), pid))
        frame_errors.extend(object_errors)
        detail = {"file": file, "n_truth_objects": len(objects), "errors": sorted(set(frame_errors)),
                  "objects": [], "unmatched_candidates": []}
        rows.append(detail)
        try:
            if object_errors:
                raise ContractError(";".join(sorted(set(object_errors))))
            candidates = list(predictor(frame))
            for candidate in candidates:
                _bbox(candidate["bbox"])
                if candidate.get("rank") is not None and candidate["rank"] not in RANKS_13:
                    raise ContractError("Invalid accepted prediction rank")
        except Exception as exc:
            # A failed prediction is an invalid frame, never an empty detector.
            detail["errors"].append(f"frame_or_prediction_invalid:{type(exc).__name__}:{exc}")
            counts["n_invalid_frames"] += 1
            for obj in objects:
                if obj.get("rank") in RANKS_13:
                    per_rank[obj["rank"]]["invalid"] += 1
                    counts["n_invalid_identifiable"] += 1
                if obj.get("rank") in confusion:
                    record_confusion(obj["rank"], "invalid")
            reasons.append("frame_or_prediction_invalid")
            reasons.extend(frame_errors)
            continue
        counts["n_evaluated_frames"] += 1
        counts["n_candidates"] += len(candidates)
        counts["n_accepted_outputs"] += sum(c.get("rank") is not None for c in candidates)
        assignments, edges = _match(objects, candidates, iou_threshold)
        matched_candidates = set(assignments.values())
        for i, obj in enumerate(objects):
            truth = obj["rank"]
            j = assignments.get(i)
            prediction = candidates[j] if j is not None else None
            rank = prediction.get("rank") if prediction else None
            if j is None:
                outcome = "missed_extraction" if truth in RANKS_13 else "not_detected"
            elif truth in RANKS_13:
                outcome = "rejected" if rank is None else "correct" if rank == truth else "wrong"
            else:
                outcome = "kept_out" if rank is None else "accepted_as_rank"
            if truth in RANKS_13:
                per_rank[truth][outcome] += 1
                counts[outcome] += 1
            elif rank is not None:
                counts[truth + "_as_rank"] += 1
            record_confusion(truth, rank or outcome)
            detail["objects"].append({"physical_card_id": obj.get("physical_card_id"),
                "truth": truth, "predicted": rank, "outcome": outcome,
                "candidate_index": j, "bbox": obj["bbox"]})
            expected_region = obj.get("region_id")
            if prediction and truth in RANKS_13 and expected_region not in (None, "", "unassigned"):
                counts["n_ownership_comparable"] += 1
                if prediction.get("region_id") != expected_region:
                    counts["ownership_wrong"] += 1
        overlapping_candidates = {j for values in edges.values() for j in values}
        for j, candidate in enumerate(candidates):
            if j in matched_candidates:
                continue
            duplicate = j in overlapping_candidates
            outcome = "duplicate_candidate" if duplicate else "unmatched_candidate"
            counts[outcome] += 1
            if candidate.get("rank") is not None:
                counts["duplicate_accepted" if duplicate else "unmatched_accepted"] += 1
            detail["unmatched_candidates"].append({**candidate, "outcome": outcome})
        reasons.extend(frame_errors)
    reasons = sorted(set(reasons))
    valid = not reasons
    keys = ("n_truth_objects", "n_truth_identifiable", "n_truth_junk", "n_truth_unreadable",
            "n_candidates", "n_accepted_outputs", "correct", "wrong", "rejected", "missed_extraction",
            "junk_as_rank", "unreadable_as_rank", "duplicate_candidate", "duplicate_accepted",
            "unmatched_candidate", "unmatched_accepted", "n_invalid_frames", "n_invalid_identifiable",
            "n_evaluated_frames", "n_ownership_comparable", "ownership_wrong")
    numeric = {key: counts[key] for key in keys}
    precision = counts["correct"] / counts["n_accepted_outputs"] if counts["n_accepted_outputs"] else None
    recall = counts["correct"] / counts["n_truth_identifiable"] if counts["n_truth_identifiable"] else None
    detected = counts["correct"] + counts["wrong"] + counts["rejected"]
    return {"schema": "original-frame-evaluation-1", "valid": valid,
            "incomplete_reasons": reasons, "n_annotation_frames": len(frames), **numeric,
            "n_annotated_physical_cards": len(physical), "iou_threshold": iou_threshold,
            "matching": "label_blind_maximum_cardinality_iou_ordered",
            "selection": annotation.get("selection"), "source_sha256": annotation.get("source_sha256"),
            "all_output_precision": precision if valid else None,
            "sampled_frame_end_to_end_recall": recall if valid else None,
            "extraction_recall": detected / counts["n_truth_identifiable"] if valid and counts["n_truth_identifiable"] else None,
            "diagnostic_only": {"precision_over_declared_truth": precision, "recall_over_declared_truth": recall},
            "per_rank": per_rank, "confusion": confusion, "frames": rows,
            "video_end_to_end_recall": None, "ledger_duplicate_count": None,
            "round_event_agreement": None, "final_acceptance_eligible": False,
            "unmeasured_reason": "Sampled frames do not evaluate every video frame, temporal tracking, confirmations or ledger events.",
            "auto_confirm_enabled": False, "human_corrected_not_counted": True}
