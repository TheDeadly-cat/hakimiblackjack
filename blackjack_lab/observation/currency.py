"""Live-table currency for analysis. Never writes the ledger or consumes cards.

Source freshness (window, generation, freeze, disconnect) is independent of
knowledge change (new suspects, unresolved regions, deferred hides, overflow).
A frame clock tick is not a knowledge change. Replay and manual as-of numbers
may be computable without being labeled as the current live table.
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
REASON_MANUAL = "manual_asof"
REASON_REPLAY = "replay_not_live"
REASON_DEFERRED = "deferred_unconfirmed"
REASON_REGIONS = "unresolved_regions"
REASON_REGION_EVENTS = "unresolved_region_events"
REASON_INPUT = "input_changed"
REGION_KIND_DEAL = "deal_event"
REGION_KIND_GEOMETRY = "geometry_debug"

KIND_LIVE_CURRENT = "live_current"
KIND_MANUAL_ASOF = "manual_asof"
KIND_REPLAY = "replay"
KIND_SYNTHETIC = "synthetic"
KIND_STALE = "stale_asof"


def _nonneg_int(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name}必须是非负整数；不能把 {value!r} 截成整数")
    return value

REASON_ZH = {
    REASON_ALIGNED: "观察与已确认记录一致",
    REASON_UNCONFIRMED: "存在尚未确认的候选或草稿",
    REASON_OVERFLOW: "队列曾溢出，清空后仍须完成对账",
    REASON_FROZEN: "来源画面冻结或超时",
    REASON_STOPPED: "来源已停止或结束",
    REASON_SWITCHED: "来源已切换，须重新绑定",
    REASON_MISSING: "尚未收到来源画面",
    REASON_MANUAL: "截至人工确认记录，不是当前真实牌桌",
    REASON_REPLAY: "录像回放可计算，但不适用于当前真实牌桌",
    REASON_DEFERRED: "草稿被撤回或暂时跳过，尚未完成对账",
    REASON_REGIONS: "仍有未定区域未审，队列空不等于对账完成",
    REASON_REGION_EVENTS: "发牌疑点仍待确认；画面上消失不等于已经解决",
    REASON_INPUT: "研究输入已改变，旧结果不再作为当前请求",
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
    unresolved_regions: int = 0
    deferred_unconfirmed: int = 0
    knowledge_seq: int = 0
    region_events_pending: bool = False

    def knowledge_identity(self):
        return (
            self.generation,
            self.mode,
            self.pending_unconfirmed,
            self.overflow_unacknowledged,
            self.unresolved_regions,
            self.deferred_unconfirmed,
            self.region_events_pending,
            self.knowledge_seq,
        )

    def source_freshness(self):
        return (self.generation, self.mode, self.source_status)

    def knowledge_clear(self):
        return (
            self.pending_unconfirmed == 0
            and not self.overflow_unacknowledged
            and self.unresolved_regions == 0
            and self.deferred_unconfirmed == 0
            and not self.region_events_pending
        )

    def knowledge_block_reason(self):
        if self.pending_unconfirmed > 0:
            return REASON_UNCONFIRMED
        if self.overflow_unacknowledged:
            return REASON_OVERFLOW
        if self.deferred_unconfirmed > 0:
            return REASON_DEFERRED
        if self.region_events_pending:
            return REASON_REGION_EVENTS
        if self.unresolved_regions > 0:
            return REASON_REGIONS
        return None

    def live_table_applicable(self):
        blocked = self.knowledge_block_reason()
        if blocked:
            return False, blocked
        if self.mode == MODE_MANUAL:
            return False, REASON_MANUAL
        if self.mode == MODE_REPLAY:
            return False, REASON_REPLAY
        if self.source_status == STATUS_FROZEN:
            return False, REASON_FROZEN
        if self.source_status == STATUS_STOPPED:
            return False, REASON_STOPPED
        if self.source_status == STATUS_SWITCHED:
            return False, REASON_SWITCHED
        if self.source_status == STATUS_MISSING:
            return False, REASON_MISSING
        if self.mode == MODE_LIVE and self.source_status == STATUS_LIVE:
            return True, REASON_ALIGNED
        return False, REASON_MISSING

    def applicability_kind(self):
        if self.mode == MODE_REPLAY:
            return KIND_REPLAY
        if self.mode == MODE_MANUAL:
            return KIND_MANUAL_ASOF
        ok, _reason = self.live_table_applicable()
        return KIND_LIVE_CURRENT if ok else KIND_STALE

    def display_policy(self):
        """What this revision may show. Hide/skip never clears an observation obligation."""
        ok, reason = self.live_table_applicable()
        kind = self.applicability_kind()
        return {
            "applicability_kind": kind,
            "reason": reason,
            "asof_compute_allowed": True,
            "asof_numbers_display_allowed": True,
            "live_table_label_allowed": bool(ok),
            "timely_live_claim_allowed": bool(ok) and kind == KIND_LIVE_CURRENT,
            "replay_is_not_live": self.mode == MODE_REPLAY,
            "manual_is_not_live": self.mode == MODE_MANUAL,
            "knowledge_clear": self.knowledge_clear(),
            "knowledge_identity": self.knowledge_identity(),
            "source_freshness": self.source_freshness(),
        }


class ObservationState:
    def __init__(self):
        self._generation = "manual"
        self._mode = MODE_MANUAL
        self._source_status = STATUS_IDLE
        self._pending = 0
        self._overflow_unacked = False
        self._overflow_count = 0
        self._unresolved_regions = 0
        self._geometry_regions = 0
        self._region_events_pending = False
        self._acknowledged_region_count = 0
        self._deferred = 0
        self._last_frame_ns = None
        self._reconciled_ns = None
        self._seq = 0
        self._knowledge_seq = 0
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

    def _notify(self):
        for callback in list(self._listeners):
            callback()

    def _bump(self, *, knowledge=False):
        self._seq += 1
        if knowledge:
            self._knowledge_seq += 1
        self._notify()

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
            unresolved_regions=self._unresolved_regions,
            deferred_unconfirmed=self._deferred,
            knowledge_seq=self._knowledge_seq,
            region_events_pending=self._region_events_pending,
        )

    def live_table_applicable(self):
        return self.revision().live_table_applicable()

    def set_unconfirmed(self, count):
        count = _nonneg_int(count, "未确认候选数")
        if count != self._pending:
            self._pending = count
            self._bump(knowledge=True)

    def set_unresolved_regions(self, count, *, kind=REGION_KIND_DEAL):
        count = _nonneg_int(count, "未定区域数")
        if kind not in (REGION_KIND_DEAL, REGION_KIND_GEOMETRY):
            raise ValueError("未定区域种类只接受 deal_event 或 geometry_debug")
        if kind == REGION_KIND_GEOMETRY:
            self._geometry_regions = count
            return
        knowledge = False
        if count > self._acknowledged_region_count:
            if not self._region_events_pending:
                knowledge = True
            self._region_events_pending = True
        if count != self._unresolved_regions:
            self._unresolved_regions = count
            knowledge = True
        if knowledge:
            self._bump(knowledge=True)

    def defer_unconfirmed(self, extra=1):
        extra = _nonneg_int(extra, "撤回草稿数")
        if extra:
            self._deferred += extra
            self._bump(knowledge=True)

    def mark_overflow(self, extra=1):
        extra = _nonneg_int(extra, "溢出增量")
        self._overflow_count += extra
        if not self._overflow_unacked:
            self._overflow_unacked = True
            self._bump(knowledge=True)

    def connect_source(self, generation):
        generation = str(generation)
        if self._generation != generation or self._mode != MODE_LIVE:
            self._generation = generation
            self._mode = MODE_LIVE
            self._source_status = STATUS_MISSING
            self._bump(knowledge=True)

    def enter_manual(self):
        if self._mode != MODE_MANUAL or self._source_status != STATUS_IDLE:
            self._mode = MODE_MANUAL
            self._source_status = STATUS_IDLE
            self._bump(knowledge=True)

    def enter_replay(self):
        if self._mode != MODE_REPLAY:
            self._mode = MODE_REPLAY
            self._source_status = STATUS_STOPPED
            self._bump(knowledge=True)

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

    def reconcile(self, *, acknowledge_overflow=False, clear_deferred=False):
        self._reconciled_ns = time.perf_counter_ns()
        if acknowledge_overflow:
            self._overflow_unacked = False
        if clear_deferred:
            self._deferred = 0
        self._region_events_pending = False
        self._acknowledged_region_count = self._unresolved_regions
        self._bump(knowledge=True)
        return self.revision()
