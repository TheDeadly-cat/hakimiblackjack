"""UI 编排：预演 -> SQLite事务 -> 发布状态；失败不污染内存或历史。"""
from __future__ import annotations
import copy
import uuid
from pathlib import Path
from ..core.rules import RuleProfile
from ..ledger.events import CANDIDATE, CONFIRMED, SOURCE_MANUAL
from ..ledger.ledger import EventLedger, LedgerError
from ..storage.database import LocalStore
from ..storage.export import import_json, import_csv
from ..storage.analysis_snapshots import AnalysisSnapshots
from ..analysis.information import build_input
from ..storage.safe_files import atomic_write
from .deal_entry import RoundEntryPlan
import json


class SessionController:
    def __init__(self, db_path: str | Path, recording_source=SOURCE_MANUAL):
        self.store = LocalStore(db_path)
        self.commit_revision = 0
        self._context_revision = 0
        self._context_listeners = []
        self.recording_source = recording_source
        self.analysis_store = AnalysisSnapshots(str(Path(db_path).resolve()) + ".analysis")
        self.session_id = uuid.uuid4().hex
        self.session_name = "手动录牌会话"
        self.ledger = EventLedger(self.session_id)
        self.ledger.start_session().source = recording_source
        self.entry_plan = None
        try:
            self.store.save_ledger(self.ledger)
        except Exception:
            self.store.close()
            raise

    @classmethod
    def recover(cls, db_path, session_id):
        obj = cls.__new__(cls)
        obj.store = LocalStore(db_path)
        obj.commit_revision = 0
        obj._context_revision = 0
        obj._context_listeners = []
        obj.recording_source = SOURCE_MANUAL
        obj.analysis_store = AnalysisSnapshots(str(Path(db_path).resolve()) + ".analysis")
        obj.entry_plan = None
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
        self._load_entry_plan()
        self._publish_context_change()

    @property
    def context_token(self):
        """Read authoritative current identity without depending on any view cache."""
        return (self.session_id, self._context_revision, self.commit_revision,
                self.ledger.events[-1].event_id if self.ledger.events else None)

    def add_context_listener(self, listener):
        self._context_listeners.append(listener)

    def remove_context_listener(self, listener):
        self._context_listeners.remove(listener)

    def _publish_context_change(self):
        self._context_revision += 1
        # The durable commit and in-memory publication have already succeeded.
        # Notification errors must never roll back or repeat that commit.
        for listener in tuple(self._context_listeners):
            listener()

    def list_recoverable(self):
        return self.store.list_sessions()

    def _apply(self, method, *args, **kwargs):
        candidate = copy.deepcopy(self.ledger)
        event = getattr(candidate, method)(*args, **kwargs)
        if event.event_id not in self.ledger._ids:
            event.source = self.recording_source
        self.store.save_event(event)
        self.ledger = candidate
        self.commit_revision += 1
        self._publish_context_change()
        return event

    def new_shoe(self, rules: RuleProfile):
        return self._apply("create_shoe", rules)

    def start_round(self, participants=None, *, my_seat=None, deal_direction="forward"):
        event = self._apply("start_round", participants)
        seg = self.state().current
        seat = my_seat or (list(participants)[0] if participants else "玩家1")
        try:
            self.entry_plan = RoundEntryPlan.freeze(
                session_id=self.session_id, shoe_id=seg.shoe_id, round_id=seg.round_id,
                selected_seats=seg.table.participants, my_seat=seat,
                deal_direction=deal_direction)
            self.save_entry_plan()
        except Exception:
            self.entry_plan = RoundEntryPlan.unaligned(
                session_id=self.session_id, shoe_id=seg.shoe_id, round_id=seg.round_id, my_seat=seat)
            raise
        return event

    def end_round(self):
        event = self._apply("end_round", settle=True, observation_status="complete")
        seg = self.state().current
        return event, [r for r in seg.settlements if r["round"] == seg.table.round_no]

    def end_round_unsettled(self, reason, observation_status="unknown"):
        if not reason.strip():
            raise ValueError("未结算结束必须记录原因")
        return self._apply("end_round", settle=False, reason=reason, observation_status=observation_status)

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
        event = self._apply("undo_last", reason)
        if self.entry_plan:
            target = event.payload.get("target_event_id")
            if target:
                self.entry_plan.undo_event(target)
            self.entry_plan.reconcile(self.live_card_event_ids())
            self.save_entry_plan()
        return event

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
        self.commit_revision += 1
        self._publish_context_change()
        return candidate

    def analysis_input(self, seat, hand_id=None, through_seq=None):
        return build_input(self.ledger, seat, hand_id, through_seq)

    def recompute_input(self, saved):
        original = saved["result"]["input"]
        ledger = self.store.load_ledger(original["session_id"], original["through_seq"])
        if not self.analysis_store.matches_prefix(saved, ledger):
            raise LedgerError("原分析关联的事件前缀摘要不匹配，拒绝复算")
        return build_input(ledger, original["seat"], original["hand_id"], original["through_seq"])

    def export_diagnostic(self, session_id, path):
        data = self.store.diagnose_session(session_id)
        return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))

    def state(self):
        return self.ledger.replay()

    def current_rules(self):
        seg = self.state().current
        return seg.rules if seg else None

    def shoe_count(self):
        return len(self.state().segments)

    def _entry_plan_dir(self):
        root = Path(self.store.db_path).resolve()
        return root.with_name(root.name + ".deal_plans")

    def _current_round_id(self):
        if self.entry_plan and self.entry_plan.round_id:
            return self.entry_plan.round_id
        seg = self.state().current
        return getattr(seg, "round_id", None) if seg else None

    def _entry_plan_path(self, round_id=None):
        rid = round_id or self._current_round_id() or "unaligned"
        return self._entry_plan_dir() / f"{self.session_id}.{rid}.json"

    def live_card_event_ids(self):
        from ..ledger.events import CARD_DEALT
        voided = self.ledger._voided_ids()
        return [ev.event_id for ev in self.ledger.events
                if ev.etype == CARD_DEALT and ev.event_id not in voided]

    def save_entry_plan(self):
        if self.entry_plan is None:
            return
        payload = json.dumps(self.entry_plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        atomic_write(self._entry_plan_path(self.entry_plan.round_id or "unaligned"),
                     payload.encode("utf-8"))

    def _load_entry_plan(self):
        seg = self.state().current
        shoe_id = getattr(seg, "shoe_id", "") if seg else ""
        round_id = getattr(seg, "round_id", "") if seg else ""
        path = self._entry_plan_path(round_id or None)
        if not path.exists():
            self.entry_plan = RoundEntryPlan.unaligned(
                session_id=self.session_id, shoe_id=shoe_id, round_id=round_id)
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        plan = RoundEntryPlan.from_dict(data)
        if plan.session_id != self.session_id or (round_id and plan.round_id != round_id):
            self.entry_plan = RoundEntryPlan.unaligned(
                session_id=self.session_id, shoe_id=shoe_id, round_id=round_id)
            return
        plan.reconcile(self.live_card_event_ids())
        self.entry_plan = plan

    def close(self):
        self.store.close()
