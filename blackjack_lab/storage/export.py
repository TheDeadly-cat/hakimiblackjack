# -*- coding: utf-8 -*-
"""JSON / CSV 导入导出（开发大纲 4-B/4-F、V0.1）。

导出内容包含：导出时间、工具版本、会话事件全量、重放后的牌靴组成快照。
所有演示/模拟数据必须在来源字段标明，不与实盘记录混淆。
"""
from __future__ import annotations

import csv
import io
import json
import time
from pathlib import Path
from typing import Optional

from .. import __version__, ENGINE_VERSION
from ..ledger.events import Event
from ..ledger.ledger import EventLedger
from .safe_files import atomic_write, spreadsheet_cell

EXPORT_FORMAT = "hakimi-blackjack-lab-json"
EXPORT_FORMAT_VERSION = 1


def build_export(ledger: EventLedger, session_name: str = "",
                 note: str = "") -> dict:
    replay = ledger.replay()
    shoes = []
    for seg in replay.segments:
        ok, cons_note = seg.shoe.conservation_check()
        shoes.append({
            "shoe_id": seg.shoe_id,
            "n_decks": seg.rules.n_decks,
            "rules_snapshot": json.loads(seg.rules.to_json()),
            "integrity_state": seg.shoe.integrity_state(),
            "conservation_ok": ok,
            "conservation_note": cons_note,
            "remaining": dict(seg.shoe.remaining),
            "exact_out": dict(seg.shoe.exact_out),
            "t_bucket_out": seg.shoe.t_bucket_out,
            "unrevealed_out": seg.shoe.unrevealed_out,
            "burn_unknown": seg.shoe.burn_unknown,
            "gap": seg.shoe.gap,
            "group_remaining": seg.shoe.group_remaining(),
            "round_no": seg.table.round_no,
            "phase": seg.table.phase,
            "closed": seg.closed,
            "unsettled_rounds": seg.unsettled_rounds,
            "unresolved": seg.unresolved,
            "pending_candidates": seg.shoe.pending_candidates,
            "settlements": seg.settlements,
        })
    return {
        "format": EXPORT_FORMAT,
        "format_version": EXPORT_FORMAT_VERSION,
        "tool_version": __version__,
        "engine_version": ENGINE_VERSION,
        "exported_at": time.time(),
        "session_id": ledger.session_id,
        "session_name": session_name,
        "note": note,
        "events": [e.to_dict() for e in ledger.events],
        "shoe_snapshots": shoes,
    }


def export_json(ledger: EventLedger, path: str | Path,
                session_name: str = "", note: str = "") -> Path:
    data = build_export(ledger, session_name=session_name, note=note)
    return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))


def export_csv(ledger: EventLedger, path: str | Path) -> Path:
    cols = ["seq", "event_id", "etype", "event_time", "shoe_id", "round_id",
            "source", "confirm_status", "payload", "event_json"]
    with io.StringIO(newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for ev in sorted(ledger.events, key=lambda e: e.seq):
            w.writerow([spreadsheet_cell(value) for value in [ev.seq, ev.event_id, ev.etype, ev.event_time,
                        ev.shoe_id, ev.round_id, ev.source, ev.confirm_status,
                        json.dumps(ev.payload, ensure_ascii=False), ev.to_json()]])
        data = f.getvalue().encode("utf-8-sig")
    return atomic_write(path, data)


def import_json(path: str | Path) -> EventLedger:
    """从 JSON 导出文件重建账本（用于导入一致性校验）。"""
    data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if data.get("format") != EXPORT_FORMAT:
        raise ValueError("不是本工具导出的 JSON 文件")
    if data.get("format_version") != EXPORT_FORMAT_VERSION:
        raise ValueError("不支持此 JSON 格式版本")
    ledger = EventLedger.from_list(data["session_id"], data["events"])
    return ledger


def import_csv(path: str | Path) -> EventLedger:
    """新增 CSV 的 event_json 列保留完整身份；旧摘要 CSV 仅供阅读。"""
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if "event_json" not in (reader.fieldnames or []):
            raise ValueError("旧版摘要 CSV 不含完整事件，请改用原 JSON 导入")
        events = [json.loads(row["event_json"]) for row in reader]
    if not events:
        raise ValueError("CSV 没有事件")
    return EventLedger.from_list(events[0]["session_id"], events)


def events_to_text_table(events) -> str:
    """供界面时间线使用的简表文本。"""
    out = io.StringIO()
    for ev in events:
        p = ev.payload
        brief = {k: v for k, v in p.items() if not k.startswith("_") and
                 k not in ("rules_snapshot",)}
        out.write(f"#{ev.seq} [{ev.etype}] {json.dumps(brief, ensure_ascii=False)}\n")
    return out.getvalue()
