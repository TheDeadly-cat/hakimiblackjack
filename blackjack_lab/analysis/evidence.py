"""Evidence binding levels. Software cannot attest human facts or auto-accept.

declared: a person or test filled in names/flags.
evidence-linked: cited files exist and SHA-256 matches when supplied.
reviewed: a named human marked the linked artifacts reviewed.
accepted: never set by this module; requires the linked+reviewed conditions
and an explicit human accepted=True that this code still will not invent.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from time import time

LEVEL_MISSING = "missing"
LEVEL_DECLARED = "declared"
LEVEL_EVIDENCE_LINKED = "evidence-linked"
LEVEL_REVIEWED = "reviewed"
LEVEL_ACCEPTED = "accepted"
SCHEMA = "hakimi-run-manifest-v1"
LEVELS = (LEVEL_MISSING, LEVEL_DECLARED, LEVEL_EVIDENCE_LINKED, LEVEL_REVIEWED, LEVEL_ACCEPTED)


class EvidenceError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify_artifacts(artifacts, *, verify_digest=True):
    """Classify a list of {path, sha256?, role} without ever returning accepted."""
    artifacts = list(artifacts or [])
    if not artifacts:
        return {
            "evidence_level": LEVEL_MISSING,
            "accepted": False,
            "linked": 0,
            "missing_paths": [],
            "digest_mismatch": [],
            "note": "没有证据文件；只能是 missing",
        }
    missing = []
    mismatch = []
    linked = 0
    declared = 0
    for item in artifacts:
        if not isinstance(item, dict):
            raise EvidenceError("ARTIFACT_INVALID", "证据项必须是对象")
        path = item.get("path")
        declared += 1
        if not path:
            missing.append({"role": item.get("role"), "reason": "empty-path"})
            continue
        file = Path(path)
        if not file.is_file():
            missing.append({"role": item.get("role"), "path": str(path)})
            continue
        expected = item.get("sha256")
        if verify_digest:
            actual = sha256_file(file)
            if expected and str(expected).lower() != actual.lower():
                mismatch.append({"role": item.get("role"), "path": str(path)})
                continue
        elif not expected:
            missing.append({"role": item.get("role"), "path": str(path), "reason": "digest-not-bound"})
            continue
        linked += 1
    if missing or mismatch or linked != declared:
        level = LEVEL_DECLARED if declared else LEVEL_MISSING
    else:
        level = LEVEL_EVIDENCE_LINKED
    return {
        "evidence_level": level,
        "accepted": False,
        "linked": linked,
        "declared": declared,
        "missing_paths": missing,
        "digest_mismatch": mismatch,
        "note": "文件存在且摘要相符只到 evidence-linked；accepted 需要人的核验，软件不得勾选",
    }


def classify_review(*, artifacts, attested_by=None, human_reviewed=False, accepted=False,
                    verify_digest=True):
    binding = classify_artifacts(artifacts, verify_digest=verify_digest)
    if accepted is True:
        raise EvidenceError("AI_CANNOT_ACCEPT", "软件不能把 accepted=true 写成通过")
    if human_reviewed is True and not (isinstance(attested_by, str) and attested_by.strip()):
        raise EvidenceError("REVIEWER_MISSING", "human_reviewed 必须有具名声明人")
    if human_reviewed is True and binding["evidence_level"] == LEVEL_EVIDENCE_LINKED:
        binding["evidence_level"] = LEVEL_REVIEWED
    binding["attested_by"] = (attested_by or "").strip() or None
    binding["human_reviewed"] = bool(human_reviewed)
    binding["accepted"] = False
    return binding


def run_manifest(*, code_commit=None, dirty_worktree=None, rules_digest=None, strategy_id=None,
                 seed=None, n_decks=None, artifacts=None, attested_by=None, human_reviewed=False,
                 extra=None):
    binding = classify_review(artifacts=artifacts, attested_by=attested_by,
                              human_reviewed=human_reviewed, accepted=False)
    return {
        "schema": SCHEMA,
        "created_at": time(),
        "python": sys.version.split()[0],
        "code_commit": code_commit,
        "dirty_worktree": dirty_worktree,
        "rules_digest": rules_digest,
        "strategy_id": strategy_id,
        "seed": seed,
        "n_decks": n_decks,
        "accepted": False,
        "evidence_level": binding["evidence_level"],
        "binding": binding,
        "extra": extra or {},
        "note": "本机 manifest 绑定代码/规则/策略/种子与可选文件摘要；不能代替真实桌验收",
    }


def write_manifest(path, manifest):
    Path(path).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
