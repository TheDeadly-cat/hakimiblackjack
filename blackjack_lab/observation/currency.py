"""Live-table currency for analysis. Never writes the ledger or consumes cards.

Unconfirmed drafts, source freeze, and queue overflow do not change confirmed
history. They can only prevent calling an as-of-ledger result "current table".
Overflow stays incomplete until an explicit human observation check.
"""
from __future__ import annotations

from dataclasses import dataclass
import time

MODE_MANUAL = "manual"
MODE_REPLAY = "replay"
MODE_LIVE = "live"

STATUS_IDLE = "idle"
STATUS_LIVE = "live"
STATUS_FROZEN = "frozen"
STATUS_STOPPED = "stopped"
STATUS_SWITCHED = "switched"
STATUS_MISSING = "missing"

REASON_ALIGNED = "aligned"
REASON_UNCONFIRMED = "unconfirmed_candidates"
REASON_OVERFLOW = "overflow_unacknowledged"
REASON_FROZEN = "source_frozen"
REASON_STOPPED = "source_stopped"
REASON_SWITCHED = "source_switched"
REASON_MISSING = "source_missing"

REASON_ZH = {
    REASON_ALIGNED: "观察与已确认记录一致",
    REASON_UNCONFIRMED: "存在尚未确认的候选或草稿",
    REASON_OVERFLOW: "队列曾溢出，清空后仍须完成对账",
    REASON_FROZEN: "来源画面冻结或超时",
    REASON_STOPPED: "来源已停止或结束",
    REASON_SWITCHED: "来源已切换，须重新绑定",
    REASON_MISSING: "尚未收到来源画面",
}


def classify_source_problem(problem):
    if not problem:
        return STATUS_LIVE
    text = str(problem)
    if "静止" in text or "未更新" in text:
        return STATUS_FROZEN
    if "已停止" in text:
        return STATUS_STOPPED
    if "改变" in text or "切换" in text:
        return STATUS_SWITCHED
    if "尚未收到" in text or "等待来源" in text:
        return STATUS_MISSING
    if "未连接" in text:
        return STATUS_IDLE
    return STATUS_FROZEN


@dataclass(frozen=True)
class ObservationRevision:
    generation: str
    mode: str
    source_status: str
    pending_unconfirmed: int
    overflow_unacknowledged: bool
    last_frame_ns: int | None
    reconciled_ns: int | None
    seq: int

    def live_table_applicable(self):
        if self.pending_unconfirmed > 0:
            return False, REASON_UNCONFIRMED
        if self.overflow_unacknowledged:
            return False, REASON_OVERFLOW
        if self.mode in (MODE_MANUAL, MODE_REPLAY):
            return True, REASON_ALIGNED
        if self.source_status == STATUS_FROZEN:
            return False, REASON_FROZEN
        if self.source_status == STATUS_STOPPED:
            return False, REASON_STOPPED
        if self.source_status == STATUS_SWITCHED:
            return False, REASON_SWITCHED
        if self.source_status == STATUS_MISSING:
            return False, REASON_MISSING
        return True, REASON_ALIGNED


class ObservationState:
    def __init__(self):
        self._generation = "manual"
        self._mode = MODE_MANUAL
        self._source_status = STATUS_IDLE
        self._pending = 0
        self._overflow_unacked = False
        self._overflow_count = 0
        self._last_frame_ns = None
        self._reconciled_ns = None
        self._seq = 0
        self._listeners = []

    def add_listener(self, callback):
        self._listeners.append(callback)

    def remove_listener(self, callback):
        self._listeners = [item for item in self._listeners if item is not callback]

    @property
    def mode(self):
        return self._mode

    @property
    def generation(self):
        return self._generation

    @property
    def overflow_count(self):
        return self._overflow_count

    def _bump(self):
        self._seq += 1
        for callback in list(self._listeners):
            callback()

    def revision(self):
        return ObservationRevision(
            generation=self._generation,
            mode=self._mode,
            source_status=self._source_status,
            pending_unconfirmed=self._pending,
            overflow_unacknowledged=self._overflow_unacked,
            last_frame_ns=self._last_frame_ns,
            reconciled_ns=self._reconciled_ns,
            seq=self._seq,
        )

    def live_table_applicable(self):
        return self.revision().live_table_applicable()

    def set_unconfirmed(self, count):
        count = max(0, int(count))
        if count != self._pending:
            self._pending = count
            self._bump()

    def mark_overflow(self, extra=1):
        self._overflow_count += max(0, int(extra))
        if not self._overflow_unacked:
            self._overflow_unacked = True
            self._bump()

    def connect_source(self, generation):
        generation = str(generation)
        if self._generation != generation or self._mode != MODE_LIVE:
            self._generation = generation
            self._mode = MODE_LIVE
            self._source_status = STATUS_MISSING
            self._bump()

    def enter_manual(self):
        if self._mode != MODE_MANUAL or self._source_status != STATUS_IDLE:
            self._mode = MODE_MANUAL
            self._source_status = STATUS_IDLE
            self._bump()

    def enter_replay(self):
        if self._mode != MODE_REPLAY:
            self._mode = MODE_REPLAY
            self._source_status = STATUS_STOPPED
            self._bump()

    def disconnect_live(self):
        if self._mode == MODE_LIVE and self._source_status != STATUS_STOPPED:
            self._source_status = STATUS_STOPPED
            self._bump()

    def note_source(self, status, last_frame_ns=None):
        if last_frame_ns is not None:
            self._last_frame_ns = last_frame_ns
        if status != self._source_status:
            self._source_status = status
            self._bump()

    def note_frame(self, last_frame_ns):
        self._last_frame_ns = last_frame_ns

    def reconcile(self, *, acknowledge_overflow=False):
        self._reconciled_ns = time.perf_counter_ns()
        if acknowledge_overflow:
            self._overflow_unacked = False
        self._bump()
        return self.revision()
