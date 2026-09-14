"""Unused-holdout remaining compositions for pre-deal window contrast.

A package is refused unless a human attests it was not used for training or
threshold selection. Synthetic fixtures may exercise the path; they never set
independent_video. Missing remaining, missing physical IDs, or missing
attestation cannot be replaced by current-hand EV.
"""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import AVAILABLE
from .research_windows import WINDOW_PRE_DEAL
from .shoe_windows import evaluate_predeal

SCHEMA = "hakimi-unused-holdout-v1"
ATTESTATION_FLAGS = (
    "unused_in_training",
    "unused_in_threshold_selection",
    "unused_in_model_selection",
    "human_reviewed_ranks",
    "physical_card_ids",
)


class HoldoutError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _positive(record):
    return record.get("status") == AVAILABLE and record.get("ev") is not None and record["ev"] > 0


def _confusion(rounds, observer_key):
    false_positive = false_negative = agree_positive = agree_negative = 0
    for item in rounds:
        truth_hit = _positive(item["truth"])
        obs_hit = _positive(item[observer_key])
        if truth_hit and obs_hit:
            agree_positive += 1
        elif (not truth_hit) and (not obs_hit):
            agree_negative += 1
        elif obs_hit and not truth_hit:
            false_positive += 1
        else:
            false_negative += 1
    return {"agree_positive": agree_positive, "agree_negative": agree_negative,
            "false_positive": false_positive, "false_negative": false_negative}


def load_holdout(path_or_data):
    data = json.loads(Path(path_or_data).read_text(encoding="utf-8")) if not isinstance(path_or_data, dict) else path_or_data
    if not isinstance(data, dict) or data.get("schema") != SCHEMA:
        raise HoldoutError("HOLDOUT_SCHEMA", "未使用留出包格式不正确")
    attestation = data.get("attestation")
    if not isinstance(attestation, dict):
        raise HoldoutError("NOT_ATTESTED", "缺少未使用/人审声明，不能把开发材料换签成独立录像")
    for flag in ATTESTATION_FLAGS:
        if attestation.get(flag) is not True:
            raise HoldoutError("NOT_UNUSED", f"留出声明 {flag} 未成立；不能用于独立窗口对照")
    if not isinstance(attestation.get("attested_by"), str) or not attestation["attested_by"].strip():
        raise HoldoutError("NOT_ATTESTED", "未使用留出必须有具名声明人")
    rounds = data.get("rounds")
    if not isinstance(rounds, list) or not rounds:
        raise HoldoutError("ROUNDS_MISSING", "留出包没有逐轮剩余组成")
    parsed = []
    for index, item in enumerate(rounds):
        if not isinstance(item, dict):
            raise HoldoutError("ROUND_INVALID", "留出轮次必须为对象")
        truth = item.get("truth_remaining")
        observer = item.get("observer_remaining")
        if not isinstance(truth, list) or not truth:
            raise HoldoutError("TRUTH_MISSING", "每轮必须有人工真值剩余组成")
        if observer is not None and not isinstance(observer, list):
            raise HoldoutError("OBSERVER_INVALID", "观察剩余必须为点值列表或空")
        video_id = item.get("video_id")
        if video_id is not None and not isinstance(video_id, str):
            raise HoldoutError("VIDEO_ID_INVALID", "录像标识必须是字符串")
        parsed.append({"round_index": item.get("round_index", index),
                       "truth_remaining": [int(v) for v in truth],
                       "observer_remaining": None if observer is None else [int(v) for v in observer],
                       "video_id": (video_id or "").strip() or None})
    source_kind = attestation.get("source_kind") or "unspecified"
    if source_kind == "unused_video":
        missing = [row["round_index"] for row in parsed if not row["video_id"]]
        if missing:
            raise HoldoutError("VIDEO_ID_MISSING", "unused_video 留出每轮必须有 video_id；不能只改 source_kind")
        independent_video = True
    else:
        independent_video = False
    return {
        "schema": SCHEMA,
        "attestation": attestation,
        "source_kind": source_kind,
        "rounds": parsed,
        "independent_video": independent_video,
    }


def compare_holdout(package, *, budget_seconds=5.0):
    loaded = load_holdout(package)
    evaluated = []
    for item in loaded["rounds"]:
        truth = evaluate_predeal(item["truth_remaining"], budget_seconds=budget_seconds)
        if item["observer_remaining"] is None:
            observer = {"status": "inapplicable", "reason_code": "OBSERVER_MISSING", "ev": None,
                        "reason": "该轮没有观察剩余；不能用真值窗口冒充当时已抓住"}
        else:
            observer = evaluate_predeal(item["observer_remaining"], budget_seconds=budget_seconds)
        evaluated.append({"round_index": item["round_index"], "truth": truth, "observer": observer})
    truth_positive = sum(1 for item in evaluated if _positive(item["truth"]))
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "independent_video": loaded["independent_video"],
        "source_kind": loaded["source_kind"],
        "attested_by": loaded["attestation"]["attested_by"],
        "rounds": evaluated,
        "summary": {
            "round_count": len(evaluated),
            "truth_positive": truth_positive,
            "zero_window_truth": truth_positive == 0,
            "observer": _confusion(evaluated, "observer"),
            "note": "无未使用录像声明时不得把合成或开发材料写成独立对照",
        },
    }
