"""SQLite 事件持久化：会话内序号唯一，事务写入、显式冲突与可恢复迁移。"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Optional

from ..ledger.events import Event
from ..ledger.ledger import EventLedger
from .safe_files import register_database, unregister_database

SCHEMA_VERSION = 2
_EVENTS = """CREATE TABLE events (
    event_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    etype TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    event_time REAL, observed_at REAL,
    session_id TEXT NOT NULL, shoe_id TEXT, round_id TEXT,
    source TEXT, confirm_status TEXT, evidence TEXT, rule_version TEXT,
    UNIQUE(session_id, seq)
)"""


class LocalStore:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self._closed = False
        self.migration_backup = None
        try:
            self._initialize()
            register_database(self.db_path)
        except Exception:
            self.conn.close()
            raise

    def _initialize(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "meta" in tables:
            version = self.get_meta("schema_version")
            if version not in ("1", "2"):
                raise ValueError(f"不支持的数据库版本: {version}")
            if version == "1":
                # SQLite backup 包含已提交 WAL 内容；保留原版副本后才迁移。
                target = Path(self.db_path).with_name(Path(self.db_path).name + ".pre-v2-" + uuid.uuid4().hex[:8] + ".bak")
                with closing(sqlite3.connect(str(target))) as backup:
                    self.conn.backup(backup)
                self.migration_backup = str(target)
                with self.conn:
                    self.conn.execute("BEGIN IMMEDIATE")
                    self.conn.execute("ALTER TABLE events RENAME TO events_v1")
                    self.conn.execute(_EVENTS)
                    self.conn.execute("INSERT INTO events SELECT * FROM events_v1")
                    self.conn.execute("DROP TABLE events_v1")
                    self._set_meta("schema_version", "2")
        elif tables:
            raise ValueError("数据库缺少版本信息，请保留文件并人工核对")
        else:
            with self.conn:
                self.conn.execute("BEGIN IMMEDIATE")
                self.conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                self.conn.execute("CREATE TABLE sessions (session_id TEXT PRIMARY KEY, name TEXT, created_at REAL, note TEXT)")
                self.conn.execute(_EVENTS)
                self._set_meta("schema_version", "2")
        with self.conn:
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_events_session_seq ON events(session_id,seq)")

    def _set_meta(self, key, value):
        self.conn.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_meta(self, key):
        row = self.conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def create_session(self, session_id, name="", note=""):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO sessions(session_id,name,created_at,note) VALUES(?,?,?,?)", (session_id, name, time.time(), note))

    def list_sessions(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT s.*, COUNT(e.event_id) AS event_count, MAX(e.event_time) AS updated_at "
            "FROM sessions s LEFT JOIN events e ON e.session_id=s.session_id "
            "GROUP BY s.session_id ORDER BY COALESCE(MAX(e.event_time),s.created_at), s.created_at")]

    @staticmethod
    def _decode(row):
        data = dict(row)
        data["payload"] = json.loads(data.pop("payload_json"))
        return Event.from_dict(data)

    def _insert(self, ev):
        Event.from_dict(ev.to_dict())
        if not ev.session_id or type(ev.seq) is not int or ev.seq <= 0:
            raise ValueError("事件缺少有效会话或序号")
        row = self.conn.execute("SELECT * FROM events WHERE event_id=?", (ev.event_id,)).fetchone()
        if row:
            if self._decode(row).to_dict() != ev.to_dict():
                raise ValueError("事件ID已存在且内容不同，拒绝覆盖审计历史")
            return False
        self.conn.execute(
            "INSERT INTO events(event_id,seq,etype,payload_json,event_time,observed_at,session_id,shoe_id,round_id,source,confirm_status,evidence,rule_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ev.event_id, ev.seq, ev.etype, json.dumps(ev.payload, ensure_ascii=False),
             ev.event_time, ev.observed_at, ev.session_id, ev.shoe_id, ev.round_id,
             ev.source, ev.confirm_status, ev.evidence, ev.rule_version))
        return True

    def save_event(self, ev):
        with self.conn:
            return self._insert(ev)

    def save_ledger(self, ledger):
        ledger.replay()
        # 只接受现有记录的完整前缀延长，导入不能删除或篡改历史。
        existing = self.load_events(ledger.session_id)
        if [e.to_dict() for e in existing] != ledger.to_list()[:len(existing)]:
            raise ValueError("导入与本地会话历史冲突，请保留两份记录核对")
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO sessions(session_id,name,created_at,note) VALUES(?,?,?,?)", (ledger.session_id, "导入/恢复会话", time.time(), ""))
            return sum(self._insert(ev) for ev in ledger.events)

    def load_events(self, session_id: Optional[str] = None, through_seq=None):
        if through_seq is not None:
            rows = self.conn.execute("SELECT * FROM events WHERE session_id=? AND seq<=? ORDER BY seq", (session_id, through_seq)).fetchall()
            return [self._decode(r) for r in rows]
        if session_id is not None:
            rows = self.conn.execute("SELECT * FROM events WHERE session_id=? ORDER BY seq", (session_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY session_id,seq").fetchall()
        return [self._decode(r) for r in rows]

    def load_ledger(self, session_id, through_seq=None):
        return EventLedger.from_list(session_id, [e.to_dict() for e in self.load_events(session_id, through_seq)])

    def diagnose_session(self, session_id):
        rows = [dict(row) for row in self.conn.execute("SELECT * FROM events WHERE session_id=? ORDER BY seq", (session_id,))]
        error = None
        try:
            self.load_ledger(session_id)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        return {"format": "hakimi-readonly-diagnostic-v1", "session_id": session_id,
                "valid_for_recording": error is None and bool(rows), "error": error,
                "raw_database_rows": rows, "note": "只读诊断，原始payload_json原样保留；此文件不能当已验证会话导入分析"}

    def event_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]

    def backup(self, path: str | Path):
        path = Path(path).resolve()
        if path == Path(self.db_path).resolve() or path.exists():
            raise ValueError("备份必须保存到新的文件路径")
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(str(path))) as backup:
            self.conn.backup(backup)
        return path

    def close(self):
        if not self._closed:
            self.conn.close()
            unregister_database(self.db_path)
            self._closed = True
