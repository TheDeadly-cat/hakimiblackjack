"""Per-file material roles. Reuses existing bindings; never rehashes or accepts."""
from __future__ import annotations

from pathlib import Path

SCHEMA = "hakimi-material-role-inventory-v1"
ROLES = ("development", "candidate", "holdout")
DEVELOPMENT_TOKENS = ("12.58.11.02",)
NOTE = (
    "角色未由你确认前，不训练、不调阈值、不拿来挑模型。"
    "12.58.11.02 已用于 navy-live 开发，不能登记为未使用留出。"
    "拆段、改名或复制不会自动变成两个独立样本。"
    "本表复用已有 SHA，不重新哈希源片。"
)


def _filename(path):
    return Path(path or "").name


def suggested_role(filename):
    name = str(filename or "")
    if any(token in name for token in DEVELOPMENT_TOKENS):
        return "development"
    return "candidate"


def refuse_holdout(filename):
    name = str(filename or "")
    return any(token in name for token in DEVELOPMENT_TOKENS)


def inventory_from_pack(pack, *, recorded_by="software-recorder"):
    """Build a confirmable table from already-bound artifacts. Does not hash files."""
    item = (pack or {}).get("items", {}).get("authorized_shoe_video") or {}
    rows = []
    for artifact in item.get("artifacts") or []:
        filename = _filename(artifact.get("path"))
        role = suggested_role(filename)
        rows.append({
            "filename": filename,
            "path": artifact.get("path"),
            "sha256": artifact.get("sha256"),
            "bytes": artifact.get("bytes"),
            "suggested_role": role,
            "role": role,
            "role_confirmed_by": None,
            "holdout_forbidden": refuse_holdout(filename),
            "usage_note": (
                "已用于 navy-live 开发，不能登记为未使用留出"
                if role == "development" else
                "用途未确认；暂不用于选模型或调阈值"
            ),
            "content_check": "pending",
            "accepted": False,
        })
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "recorded_by": recorded_by,
        "verify_digest": False,
        "rows": rows,
        "note": NOTE,
    }


def set_role(inventory, filename, role, *, confirmed_by):
    if role not in ROLES:
        raise ValueError("材料角色只能是 development / candidate / holdout")
    if not confirmed_by or str(confirmed_by).strip().lower() in {
            "grok", "chatgpt", "codex", "cursor", "software", "ai", "assistant"}:
        raise ValueError("用途必须由人确认，软件不能代签")
    if role == "holdout" and refuse_holdout(filename):
        raise ValueError("12.58.11.02 已用于 navy-live 开发，不能登记为未使用留出")
    for row in inventory.get("rows") or []:
        if row.get("filename") != filename:
            continue
        row["role"] = role
        row["role_confirmed_by"] = str(confirmed_by).strip()
        row["accepted"] = False
        inventory["accepted"] = False
        inventory["passed"] = False
        return row
    raise KeyError(filename)
