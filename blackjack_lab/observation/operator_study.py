"""Paired operator-efficiency study. Empty or synthetic runs stay inconclusive.

Auto-prompt must not become the default because a pause-then-reconcile pass
looked faster. Human paired trials are required; this module never flips the
product default.
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
    return record


def trial_from_usage(condition, usage, *, human_run, missed_cards, duplicates, repair_seconds,
                    pause_reconcile_not_realtime=True, now=None):
    """Map the assisted panel usage counters into a study trial. Does not conclude."""
    started = usage.get("started_at")
    elapsed = 0.0 if started is None else max(0.0, float((now or __import__("time").time()) - started))
    return trial(
        condition, human_run=human_run, elapsed_seconds=elapsed,
        keystrokes=int(usage.get("key_presses") or 0),
        clicks=int(usage.get("mouse_clicks") or 0),
        backlog_peak=int(usage.get("max_pending") or 0),
        missed_cards=missed_cards, duplicates=duplicates, repair_seconds=repair_seconds,
        pause_reconcile_not_realtime=pause_reconcile_not_realtime,
    )


def conclude(trials):
    """Never returns auto_prompt_default True. Missing human pairs stay inconclusive."""
    base = {
        "schema": SCHEMA,
        "auto_prompt_default": False,
        "note": "不得把暂停后的对账成功算作实时跟上；未完成配对真人实验前自动提示保持可选",
    }
    trials = list(trials or [])
    if not trials:
        return {**base, "status": "inconclusive", "reason_code": "NEED_PAIRED_HUMAN_TRIALS"}
    if any(not item.get("human_run") for item in trials):
        return {**base, "status": "inconclusive", "reason_code": "SYNTHETIC_NOT_HUMAN"}
    manuals = [item for item in trials if item.get("condition") == "manual"]
    assisted = [item for item in trials if item.get("condition") == "assisted"]
    if not manuals or not assisted:
        return {**base, "status": "inconclusive", "reason_code": "NEED_PAIRED_HUMAN_TRIALS"}
    return {
        **base,
        "status": "recorded",
        "reason_code": "HUMAN_PAIRS_PRESENT_DEFAULT_UNCHANGED",
        "manual_runs": len(manuals),
        "assisted_runs": len(assisted),
    }


def write_export(path, trials):
    body = conclude(trials)
    body["trials"] = list(trials)
    Path(path).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return body
