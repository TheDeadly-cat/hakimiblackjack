"""Unchecked M4 acceptance pack. Software never marks human items passed."""
from __future__ import annotations

from pathlib import Path

from .evidence import (
    LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, LEVEL_REVIEWED, EvidenceError,
    classify_artifacts, run_manifest, sha256_file,
)
from .predeal_contracts import PREDEAL_MAX_REMAINING

SCHEMA = "hakimi-m4-acceptance-pack-v1"
SKIP_SUFFIXES = {".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".log"}
SKIP_DIR_NAMES = {"__pycache__", ".git"}
SKIP_NAMES = {"m4-acceptance-pending.json"}
_LEVEL_RANK = {
    LEVEL_MISSING: 0,
    LEVEL_DECLARED: 1,
    LEVEL_EVIDENCE_LINKED: 2,
    LEVEL_REVIEWED: 3,
}
ITEMS = (
    ("table_rules", "目标桌规则页或可核对规则截图；研究模板不能代替"),
    ("authorized_shoe_video", "从明确开靴到切牌或洗牌的授权原片"),
    ("unused_attestation", "使用前由人登记是否曾用于训练/调参/阈值"),
    ("fullscreen_f11", "用户 Windows 浏览器 F11/源帧/焦点/DPI/热键"),
    ("operator_pairs", "配对真人对照：operator_id、video_id、pair_id"),
)
SHIPPED_HEAD = "3bcd627814aaa3792d4edc24e2ad737cfbbe727d"
PRODUCT_VERSION = "0.2.0b1"
SQLITE_SCHEMA = 2
INTERACTIVE_EXACT_BUDGET_SECONDS = 5.0


def empty_item(label):
    return {
        "label": label,
        "passed": False,
        "evidence_level": LEVEL_MISSING,
        "artifacts": [],
        "notes": "尚未由人提供并核验",
    }


def empty_pack(*, code_commit=None, dirty_worktree=None):
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "evidence_level": LEVEL_MISSING,
        "items": {item_id: empty_item(label) for item_id, label in ITEMS},
        "local_candidates": [],
        "human_blockers": [
            {"item_id": item_id, "label": label, "passed": False,
             "reason": "须由人提供并验收；软件不能勾选"}
            for item_id, label in ITEMS
        ],
        "code_commit": code_commit,
        "dirty_worktree": dirty_worktree,
        "note": "未勾选验收包。哈希只证明字节身份，不证明来源真实、授权或从未被训练使用。"
                "软件不得把 passed 或 accepted 写成 true。",
    }


def hash_tree(root, *, limit=400):
    """Inventory files under root. Role stays unconfirmed; not an unused-video claim."""
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(str(root))
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if path.name in SKIP_NAMES:
            continue
        if path.suffix.lower() in SKIP_SUFFIXES:
            continue
        if len(rows) >= limit:
            rows.append({"path": str(path), "truncated": True, "role": "unconfirmed-local-candidate"})
            break
        rows.append({
            "path": str(path.resolve()),
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "role": "unconfirmed-local-candidate",
        })
    return rows


def build_acceptance_pack(*, code_commit=None, dirty_worktree=None, local_root=None,
                          item_artifacts=None):
    pack = empty_pack(code_commit=code_commit, dirty_worktree=dirty_worktree)
    item_artifacts = item_artifacts or {}
    for item_id, artifacts in item_artifacts.items():
        if item_id not in pack["items"]:
            raise ValueError(f"未知验收项: {item_id}")
        binding = classify_artifacts(artifacts)
        pack["items"][item_id]["artifacts"] = list(artifacts)
        pack["items"][item_id]["evidence_level"] = binding["evidence_level"]
        pack["items"][item_id]["binding"] = binding
        pack["items"][item_id]["passed"] = False
    if local_root is not None:
        pack["local_candidates"] = hash_tree(local_root)
        pack["local_candidate_root"] = str(Path(local_root).resolve())
        pack["local_candidates_are_not_accepted_evidence"] = True
    _refresh_pack_level(pack)
    pack["manifest"] = run_manifest(
        code_commit=code_commit, dirty_worktree=dirty_worktree,
        extra={"acceptance_schema": SCHEMA})
    return pack


