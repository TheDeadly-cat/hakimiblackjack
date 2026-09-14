"""Real-table rule archive. The research template is not a platform table."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .rules import RuleProfile


ARCHIVE_SCHEMA = "hakimi-table-rule-archive-v1"
RESEARCH_MARKERS = ("不代表平台", "研究模板", "自建研究桌规")


class TableArchiveError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class TableRuleArchive:
    table_id: str
    rule_source: str
    source_version: str
    verify_date: str
    rules: RuleProfile
    notes: str = ""
    schema: str = ARCHIVE_SCHEMA

    def __post_init__(self):
        for name in ("table_id", "rule_source", "source_version", "verify_date"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise TableArchiveError("ARCHIVE_INCOMPLETE", "真实桌档案必须有桌标识、来源、版本和核对日期")
        if any(marker in self.rule_source for marker in RESEARCH_MARKERS):
            raise TableArchiveError("RESEARCH_TEMPLATE_NOT_A_TABLE", "研究模板不能当作真实桌规则档案")
        if self.rules.table_id != self.table_id:
            raise TableArchiveError("TABLE_MISMATCH", "规则快照的桌标识必须与档案一致")
        if self.rules.rule_source != self.rule_source:
            raise TableArchiveError("SOURCE_MISMATCH", "规则快照的来源必须与档案一致")
        if self.rules.verify_date != self.verify_date:
            raise TableArchiveError("DATE_MISMATCH", "规则快照的核对日期必须与档案一致")
        if self.schema != ARCHIVE_SCHEMA:
            raise TableArchiveError("ARCHIVE_SCHEMA", "不支持的规则档案格式")

    def to_dict(self):
        return {
            "schema": self.schema,
            "table_id": self.table_id,
            "rule_source": self.rule_source,
            "source_version": self.source_version,
            "verify_date": self.verify_date,
            "notes": self.notes,
            "rules": json.loads(self.rules.to_json()),
        }


def archive_from_profile(rules: RuleProfile, source_version: str, notes: str = "") -> TableRuleArchive:
    refuse_research_template(rules)
    return TableRuleArchive(
        table_id=rules.table_id or "",
        rule_source=rules.rule_source or "",
        source_version=source_version,
        verify_date=rules.verify_date or "",
        rules=rules,
        notes=notes,
    )


def refuse_research_template(rules: RuleProfile):
    source = rules.rule_source or ""
    if any(marker in source for marker in RESEARCH_MARKERS):
        raise TableArchiveError("RESEARCH_TEMPLATE_NOT_A_TABLE", "研究模板不能当作真实桌规则档案")
    return rules


def missing_archive():
    return {
        "schema": ARCHIVE_SCHEMA,
        "status": "missing",
        "reason_code": "NO_VERIFIED_TABLE_ARCHIVE",
        "reason": "尚无带来源与核对日期的真实桌档案；分析可继续使用用户主动选择的研究模板，但不能把该模板写成平台桌规",
    }


def load_archive(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != ARCHIVE_SCHEMA:
        raise TableArchiveError("ARCHIVE_SCHEMA", "不支持的规则档案格式")
    rules = RuleProfile(**data["rules"])
    archive = TableRuleArchive(
        table_id=data["table_id"], rule_source=data["rule_source"],
        source_version=data["source_version"], verify_date=data["verify_date"],
        rules=rules, notes=data.get("notes") or "",
    )
    return archive


def save_archive(archive: TableRuleArchive, path):
    Path(path).write_text(json.dumps(archive.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path
