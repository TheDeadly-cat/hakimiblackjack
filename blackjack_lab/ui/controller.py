"""UI 编排：预演 -> SQLite事务 -> 发布状态；失败不污染内存或历史。"""
from __future__ import annotations
import copy
import uuid
from pathlib import Path
from ..core.rules import RuleProfile
from ..ledger.events import CANDIDATE, CONFIRMED
from ..ledger.ledger import EventLedger, LedgerError
from ..storage.database import LocalStore
from ..storage.export import import_json, import_csv


class SessionController:
    def __init__(self, db_path: str | Path):
        self.store = LocalStore(db_path)
        self.session_id = uuid.uuid4().hex
        self.session_name = "手动录牌会话"
        self.ledger = EventLedger(self.session_id)
        self.ledger.start_session()
        try:
            self.store.save_ledger(self.ledger)
        except Exception:
            self.store.close()
            raise

    @classmethod
    def recover(cls, db_path, session_id):
        obj = cls.__new__(cls)
        obj.store = LocalStore(db_path)
        try:
            obj.load_session(session_id)
        except Exception:
            obj.store.close()
            raise
        return obj

    def load_session(self, session_id):
        candidate = self.store.load_ledger(session_id)
        if not candidate.events:
            raise LedgerError("此会话没有可恢复事件")
        self.ledger = candidate
        self.session_id = session_id
        self.session_name = next((s["name"] for s in self.store.list_sessions() if s["session_id"] == session_id), "恢复会话")

    def list_recoverable(self):
        return self.store.list_sessions()

    def _apply(self, method, *args, **kwargs):
        candidate = copy.deepcopy(self.ledger)
        event = getattr(candidate, method)(*args, **kwargs)
        self.store.save_event(event)
        self.ledger = candidate
        return event

    def new_shoe(self, rules: RuleProfile):
        return self._apply("create_shoe", rules)

    def start_round(self, participants=None):
        return self._apply("start_round", participants)

    def end_round(self):
        event = self._apply("end_round", settle=True)
        seg = self.state().current
        return event, [r for r in seg.settlements if r["round"] == seg.table.round_no]

    def end_round_unsettled(self, reason):
        if not reason.strip():
            raise ValueError("未结算结束必须记录原因")
        return self._apply("end_round", settle=False, reason=reason)

    def end_shoe(self):
        return self._apply("end_shoe")

    def deal_shown(self, seat, rank, hand_id=None, suit=None, track_id=None, confirm_status=CONFIRMED):
        return self._apply("deal", seat, rank, hand_id=hand_id, suit=suit,
                           track_id=track_id, confirm_status=confirm_status)

    def deal_hidden(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, hidden=True, hand_id=hand_id, track_id=track_id)

    def deal_unknown(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, unknown=True, hand_id=hand_id,
                           track_id=track_id, confirm_status=CANDIDATE)

    def reveal(self, target_event_id, rank, suit=None):
        return self._apply("reveal", target_event_id, rank, suit=suit)

    def player_action(self, seat, hand_id, action, extra=None):
        return self._apply("player_action", seat, hand_id, action, extra)

    def peek_negative(self):
        return self._apply("peek_negative")

    def burn(self, count, note=""):
        return self._apply("burn", count, note)

    def mark_gap(self, reason):
        return self._apply("gap", reason)

    def undo_last(self, reason=""):
        return self._apply("undo_last", reason)

    def correct(self, event_id, payload_fix, reason):
        if not reason.strip():
            raise ValueError("纠错必须填写依据或原因")
        return self._apply("correct", event_id, payload_fix, reason)

    def import_file(self, path):
        candidate = import_csv(path) if Path(path).suffix.lower() == ".csv" else import_json(path)
        self.store.save_ledger(candidate)  # 全部验证后原子写入；不覆盖同ID不同内容
        self.ledger = candidate
        self.session_id = candidate.session_id
        self.session_name = "导入会话"
        return candidate

    def state(self):
        return self.ledger.replay()

    def current_rules(self):
        seg = self.state().current
        return seg.rules if seg else None

    def shoe_count(self):
        return len(self.state().segments)

    def close(self):
        self.store.close()