def _refresh_pack_level(pack):
    items = pack.setdefault("items", {})
    for item_id, label in ITEMS:
        item = items.setdefault(item_id, empty_item(label))
        item["passed"] = False
        item["label"] = label
    levels = [items[item_id]["evidence_level"] for item_id, _label in ITEMS]
    if pack.get("local_candidates") and all(level == LEVEL_MISSING for level in levels):
        pack["evidence_level"] = LEVEL_DECLARED
    elif all(level == LEVEL_MISSING for level in levels):
        pack["evidence_level"] = LEVEL_MISSING
    else:
        pack["evidence_level"] = min(
            levels, key=lambda level: _LEVEL_RANK.get(level, 0))
    pack["accepted"] = False
    pack["passed"] = False
    pack["human_blockers"] = [
        {"item_id": item_id, "label": label, "passed": False,
         "reason": "须由人提供并验收；软件不能勾选"}
        for item_id, label in ITEMS
    ]
    return pack


def bind_item_artifact(pack, item_id, path, *, role=None, notes=None):
    """Attach a local file to one M4 item. Never marks the item or pack passed."""
    if item_id not in dict(ITEMS):
        raise ValueError(f"未知验收项: {item_id}")
    _refresh_pack_level(pack)
    file = Path(path)
    if not file.is_file():
        raise FileNotFoundError(str(path))
    artifact = {
        "path": str(file.resolve()),
        "sha256": sha256_file(file),
        "bytes": file.stat().st_size,
        "role": role or item_id,
    }
    artifacts = list(pack["items"][item_id].get("artifacts") or []) + [artifact]
    binding = classify_artifacts(artifacts)
    pack["items"][item_id]["artifacts"] = artifacts
    pack["items"][item_id]["evidence_level"] = binding["evidence_level"]
    pack["items"][item_id]["binding"] = binding
    pack["items"][item_id]["passed"] = False
    if notes:
        pack["items"][item_id]["notes"] = notes
    return _refresh_pack_level(pack)


def freeze_status(*, code_commit=None, dirty_worktree=True, tests_bound_to_sha=False,
                  pr_body_updated=False, human_commit_authorized=False, m4_pack=None):
    """M5 freeze cannot complete itself. Software never reports ready=True."""
    if m4_pack is not None and m4_pack.get("accepted") is True:
        raise EvidenceError("AI_CANNOT_ACCEPT", "冻结不得依赖软件把 M4 写成通过")
    if human_commit_authorized is True and dirty_worktree is not False:
        raise EvidenceError("FREEZE_DIRTY", "脏工作树不能冒充已授权冻结")
    blockers = []
    if not code_commit:
        blockers.append("missing_code_commit")
    if dirty_worktree is not False:
        blockers.append("dirty_worktree")
    if tests_bound_to_sha is not True:
        blockers.append("tests_not_bound_to_sha")
    if pr_body_updated is not True:
        blockers.append("pr_body_not_updated")
    if human_commit_authorized is not True:
        blockers.append("no_human_commit_authorization")
    blockers.append("m4_materials_not_accepted")
    return {
        "ready": False,
        "accepted": False,
        "blockers": blockers,
        "code_commit": code_commit,
        "dirty_worktree": dirty_worktree,
        "shipped_head": SHIPPED_HEAD,
        "product": PRODUCT_VERSION,
        "sqlite_schema": SQLITE_SCHEMA,
        "predeal_max_remaining": PREDEAL_MAX_REMAINING,
        "interactive_exact_budget_seconds": INTERACTIVE_EXACT_BUDGET_SECONDS,
        "note": "M5 冻结需要干净单一 SHA、绑定测试、已验收真实材料与人工提交授权。"
                "本函数在软件侧永远 ready=False。",
    }
