"""Judge a --usage-session evidence folder. Never marks accepted or live catch-up."""
from __future__ import annotations

import json
from pathlib import Path

from .fullscreen_wizard import REQUIRED_IDS

SCHEMA = "hakimi-usage-session-report-v1"
NOTE = (
    "软件向导测试不能代替真人 F11。"
    "looks_monitor_sized 只是几何记录，不是验收通过。"
    "暂停后补齐不能当成实时跟上。"
)


def _load(path):
    if not path.is_file():
        return None
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (TypeError, ValueError, OSError):
        return {"_error": "json_decode", "path": str(path)}
    return body if isinstance(body, dict) else {"_error": "not_object", "path": str(path)}


def _jsonl(path):
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except (TypeError, ValueError):
            rows.append({"_error": "json_decode", "raw": line})
    return rows


def inspect_usage_session(folder):
    folder = Path(folder)
    session = _load(folder / "session.json") or {}
    usage_run = _load(folder / "usage_run.json") or _load(folder / "usage-final.json") or {}
    wizard = _load(folder / "fullscreen-wizard.json") or _load(folder / "fullscreen-wizard-live.json") or {}
    wizard_session = wizard.get("wizard") if isinstance(wizard.get("wizard"), dict) else wizard
    log = _jsonl(folder / "wizard-log.jsonl")
    db = Path(str(session.get("db") or usage_run.get("db") or folder / "lab.db")).resolve()
    default_raw = session.get("default_user_db") or usage_run.get("default_user_db")
    default_db = Path(str(default_raw)).resolve() if default_raw else None
    recorded = list(
        usage_run.get("fullscreen_recorded_steps")
        or wizard_session.get("recorded_steps")
        or []
    )
    enter_f11 = usage_run.get("enter_f11") if isinstance(usage_run.get("enter_f11"), dict) else {}
    looks = bool(enter_f11.get("looks_monitor_sized"))
    missing = []
    if not folder.is_dir():
        missing.append("folder")
    if not (folder / "session.json").is_file():
        missing.append("session.json")
    if not (folder / "usage_run.json").is_file() and not (folder / "usage-final.json").is_file():
        missing.append("usage_run.json")
    if default_db is not None and db == default_db:
        missing.append("used_user_default_db")
    for step_id in REQUIRED_IDS:
        if step_id not in recorded:
            missing.append(f"step:{step_id}")
    if not looks:
        missing.append("human_f11_geometry")
    accepted_flags = [
        bool(session.get("accepted")),
        bool(usage_run.get("accepted")),
        bool(wizard.get("accepted")),
        bool(wizard_session.get("accepted")),
    ]
    live = bool(usage_run.get("live_catchup"))
    software_usage_test = (
        "usage_run.json" not in missing
        and "used_user_default_db" not in missing
        and "session.json" not in missing
        and not any(accepted_flags)
        and not live
        and all(step_id in recorded for step_id in REQUIRED_IDS)
    )
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "folder": str(folder),
        "db": str(db),
        "uses_user_default_db": bool(default_db is not None and db == default_db),
        "recorded_steps": recorded,
        "required_complete": all(step_id in recorded for step_id in REQUIRED_IDS),
        "enter_f11_looks_monitor_sized": looks,
        "live_catchup": live,
        "software_usage_test_complete": bool(software_usage_test),
        "human_f11_ready_for_signoff": bool(software_usage_test and looks),
        "missing": missing,
        "event_count": usage_run.get("event_count"),
        "current_hands": usage_run.get("current_hands") or [],
        "log_events": [row.get("kind") for row in log if isinstance(row, dict)],
        "note": NOTE,
    }
