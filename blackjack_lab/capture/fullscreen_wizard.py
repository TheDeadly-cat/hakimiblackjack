"""One-action-at-a-time F11 trial. Records evidence; never marks accepted."""
from __future__ import annotations

from time import time

from .fullscreen_acceptance import ITEMS, empty_evidence, report

SCHEMA = "hakimi-fullscreen-wizard-v1"
STATUS_PENDING = "pending"
STATUS_RECORDED = "recorded"
STATUS_FAILED = "failed"
STATUS_UNTESTED = "skipped_untested"

REQUIRED_STEPS = (
    {"id": "enter_f11", "prompt": "进入 F11 或网页全屏",
     "maps_to": "f11_or_page_fullscreen"},
    {"id": "invoke_panel", "prompt": "呼出小面板",
     "maps_to": "hotkey_with_browser_focus"},
    {"id": "enter_two_cards", "prompt": "输入两张牌",
     "maps_to": "ime_and_held_keys"},
    {"id": "correct_one_card", "prompt": "故意改错一张并纠正",
     "maps_to": "ime_and_held_keys"},
    {"id": "return_to_browser", "prompt": "切回浏览器",
     "maps_to": "rebind_after_round_end"},
    {"id": "interrupt_source", "prompt": "中断来源",
     "maps_to": "browser_minimized_or_dropped"},
)
OPTIONAL_STEPS = (
    {"id": "dpi_variants", "prompt": "在 100%/125%/150% DPI 下各测一次（单屏用户可标未测）",
     "maps_to": "dpi_100_125_150"},
    {"id": "multi_monitor", "prompt": "拖到第二块显示器（没有第二屏可标未测）",
     "maps_to": "drag_and_multi_monitor"},
)
ALL_STEPS = REQUIRED_STEPS + OPTIONAL_STEPS
REQUIRED_IDS = frozenset(item["id"] for item in REQUIRED_STEPS)
OPTIONAL_IDS = frozenset(item["id"] for item in OPTIONAL_STEPS)
STEP_BY_ID = {item["id"]: item for item in ALL_STEPS}
NOTE = (
    "向导只记录你实际做了哪一步。软件不能把 F11 写成通过。"
    "未测的 DPI 或多显示器保持未测，不要求为此购买硬件。"
    "暂停后补齐不能当成实时跟上。"
)


def _empty_row(spec):
    return {
        "id": spec["id"],
        "prompt": spec["prompt"],
        "maps_to": spec["maps_to"],
        "required": spec["id"] in REQUIRED_IDS,
        "status": STATUS_PENDING,
        "recorded_at": None,
        "notes": "",
        "evidence_path": None,
        "passed": False,
    }


def start_session(*, environment=None, code_version=None, recorded_by="software-recorder"):
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "evidence_level": "missing",
        "code_version": code_version,
        "recorded_by": recorded_by,
        "started_at": time(),
        "environment": dict(environment or {"not_acceptance": True}),
        "steps": [_empty_row(spec) for spec in ALL_STEPS],
        "current_index": 0,
        "note": NOTE,
    }


def current_step(session):
    steps = session["steps"]
    index = int(session.get("current_index") or 0)
    if index < 0 or index >= len(steps):
        return None
    return steps[index]


def _row(session, step_id):
    for row in session["steps"]:
        if row["id"] == step_id:
            return row
    raise ValueError(f"未知向导步骤: {step_id}")


def record_step(session, step_id, *, notes="", evidence_path=None, failed=False,
                observation=None):
    if step_id not in STEP_BY_ID:
        raise ValueError(f"未知向导步骤: {step_id}")
    row = _row(session, step_id)
    row["status"] = STATUS_FAILED if failed else STATUS_RECORDED
    row["recorded_at"] = time()
    row["notes"] = notes or ""
    row["evidence_path"] = evidence_path
    row["observation"] = observation
    row["passed"] = False
    session["accepted"] = False
    session["passed"] = False
    _advance(session)
    return conclude_session(session)


def skip_untested(session, step_id, *, reason):
    if step_id not in OPTIONAL_IDS:
        raise ValueError("只有 DPI/多显示器等可选步骤可以标未测")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("未测必须写明原因")
    row = _row(session, step_id)
    row["status"] = STATUS_UNTESTED
    row["recorded_at"] = time()
    row["notes"] = reason.strip()
    row["passed"] = False
    session["accepted"] = False
    _advance(session)
    return conclude_session(session)


def _advance(session):
    for index, row in enumerate(session["steps"]):
        if row["status"] == STATUS_PENDING:
            session["current_index"] = index
            return
    session["current_index"] = len(session["steps"])


def conclude_session(session):
    rows = session["steps"]
    required = [row for row in rows if row["required"]]
    optional = [row for row in rows if not row["required"]]
    required_complete = all(row["status"] == STATUS_RECORDED for row in required)
    failed = [row["id"] for row in rows if row["status"] == STATUS_FAILED]
    untested = [row["id"] for row in optional if row["status"] == STATUS_UNTESTED]
    recorded = [row["id"] for row in rows if row["status"] == STATUS_RECORDED]
    if failed:
        level = "declared"
    elif recorded:
        level = "declared"
    else:
        level = "missing"
    session["accepted"] = False
    session["passed"] = False
    session["evidence_level"] = level
    session["required_complete"] = required_complete
    session["failed_steps"] = failed
    session["untested_optional"] = untested
    session["recorded_steps"] = recorded
    session["ready_for_human_fullscreen_signoff"] = bool(required_complete and not failed)
    session["note"] = NOTE
    return session


def as_fullscreen_evidence(session):
    """Map wizard rows onto the checklist. Checkboxes stay false."""
    evidence = empty_evidence()
    notes_by_item = {item: [] for item in ITEMS}
    for row in session["steps"]:
        item = row["maps_to"]
        if item not in evidence:
            continue
        if row.get("evidence_path") and not evidence[item].get("evidence_path"):
            evidence[item]["evidence_path"] = row["evidence_path"]
        notes_by_item[item].append(f"{row['id']}:{row['status']}")
        evidence[item]["passed"] = False
        evidence[item]["wizard_status"] = row["status"]
    for item, notes in notes_by_item.items():
        extra = "；".join(notes)
        if extra:
            evidence[item]["notes"] = extra
        evidence[item]["passed"] = False
    body = report(evidence, session.get("environment"))
    body["wizard"] = conclude_session(session)
    body["accepted"] = False
    return body
