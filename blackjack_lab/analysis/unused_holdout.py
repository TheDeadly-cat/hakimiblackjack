"""Unused-holdout remaining compositions for pre-deal window contrast.

A package is refused unless a human attests it was not used for training or
threshold selection. Synthetic fixtures may exercise the path; they never set
independent_video. A named video_id is still only a declaration: software
cannot certify unused footage from flags or strings. Missing remaining,
missing physical IDs, or missing attestation cannot be replaced by
current-hand EV.
"""
from __future__ import annotations

import json
from pathlib import Path

from .predeal_contracts import SURRENDER_UNSET, require_declared_surrender
from .research_windows import WINDOW_PRE_DEAL, confusion_matrix, evaluation_scope, require_point_values
from .shoe_windows import evaluate_predeal
from .evidence import classify_artifacts

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
        try:
            truth_values = list(require_point_values(truth, what="真值剩余组成"))
        except ValueError as error:
            raise HoldoutError("TRUTH_INVALID", str(error)) from error
        observer_values = None
        if observer is not None:
            try:
                observer_values = list(require_point_values(observer, what="观察剩余组成"))
            except ValueError as error:
                raise HoldoutError("OBSERVER_INVALID", str(error)) from error
        video_id = item.get("video_id")
        if video_id is not None and not isinstance(video_id, str):
            raise HoldoutError("VIDEO_ID_INVALID", "录像标识必须是字符串")
        video_sha256 = item.get("video_sha256")
        if video_sha256 is not None:
            if not isinstance(video_sha256, str) or len(video_sha256) != 64:
                raise HoldoutError("VIDEO_SHA_INVALID", "录像摘要必须是64位十六进制")
            if any(c not in "0123456789abcdefABCDEF" for c in video_sha256):
                raise HoldoutError("VIDEO_SHA_INVALID", "录像摘要必须是64位十六进制")
            video_sha256 = video_sha256.lower()
        video_path = item.get("video_path")
        if video_path is not None and (not isinstance(video_path, str) or not video_path.strip()):
            raise HoldoutError("VIDEO_PATH_INVALID", "录像路径必须是非空字符串")
        physical_ids = item.get("physical_card_ids")
        if physical_ids is not None:
            if not isinstance(physical_ids, list) or not all(
                    isinstance(value, str) and value.strip() for value in physical_ids):
                raise HoldoutError("PHYSICAL_IDS_INVALID", "物理牌身份必须是非空字符串列表")
            physical_ids = [value.strip() for value in physical_ids]
        parsed.append({
            "round_index": item.get("round_index", index),
            "truth_remaining": truth_values,
            "observer_remaining": observer_values,
            "video_id": (video_id or "").strip() or None,
            "video_sha256": video_sha256,
            "video_path": video_path.strip() if isinstance(video_path, str) else None,
            "physical_card_ids": physical_ids,
        })
    source_kind = attestation.get("source_kind") or "unspecified"
    declared_unused_video = False
    if source_kind == "unused_video":
        missing = [row["round_index"] for row in parsed if not row["video_id"]]
        if missing:
            raise HoldoutError("VIDEO_ID_MISSING", "unused_video 留出每轮必须有 video_id；不能只改 source_kind")
        declared_unused_video = True
    artifacts = []
    for row in parsed:
        if row.get("video_path") or row.get("video_sha256"):
            artifacts.append({
                "path": row.get("video_path"),
                "sha256": row.get("video_sha256"),
                "role": "unused_video",
            })
    binding = classify_artifacts(artifacts) if artifacts else None
    evidence_level = binding["evidence_level"] if binding else "declared"
    return {
        "schema": SCHEMA,
        "attestation": {
            "attested_by": attestation["attested_by"].strip(),
            "source_kind": source_kind,
            "unused_in_training": True,
            "unused_in_threshold_selection": True,
            "unused_in_model_selection": True,
            "human_reviewed_ranks": True,
            "physical_card_ids_flag": attestation.get("physical_card_ids") is True,
        },
        "source_kind": source_kind,
        "evidence_level": evidence_level,
        "video_binding": binding,
        "identity_chain": [
            {key: row.get(key) for key in (
                "round_index", "video_id", "video_sha256", "video_path", "physical_card_ids")}
            for row in parsed
        ],
        "rounds": parsed,
        "declared_unused_video": declared_unused_video,
        "independent_video": False,
        "accepted": False,
    }


def compare_holdout(package, *, budget_seconds=5.0, surrender=SURRENDER_UNSET):
    surrender = require_declared_surrender(surrender, what="未使用留出对照")
    loaded = load_holdout(package)
    evaluated = []
    for item in loaded["rounds"]:
        truth = evaluate_predeal(item["truth_remaining"], budget_seconds=budget_seconds,
                                 surrender=surrender)
        if item["observer_remaining"] is None:
            observer = {"status": "inapplicable", "reason_code": "OBSERVER_MISSING", "ev": None,
                        "reason": "该轮没有观察剩余；不能用真值窗口冒充当时已抓住"}
        else:
            observer = evaluate_predeal(item["observer_remaining"], budget_seconds=budget_seconds,
                                       surrender=surrender)
        evaluated.append({
            "round_index": item["round_index"],
            "video_id": item.get("video_id"),
            "video_sha256": item.get("video_sha256"),
            "video_path": item.get("video_path"),
            "physical_card_ids": item.get("physical_card_ids"),
            "truth": truth,
            "observer": observer,
        })
    scope = evaluation_scope([item["truth"] for item in evaluated])
    declared_ids = [item["video_id"] for item in loaded["rounds"] if item.get("video_id")]
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "declared_unused_video": loaded.get("declared_unused_video", False),
        "declared_video_ids": declared_ids,
        "identity_chain": list(loaded.get("identity_chain") or []),
        "attestation": loaded.get("attestation"),
        "video_binding": loaded.get("video_binding"),
        "source_kind": loaded["source_kind"],
        "evidence_level": loaded.get("evidence_level", "declared"),
        "accepted": False,
        "attested_by": loaded["attestation"]["attested_by"],
        "surrender": surrender,
        "rounds": evaluated,
        "summary": {
            "round_count": len(evaluated),
            "truth_positive": scope["positive"],
            "truth_nonpositive": scope["nonpositive"],
            "truth_unavailable": scope["unavailable"],
            "zero_window_truth": scope["zero_window"],
            "no_positive_signal_detected": scope["no_positive_signal_detected"],
            "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
            "evaluated_count": scope["evaluated_count"],
            "unassessable_count": scope["unassessable_count"],
            "verified_no_positive_over_declared_domain": scope["verified_no_positive_over_declared_domain"],
            "observer": confusion_matrix(evaluated, observer_key="observer"),
            "note": "无未使用录像声明时不得把合成或开发材料写成独立对照；真值不可评不定误报或共同阴性；"
                    "video_id/SHA/路径只是身份链，哈希相符最多 evidence-linked，不能写成 independent_video",
        },
    }
