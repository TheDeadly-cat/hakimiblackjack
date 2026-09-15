"""Unchecked M4 acceptance pack. Software never marks human items passed."""
from __future__ import annotations

from pathlib import Path
from time import time

from .evidence import (
    LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, LEVEL_REVIEWED, EvidenceError,
    classify_artifacts, classify_review, run_manifest, sha256_file,
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
SCOPE_OFFLINE_RESEARCH = "offline_research_declared_rules"
SCOPE_FULLSCREEN_MANUAL = "fullscreen_manual_counting"
SCOPE_TABLE_ASSISTED = "specific_table_assisted_research"
SCOPE_RELEASE = "release_authorization"
SCOPES = (
    SCOPE_OFFLINE_RESEARCH,
    SCOPE_FULLSCREEN_MANUAL,
    SCOPE_TABLE_ASSISTED,
    SCOPE_RELEASE,
)
SCOPE_LABELS = {
    SCOPE_OFFLINE_RESEARCH: "限定规则离线研究版",
    SCOPE_FULLSCREEN_MANUAL: "全屏手动记牌工具",
    SCOPE_TABLE_ASSISTED: "特定桌面辅助研究版",
    SCOPE_RELEASE: "获准发布/交付",
}
HUMAN_CONFIRMATION_PHRASE = "我确认这是人工签收，不是软件自行勾选"
SOFTWARE_ATTESTER_NAMES = frozenset({
    "grok", "chatgpt", "codex", "cursor", "software", "ai", "assistant",
})
DEFAULT_REVIEW_EXCLUDES = (
    "逐牌真值", "无漏帧", "未使用留出", "规则认证", "操作效率", "桌面已证",
)


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
        "scope_signoffs": [],
        "note": "未勾选全局验收包。哈希只证明字节身份，不证明来源真实、授权或从未被训练使用。"
                "软件不得自行把 passed 或 accepted 写成 true；人确认后可保存范围内签收。",
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
    pack.setdefault("scope_signoffs", [])
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


def ingest_inbox(pack, inbox_root):
    """Bind files dropped into per-item inbox folders. Never accepts."""
    root = Path(inbox_root)
    root.mkdir(parents=True, exist_ok=True)
    bound = []
    seen = {
        (item_id, artifact.get("sha256"))
        for item_id, item in pack.get("items", {}).items()
        for artifact in (item.get("artifacts") or [])
    }
    for item_id, _label in ITEMS:
        folder = root / item_id
        folder.mkdir(parents=True, exist_ok=True)
        for path in sorted(p for p in folder.iterdir() if p.is_file() and not p.name.startswith(".")):
            digest = sha256_file(path)
            if (item_id, digest) in seen:
                continue
            pack = bind_item_artifact(
                pack, item_id, path,
                notes="从 M4 收件箱哈希绑定；人尚未核验，不能勾选")
            seen.add((item_id, digest))
            bound.append({"item_id": item_id, "path": str(path.resolve()), "sha256": digest})
    pack["inbox_root"] = str(root.resolve())
    pack["inbox_bound"] = bound
    pack["inbox_is_not_acceptance"] = True
    return _refresh_pack_level(pack)


def record_named_review(pack, item_id, *, attested_by, notes=None, recorded_by="software-recorder",
                        review_scope=None, includes=None, excludes=None, declarant=None,
                        verify_digest=True):
    """Record a named human review of already-linked artifacts. Never accepts the pack.

    attested_by / declarant is the person making the statement. recorded_by is the
    tool that wrote the row. Scope must say what was checked; omitted checks stay excluded.
    """
    if item_id not in dict(ITEMS):
        raise ValueError(f"未知验收项: {item_id}")
    _refresh_pack_level(pack)
    artifacts = pack["items"][item_id].get("artifacts") or []
    binding = classify_review(
        artifacts=artifacts,
        attested_by=attested_by,
        human_reviewed=True,
        accepted=False,
        verify_digest=verify_digest,
    )
    pack["items"][item_id]["binding"] = binding
    pack["items"][item_id]["evidence_level"] = binding["evidence_level"]
    pack["items"][item_id]["attested_by"] = binding["attested_by"]
    pack["items"][item_id]["human_reviewed"] = binding["evidence_level"] == LEVEL_REVIEWED
    pack["items"][item_id]["passed"] = False
    record = {
        "recorded_at": time(),
        "recorded_by": (recorded_by or "software-recorder").strip() or "software-recorder",
        "declarant": (declarant or attested_by or "").strip() or None,
        "review_scope": (review_scope or "artifact-identity-and-stated-range").strip(),
        "includes": list(includes or ("已列出文件的摘要绑定", "声明者所述录像/材料范围")),
        "excludes": list(excludes or DEFAULT_REVIEW_EXCLUDES),
        "artifact_sha256": [item.get("sha256") for item in artifacts if item.get("sha256")],
        "notes": notes,
        "human_reviewed": binding["evidence_level"] == LEVEL_REVIEWED,
        "accepted": False,
    }
    history = list(pack["items"][item_id].get("review_records") or [])
    history.append(record)
    pack["items"][item_id]["review_records"] = history
    pack["items"][item_id]["review_scope"] = record["review_scope"]
    pack["items"][item_id]["review_excludes"] = record["excludes"]
    if notes:
        pack["items"][item_id]["notes"] = notes
    return _refresh_pack_level(pack)


def amend_named_review(pack, item_id, *, attested_by, correction, recorded_by="software-recorder"):
    """Append a narrower correction. History is kept."""
    if item_id not in dict(ITEMS):
        raise ValueError(f"未知验收项: {item_id}")
    if not isinstance(correction, str) or not correction.strip():
        raise EvidenceError("REVIEW_CORRECTION_MISSING", "更正说明不能为空")
    previous = list(pack.get("items", {}).get(item_id, {}).get("review_records") or [])
    notes = "更正（不删除原记录）：" + correction.strip()
    pack = record_named_review(
        pack, item_id, attested_by=attested_by, notes=notes, recorded_by=recorded_by,
        review_scope="correction-of-prior-review", verify_digest=False)
    pack["items"][item_id]["review_records"][-1]["amends_index"] = max(len(previous) - 1, 0)
    pack["items"][item_id]["review_records"][-1]["correction"] = correction.strip()
    return pack


def _require_human_attester(attested_by):
    name = (attested_by or "").strip()
    if not name:
        raise EvidenceError("REVIEWER_MISSING", "必须有具名声明人")
    if name.lower() in SOFTWARE_ATTESTER_NAMES:
        raise EvidenceError("SOFTWARE_CANNOT_SIGN", "软件不能代签人工确认")
    return name


def record_scope_signoff(pack, *, scope, attested_by, code_commit, criteria, result,
                         confirmation_phrase, recorded_by="software-recorder"):
    """Save a bounded human confirmation. Software cannot invent this row."""
    if scope not in SCOPES:
        raise ValueError(f"未知签收范围: {scope}")
    name = _require_human_attester(attested_by)
    if confirmation_phrase != HUMAN_CONFIRMATION_PHRASE:
        raise EvidenceError(
            "CONFIRMATION_PHRASE_REQUIRED",
            f"必须由人抄写确认短语：{HUMAN_CONFIRMATION_PHRASE}")
    if result not in ("accepted", "rejected", "deferred"):
        raise ValueError("签收结果只接受 accepted / rejected / deferred")
    if not isinstance(code_commit, str) or not code_commit.strip():
        raise EvidenceError("SCOPE_COMMIT_REQUIRED", "范围内签收必须绑定精确代码版本")
    if not isinstance(criteria, str) or not criteria.strip():
        raise EvidenceError("SCOPE_CRITERIA_REQUIRED", "范围内签收必须写明验收标准")
    _refresh_pack_level(pack)
    history = list(pack.get("scope_signoffs") or [])
    for previous in history:
        if previous.get("scope") == scope and not previous.get("superseded"):
            previous["superseded"] = True
            previous["superseded_reason"] = "replaced_by_later_human_record"
    history.append({
        "scope": scope,
        "scope_label": SCOPE_LABELS[scope],
        "attested_by": name,
        "recorded_by": (recorded_by or "software-recorder").strip() or "software-recorder",
        "code_commit": code_commit.strip(),
        "criteria": criteria.strip(),
        "result": result,
        "confirmation_kind": "human_explicit",
        "confirmation_phrase": HUMAN_CONFIRMATION_PHRASE,
        "recorded_at": time(),
        "superseded": False,
        "accepted_global_pack": False,
    })
    pack["scope_signoffs"] = history
    pack["accepted"] = False
    pack["passed"] = False
    return _refresh_pack_level(pack)


def _active_signoff(signoffs, scope, code_commit):
    for record in reversed(list(signoffs or [])):
        if record.get("superseded"):
            continue
        if record.get("scope") != scope:
            continue
        if record.get("code_commit") != code_commit:
            continue
        if record.get("confirmation_kind") != "human_explicit":
            continue
        return record
    return None


def freeze_status(*, code_commit=None, dirty_worktree=True, tests_bound_to_sha=False,
                  pr_body_updated=False, human_commit_authorized=False, m4_pack=None,
                  scope=None):
    """Record whether a named scope can freeze after a human confirmation.

    Software never invents the confirmation. Global pack.accepted stays false
    unless a human release signoff exists; a hand-edited accepted=true is ignored.
    """
    if scope is not None and scope not in SCOPES:
        raise ValueError(f"未知冻结范围: {scope}")
    if human_commit_authorized is True and dirty_worktree is not False:
        raise EvidenceError("FREEZE_DIRTY", "脏工作树不能冒充已授权冻结")
    signoffs = list((m4_pack or {}).get("scope_signoffs") or [])
    technical = []
    if not code_commit:
        technical.append("missing_code_commit")
    if dirty_worktree is not False:
        technical.append("dirty_worktree")
    if tests_bound_to_sha is not True:
        technical.append("tests_not_bound_to_sha")
    if pr_body_updated is not True:
        technical.append("pr_body_not_updated")
    blockers = list(technical)
    claimed_accept = bool(m4_pack is not None and m4_pack.get("accepted") is True)
    release = _active_signoff(signoffs, SCOPE_RELEASE, code_commit)
    table = _active_signoff(signoffs, SCOPE_TABLE_ASSISTED, code_commit)
    if claimed_accept and not (release and release.get("result") == "accepted"):
        blockers.append("untrusted_global_accepted_without_human_signoff")
    target = scope or SCOPE_RELEASE
    signed = _active_signoff(signoffs, target, code_commit)
    if not signed or signed.get("result") != "accepted":
        blockers.append("scope_not_signed_by_human" if scope else "release_not_signed_by_human")
    if target == SCOPE_RELEASE:
        if human_commit_authorized is not True:
            blockers.append("no_human_commit_authorization")
        if not table or table.get("result") != "accepted":
            blockers.append("m4_materials_not_accepted")
    ready = not blockers
    return {
        "ready": ready,
        "accepted": False,
        "technical_ready": not technical,
        "scope": target,
        "scope_label": SCOPE_LABELS[target],
        "scope_accepted": bool(signed and signed.get("result") == "accepted"),
        "release_authorized": bool(release and release.get("result") == "accepted"
                                   and human_commit_authorized is True
                                   and dirty_worktree is False),
        "blockers": blockers,
        "code_commit": code_commit,
        "dirty_worktree": dirty_worktree,
        "shipped_head": SHIPPED_HEAD,
        "product": PRODUCT_VERSION,
        "sqlite_schema": SQLITE_SCHEMA,
        "predeal_max_remaining": PREDEAL_MAX_REMAINING,
        "interactive_exact_budget_seconds": INTERACTIVE_EXACT_BUDGET_SECONDS,
        "note": "技术完成、范围内人工签收和发布授权是三个状态。软件不得代签。"
                "手改 accepted=true 不能当作签收。",
    }
