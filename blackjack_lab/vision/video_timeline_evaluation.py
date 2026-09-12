"""Read-only temporal identity evaluation against sampled human frame truth.

The reader receives only a frame index; the recognizer receives only its image.
Truth boxes, ranks, IDs and round labels never reach recognition or tracking.
Only a separate, explicit operator binding can change the tracker's round.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import hashlib
from typing import Mapping

from .contracts import ContractError, RANKS_13
from .frame_evaluation import _bbox, _match, source_completeness_reasons
from .tracker import FrameTracker
from .image_io import crop_rgb


def _operator_bindings(payload, source_sha256):
    if payload is None:
        return {}
    if (payload.get("schema") != "operator-round-bindings-1"
            or payload.get("source_sha256") != source_sha256):
        raise ContractError("Operator round bindings have an invalid schema or source")
    bindings = {}
    for binding in payload.get("bindings", []):
        index = binding.get("frame_index")
        if (type(index) is not int or index < 0 or index in bindings
                or not isinstance(binding.get("binding_id"), str) or not binding["binding_id"].strip()
                or binding.get("provenance") != "operator_confirmed" or not binding.get("confirmed_by")):
            raise ContractError("Round changes require distinct frames and explicit operator confirmation")
        bindings[index] = binding["binding_id"]
    return bindings


def style_offset(loaded, style):
    """Translate pipeline coordinates back to the source frame; never use GT ROI."""
    from .live_input import LiveStyle, capture_crop_pixels
    if isinstance(style, LiveStyle):
        crop = capture_crop_pixels(style, loaded.width, loaded.height)
        return (crop[0], crop[1]) if crop else (0, 0)
    width, height = loaded.width, loaded.height
    dx = dy = 0
    if (style.source_crop and (width, height) == (style.source_frame_width, style.source_frame_height)):
        dx, dy, width, height = style.source_crop
    if style.felt_crop and (width, height) != (style.canvas_width, style.canvas_height):
        x, y, w, h = style.felt_crop
        if x + w <= width and y + h <= height:
            dx += x
            dy += y
    return dx, dy


def _truth_targets(record, offset, truth_limits=None):
    raw = record.get("objects")
    if not isinstance(raw, list):
        raise ContractError("Missing objects is unknown truth, not an empty table")
    targets, cards, errors = [], {}, []
    for number, obj in enumerate(raw):
        if not isinstance(obj, Mapping):
            raise ContractError("Invalid truth object")
        label = obj.get("rank")
        if label is None and obj.get("face_state") in ("back", "unreadable"):
            label = "unreadable"
        if label not in RANKS_13 + ("junk", "unreadable"):
            raise ContractError("Invalid truth rank")
        if obj.get("label_provenance") != "human_reviewed" or not obj.get("reviewed_by"):
            errors.append("object_human_review_missing")
        physical = obj.get("physical_card_id")
        if label != "junk" and (not isinstance(physical, str) or not physical.strip()):
            raise ContractError("A physical card needs a stable source-wide ID")
        key = physical if label != "junk" else f"junk:{number}"
        if key in cards and (cards[key]["rank"], cards[key]["region_id"]) != (label, obj.get("region_id")):
            raise ContractError("One physical card has conflicting truth in a frame")
        boxes = obj.get("boxes", [obj.get("bbox")])
        if not isinstance(boxes, list) or not boxes:
            raise ContractError("Each truth object needs at least one bbox")
        card = cards.setdefault(key, {"rank": label, "region_id": obj.get("region_id"), "physical_card_id": key})
        for raw_box in boxes:
            x, y, w, h = _bbox(raw_box)
            if truth_limits is not None and (x + w > truth_limits[0] or y + h > truth_limits[1]):
                raise ContractError("Truth bbox lies outside the verified material ROI")
            targets.append({**card, "bbox": [x + offset[0], y + offset[1], w, h]})
    return targets, cards, errors


def evaluate_timeline(annotation, *, source_sha256, first_frame, last_frame,
                      read_frame, recognize_frame, time_ms, round_bindings=None,
                      iou_threshold=0.30, tracker=None):
    """Decode every frame, score only explicit annotation frames.

    ``recognize_frame(loaded)`` returns ``(RecognitionResult, (offset_x, offset_y))``.
    It is never passed any annotation object. This function does not write files,
    import a controller, confirm candidates, or fabricate ledger receipts.
    """
    if annotation.get("schema") != "original-frame-annotations-1":
        raise ContractError("Expected original-frame-annotations-1")
    if (type(first_frame) is not int or type(last_frame) is not int
            or first_frame < 0 or last_frame < first_frame or not 0 < iou_threshold <= 1):
        raise ContractError("Invalid timeline frame interval or IoU threshold")
    bindings = _operator_bindings(round_bindings, source_sha256)
    tracker = tracker or FrameTracker()
    errors = list(source_completeness_reasons(annotation))
    if annotation.get("source_sha256") != source_sha256:
        errors.append("annotation_source_mismatch")
    space = annotation.get("coordinate_space", "source_frame")
    truth_offset = (0, 0)
    truth_limits = None
    if space == "material_roi":
        roi = annotation.get("source_roi")
        if not isinstance(roi, list) or len(roi) != 4:
            raise ContractError("material_roi truth requires source_roi=[x0,y0,x1,y1]")
        _bbox([roi[0], roi[1], roi[2] - roi[0], roi[3] - roi[1]])
        truth_offset = tuple(roi[:2])
        truth_limits = (roi[2] - roi[0], roi[3] - roi[1])
    elif space not in ("source_frame", "original_frame"):
        raise ContractError("Unknown truth coordinate space")
    records, parsed, truth_rows, physical, gt_round_counts = {}, {}, [], {}, defaultdict(set)
    gt_round_provenance = defaultdict(set)
    n_declared_records = n_outside = 0
    for record in annotation.get("frames", []):
        index = record.get("frame_index")
        if type(index) is not int or index < 0:
            errors.append("truth_frame_index_missing_or_invalid")
            continue
        if not first_frame <= index <= last_frame:
            n_outside += 1
            continue
        n_declared_records += 1
        if index in records:
            errors.append("duplicate_truth_frame_index")
            continue
        records[index] = record
        row = {"frame_index": index, "errors": [], "matches": []}
        truth_rows.append(row)
        if record.get("complete") is not True or not record.get("reviewed_by"):
            row["errors"].append("frame_human_review_incomplete")
        if not record.get("source_rgb_sha256") and not (space == "material_roi" and record.get("material_rgb_sha256")):
            row["errors"].append("source_or_material_rgb_sha256_missing")
        try:
            targets, cards, review_errors = _truth_targets(record, truth_offset, truth_limits)
            row["errors"].extend(review_errors)
            parsed[index] = (targets, cards, row)
            for pid, card in cards.items():
                if card["rank"] == "junk":
                    continue
                gt_round_counts[str(record.get("round_id", "unspecified"))].add(pid)
                gt_round_provenance[str(record.get("round_id", "unspecified"))].add(
                    str(record.get("round_provenance", "unspecified")))
                entry = physical.setdefault(pid, {"physical_card_id": pid, "n_truth_frames": 0,
                    "identifiable_truth": False, "matched_frames": 0, "accepted_frames": 0,
                    "correct_frames": 0, "first_comparable_prediction": None, "track_ids": set(),
                    "last_track_ids": set(), "id_switches": 0, "known_truth_ranks": set(),
                    "declared_truth_round_ids": set()})
                entry["n_truth_frames"] += 1
                entry["identifiable_truth"] |= card["rank"] in RANKS_13
                if card["rank"] in RANKS_13:
                    entry["known_truth_ranks"].add(card["rank"])
                if record.get("round_id") is not None:
                    entry["declared_truth_round_ids"].add(str(record["round_id"]))
                if len(entry["known_truth_ranks"]) > 1:
                    row["errors"].append("physical_id_conflicting_known_rank")
                if len(entry["declared_truth_round_ids"]) > 1:
                    row["errors"].append("physical_id_reused_across_truth_rounds")
        except (ContractError, TypeError, KeyError, ValueError) as exc:
            row["errors"].append(f"invalid_truth:{exc}")
        errors.extend(row["errors"])
    if not records:
        errors.append("no_truth_frames_in_range")
    counts = Counter()
    stream_tracks, track_to_physical, decode_errors, prediction_errors = {}, defaultdict(set), [], []
    active_binding = "unbound"
    preceding = [index for index in bindings if index < first_frame]
    if preceding:
        active_binding = bindings[max(preceding)]
        tracker.confirm_round_boundary(active_binding)
    for index in range(first_frame, last_frame + 1):
        if index in bindings:
            active_binding = bindings[index]
            tracker.confirm_round_boundary(active_binding)
        try:
            loaded = read_frame(index)
            counts["decoded_frames"] += 1
        except Exception as exc:
            decode_errors.append({"frame_index": index, "error": f"{type(exc).__name__}:{exc}"})
            if index in parsed:
                parsed[index][2]["errors"].append("decode_failed")
            continue
        if index in records:
            record = records[index]
            verification_scope = None
            if record.get("source_rgb_sha256"):
                if record["source_rgb_sha256"] == loaded.sha256:
                    verification_scope = "source_frame"
            elif space == "material_roi" and record.get("material_rgb_sha256"):
                try:
                    x0, y0, x1, y1 = annotation["source_roi"]
                    digest = hashlib.sha256(crop_rgb(loaded, x0, y0, x1 - x0, y1 - y0)).hexdigest()
                    if digest == record["material_rgb_sha256"]:
                        verification_scope = "material_roi"
                except (TypeError, ValueError):
                    pass
            if verification_scope is None:
                errors.append("video_frame_pixels_unverified")
                if index in parsed:
                    parsed[index][2]["errors"].append("video_frame_pixels_unverified")
            else:
                counts[f"verified_{verification_scope}_frames"] += 1
                if index in parsed:
                    parsed[index][2]["verified_frame_scope"] = verification_scope
        try:
            result, offset = recognize_frame(loaded)
            tracker.apply_to_result(result, index, time_ms(index))
            counts["recognized_frames"] += 1
            candidates = []
            for obs in result.observations:
                tid, rank = obs.observation_id, obs.accepted_rank()
                event = stream_tracks.setdefault(tid, {"track_id": tid, "binding_id": active_binding,
                    "first_frame": index, "first_accepted_frame": None, "first_accepted_rank": None})
                if rank is not None and event["first_accepted_frame"] is None:
                    event.update(first_accepted_frame=index, first_accepted_rank=rank)
                candidates.append({"track_id": tid, "rank": rank, "region_id": obs.region_id,
                    "bbox": {**obs.bbox, "x": obs.bbox["x"] + offset[0], "y": obs.bbox["y"] + offset[1]}})
        except Exception as exc:
            prediction_errors.append({"frame_index": index, "error": f"{type(exc).__name__}:{exc}"})
            if index in parsed:
                parsed[index][2]["errors"].append("prediction_failed")
            continue
        if index not in parsed:
            continue
        targets, cards, row = parsed[index]
        if any(x < 0 or y < 0 or x + w > loaded.width or y + h > loaded.height
               for x, y, w, h in (target["bbox"] for target in targets)):
            row["errors"].append("truth_bbox_outside_source_frame")
            errors.append("truth_bbox_outside_source_frame")
            continue
        counts["scored_annotation_frames"] += 1
        counts["sampled_identifiable_truth_targets"] += sum(target["rank"] in RANKS_13 for target in targets)
        counts["sampled_candidate_outputs"] += len(candidates)
        counts["sampled_accepted_outputs"] += sum(candidate["rank"] is not None for candidate in candidates)
        assignments, _ = _match(targets, candidates, iou_threshold)
        for ti, target in enumerate(targets):
            if target["rank"] not in RANKS_13:
                continue
            if ti not in assignments:
                counts["sampled_missed_identifiable_targets"] += 1
            else:
                predicted = candidates[assignments[ti]]["rank"]
                if predicted is None:
                    counts["sampled_rejected_identifiable_targets"] += 1
                elif predicted == target["rank"]:
                    counts["sampled_correct_rank_outputs"] += 1
                else:
                    counts["sampled_wrong_rank_outputs"] += 1
        matched = defaultdict(list)
        for ti, ci in assignments.items():
            target, candidate = targets[ti], candidates[ci]
            if target["rank"] == "junk":
                counts["junk_as_rank"] += candidate["rank"] is not None
                continue
            pid = target["physical_card_id"]
            matched[pid].append(candidate)
            track_to_physical[candidate["track_id"]].add(pid)
            expected_region = target.get("region_id")
            if expected_region not in (None, "", "unassigned"):
                counts["ownership_comparable_observations"] += 1
                counts["ownership_wrong_observations"] += candidate["region_id"] != expected_region
            row["matches"].append({"physical_card_id": pid, **candidate, "truth": target["rank"]})
        counts["unmatched_sampled_candidates"] += len(candidates) - len(assignments)
        used_candidates = set(assignments.values())
        counts["unmatched_sampled_accepted_outputs"] += sum(
            candidate["rank"] is not None for ci, candidate in enumerate(candidates) if ci not in used_candidates)
        for pid, card in cards.items():
            if card["rank"] == "junk":
                continue
            entry = physical[pid]
            sightings = matched.get(pid, [])
            ids = {candidate["track_id"] for candidate in sightings}
            accepted = [candidate["rank"] for candidate in sightings if candidate["rank"] is not None]
            entry["matched_frames"] += bool(sightings)
            entry["accepted_frames"] += bool(accepted)
            entry["correct_frames"] += card["rank"] in accepted
            if ids and entry["last_track_ids"] and not ids.intersection(entry["last_track_ids"]):
                entry["id_switches"] += 1
            if ids:
                entry["last_track_ids"] = ids
                entry["track_ids"].update(ids)
            if accepted and card["rank"] in RANKS_13 and entry["first_comparable_prediction"] is None:
                entry["first_comparable_prediction"] = {"frame_index": index, "truth": card["rank"],
                    "predicted_ranks": accepted, "wrong": any(rank != card["rank"] for rank in accepted)}
            if accepted and card["rank"] == "unreadable":
                counts["unreadable_as_rank"] += len(accepted)
    if decode_errors:
        errors.append("video_decode_incomplete")
    if prediction_errors:
        errors.append("recognition_incomplete")
    valid = not errors
    metrics = {
        "physical_cards_never_detected": sum(not entry["matched_frames"] for entry in physical.values()),
        "identifiable_cards_never_correct": sum(entry["identifiable_truth"] and not entry["correct_frames"] for entry in physical.values()),
        "first_comparable_prediction_wrong": sum(bool(entry["first_comparable_prediction"] and entry["first_comparable_prediction"]["wrong"]) for entry in physical.values()),
        "track_fragmentation": sum(max(0, len(entry["track_ids"]) - 1) for entry in physical.values()),
        "id_switches": sum(entry["id_switches"] for entry in physical.values()),
        "identity_merge_tracks": sum(len(ids) > 1 for ids in track_to_physical.values()),
    }
    for key in ("ownership_comparable_observations", "ownership_wrong_observations", "junk_as_rank",
                "unreadable_as_rank", "unmatched_sampled_candidates", "unmatched_sampled_accepted_outputs",
                "sampled_identifiable_truth_targets", "sampled_candidate_outputs", "sampled_accepted_outputs",
                "sampled_correct_rank_outputs", "sampled_wrong_rank_outputs",
                "sampled_rejected_identifiable_targets", "sampled_missed_identifiable_targets"):
        metrics[key] = counts[key]
    metrics["sampled_all_output_verified_precision"] = (
        counts["sampled_correct_rank_outputs"] / counts["sampled_accepted_outputs"]
        if counts["sampled_accepted_outputs"] else None)
    rounds = defaultdict(lambda: {"candidate_appearances": 0, "accepted_track_appearances": 0})
    for event in stream_tracks.values():
        rounds[event["binding_id"]]["candidate_appearances"] += 1
        rounds[event["binding_id"]]["accepted_track_appearances"] += event["first_accepted_frame"] is not None
    physical_rows = [{key: sorted(value) if isinstance(value, set) else value
                      for key, value in entry.items() if key != "last_track_ids"} for entry in physical.values()]
    verified_scopes = {row["verified_frame_scope"] for row in truth_rows if row.get("verified_frame_scope")}
    return {"schema": "sampled-video-timeline-evaluation-1", "valid": valid,
        "scope": "continuous_decode_with_sampled_human_temporal_identity_truth",
        "source_sha256": source_sha256, "first_frame": first_frame, "last_frame": last_frame,
        "expected_decode_frames": last_frame - first_frame + 1,
        "decoded_frames": counts["decoded_frames"], "recognized_frames": counts["recognized_frames"],
        "scored_annotation_frames": counts["scored_annotation_frames"],
        "verified_source_frame_count": counts["verified_source_frame_frames"],
        "verified_material_roi_frame_count": counts["verified_material_roi_frames"],
        "verified_frame_scope": next(iter(verified_scopes)) if len(verified_scopes) == 1 else "mixed" if verified_scopes else None,
        "n_declared_annotation_records": n_declared_records, "n_annotation_frames": len(records),
        "n_annotations_outside_interval": n_outside, "n_truth_physical_cards": len(physical),
        "incomplete_reasons": sorted(set(errors)), "decode_errors": decode_errors,
        "prediction_errors": prediction_errors, "metrics": metrics if valid else None,
        "diagnostic_counts": metrics, "physical_cards": physical_rows, "annotation_frames": truth_rows,
        "track_to_physical": {key: sorted(value) for key, value in track_to_physical.items()},
        "candidate_appear_events": list(stream_tracks.values()), "operator_bound_round_counts": dict(rounds),
        "annotation_round_group_counts": {key: {"physical_cards": len(value),
            "provenance": sorted(gt_round_provenance[key])} for key, value in gt_round_counts.items()},
        "round_group_note": "Annotation round labels may be automatic segmentation; these group counts do not establish true rounds or round-event agreement.",
        "tracker_rounds_driven_by": "explicit_operator_bindings_only",
        "ground_truth_used_by_recognizer_or_tracker": False,
        "video_per_frame_end_to_end_recall": None, "ledger_duplicate_count": None,
        "round_event_agreement": None, "reveal_move_leave_event_agreement": None,
        "ledger_unmeasured_reason": "No independent actual operator confirmation and ledger receipt trace was evaluated.",
        "event_unmeasured_reason": "Only candidate track appearances are recorded; human appearance timing and reveal/move/leave event agreement are not evaluated.",
        "pixel_unmeasured_reason": "Only explicit human annotation frames have detection/rank/identity truth; decoding intervening frames does not create truth.",
        "sampled_output_metric_note": "Counts use one-to-one annotated visible-index targets, including multiple boxes of one physical card. Verified precision includes every accepted sampled output in its denominator; it is not full-video precision.",
        "iou_threshold": iou_threshold, "auto_confirm_enabled": False, "final_acceptance_eligible": False}
