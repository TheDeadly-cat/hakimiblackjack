"""Paired operator-efficiency study. Empty or synthetic runs stay inconclusive.

Auto-prompt must not become the default because a pause-then-reconcile pass
looked faster. Matching operator_id / video_id / pair_id strings are only a
declaration; software never certifies paired=True or accepted=True.
"""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA = "hakimi-operator-study-v1"
CONDITIONS = ("manual", "assisted")
REQUIRED = (
    "elapsed_seconds", "keystrokes", "clicks", "backlog_peak",
    "missed_cards", "duplicates", "repair_seconds",
    "pause_reconcile_not_realtime",
)
IDENTITY_FIELDS = ("operator_id", "video_id", "pair_id")


def optional_identity_fields(*, operator_id=None, video_id=None, pair_id=None):
    """Keep non-empty identity strings. Empty/omitted fields stay undeclared."""
    payload = {}
    for name, value in (("operator_id", operator_id), ("video_id", video_id), ("pair_id", pair_id)):
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        payload[name] = value.strip()
    return payload


def _identity_chain(trials):
    return [{name: item.get(name) for name in ("condition",) + IDENTITY_FIELDS} for item in trials]


def trial(condition, *, human_run, **metrics):
    if condition not in CONDITIONS:
        raise ValueError("对照条件只能是 manual 或 assisted")
    missing = [name for name in REQUIRED if name not in metrics]
    if missing:
        raise ValueError("缺少操作者对照字段: " + ",".join(missing))
    if metrics["pause_reconcile_not_realtime"] is not True:
        raise ValueError("必须显式声明：暂停后的对账成功不算实时跟上")
    record = {"schema": SCHEMA, "condition": condition, "human_run": bool(human_run)}
    record.update({name: metrics[name] for name in REQUIRED})
    for name in IDENTITY_FIELDS:
        if name not in metrics:
            continue
        value = metrics[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError("操作者对照身份字段必须是非空字符串")
        record[name] = value.strip()
    return record


def trial_from_usage(condition, usage, *, human_run, missed_cards, duplicates, repair_seconds,
                    pause_reconcile_not_realtime=None, now=None, **extra):
    """Map the assisted panel usage counters into a study trial. Does not conclude."""
    started = usage.get("started_at")
    elapsed = 0.0 if started is None else max(0.0, float((now or __import__("time").time()) - started))
    identity = {name: usage[name] for name in IDENTITY_FIELDS if name in usage}
    identity.update(optional_identity_fields(
        operator_id=extra.get("operator_id"), video_id=extra.get("video_id"),
        pair_id=extra.get("pair_id")))
    return trial(
        condition, human_run=human_run, elapsed_seconds=elapsed,
        keystrokes=int(usage.get("key_presses") or 0),
        clicks=int(usage.get("mouse_clicks") or 0),
        backlog_peak=int(usage.get("max_pending") or 0),
        missed_cards=missed_cards, duplicates=duplicates, repair_seconds=repair_seconds,
        pause_reconcile_not_realtime=pause_reconcile_not_realtime, **identity,
    )


def conclude(trials):
    """Never returns auto_prompt_default True. Missing human pairs stay inconclusive."""
    base = {
        "schema": SCHEMA,
        "auto_prompt_default": False,
        "accepted": False,
        "evidence_level": "missing",
        "paired": False,
        "declared_pair_ids": False,
        "note": "不得把暂停后的对账成功算作实时跟上；具名配对字段仍只是声明，不能改自动提示默认",
    }
    trials = list(trials or [])
    chain = _identity_chain(trials)
    if not trials:
        return {**base, "status": "inconclusive", "reason_code": "NEED_PAIRED_HUMAN_TRIALS",
                "identity_chain": chain}
    if any(not item.get("human_run") for item in trials):
        return {**base, "status": "inconclusive", "reason_code": "SYNTHETIC_NOT_HUMAN",
                "evidence_level": "declared", "identity_chain": chain}
    manuals = [item for item in trials if item.get("condition") == "manual"]
    assisted = [item for item in trials if item.get("condition") == "assisted"]
    if not manuals or not assisted:
        return {**base, "status": "inconclusive", "reason_code": "NEED_PAIRED_HUMAN_TRIALS",
                "evidence_level": "declared", "identity_chain": chain}
    manual_pairs = {item.get("pair_id") for item in manuals if item.get("pair_id")}
    assisted_pairs = {item.get("pair_id") for item in assisted if item.get("pair_id")}
    if manual_pairs or assisted_pairs:
        if manual_pairs != assisted_pairs or not manual_pairs:
            return {**base, "status": "inconclusive", "reason_code": "UNPAIRED_OPERATOR_VIDEO",
                    "evidence_level": "declared", "identity_chain": chain}
        if any(not item.get("operator_id") or not item.get("video_id") or not item.get("pair_id")
               for item in trials):
            return {**base, "status": "inconclusive", "reason_code": "UNPAIRED_OPERATOR_VIDEO",
                    "evidence_level": "declared", "identity_chain": chain}
        declared_pair_ids = True
    else:
        declared_pair_ids = False
    return {
        **base,
        "status": "recorded",
        "reason_code": "HUMAN_PAIRS_PRESENT_DEFAULT_UNCHANGED",
        "evidence_level": "declared",
        "paired": False,
        "declared_pair_ids": declared_pair_ids,
        "identity_chain": chain,
        "manual_runs": len(manuals),
        "assisted_runs": len(assisted),
    }


def write_export(path, trials):
    body = conclude(trials)
    body["trials"] = list(trials)
    Path(path).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return body
