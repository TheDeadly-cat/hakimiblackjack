"""Append-only retrospective insert of a missed deal.

Original events are never rewritten. A repair voids the suffix, inserts the
missed card at confirmation time, then replays the suffix. Historical prefixes
that stop before the new events still replay the unrepaired timeline.
"""
from __future__ import annotations

import copy
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..core.table import TableError
from .events import (
    BURN_CARDS, CARD_DEALT, CARD_REVEALED, CONFIRMED, CORRECTION, FACE_HIDDEN,
    FACE_UNKNOWN, OBSERVATION_GAP, PEEK_NEGATIVE, PLAYER_ACTION, ROUND_ENDED,
    ROUND_STARTED, SESSION_STARTED, SHOE_CREATED, SHOE_ENDED, SOURCE_REPAIR,
    UNDO, Event,
)
from .ledger import EventLedger, LedgerError


PLAN_SCHEMA = "hakimi-ledger-repair-plan-v1"
PLAN_NOTE = (
    "修正版本另存为后续事件；历史前缀不包含后来插入的牌。"
    "禁止用修复后的当前结果宣称当时已经抓到窗口。"
)
FORBIDDEN_SUFFIX = {SESSION_STARTED, SHOE_CREATED, UNDO, CORRECTION}


class RepairError(LedgerError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass
class MissedDealPlan:
    batch_id: str
    anchor_event_id: str
    anchor_seq: int
    base_event_count: int
    base_head_event_id: str
    suffix_event_ids: tuple
    missed: Dict[str, Any]
    confirmed_at: float
    reason: str = "回溯插入漏牌"
    schema: str = PLAN_SCHEMA
    note: str = PLAN_NOTE

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "batch_id": self.batch_id,
            "anchor_event_id": self.anchor_event_id,
            "anchor_seq": self.anchor_seq,
            "base_event_count": self.base_event_count,
            "base_head_event_id": self.base_head_event_id,
            "suffix_event_ids": list(self.suffix_event_ids),
            "missed": copy.deepcopy(self.missed),
            "confirmed_at": self.confirmed_at,
            "reason": self.reason,
            "note": self.note,
        }


def inserted_visible_at(ledger: EventLedger, through_seq: int, batch_id: str) -> bool:
    """True only if the repaired insert itself is already in the chosen prefix."""
    return any(
        ev.seq <= through_seq
        and ev.payload.get("repair_batch_id") == batch_id
        and ev.payload.get("repair_role") == "inserted_missed"
        for ev in ledger.events
    )


def plan_missed_deal(
    ledger: EventLedger,
    after_event_id: str,
    *,
    seat: str,
    rank: Optional[str] = None,
    hidden: bool = False,
    unknown: bool = False,
    hand_id: Optional[str] = None,
    suit: Optional[str] = None,
    track_id: Optional[str] = None,
    first_readable_at: Optional[float] = None,
    occurred_at: Optional[float] = None,
    confirm_status: str = CONFIRMED,
    reason: str = "回溯插入漏牌",
    confirmed_at: Optional[float] = None,
) -> MissedDealPlan:
    """Preview a missed-deal insert. Does not mutate `ledger`."""
    if not ledger.events:
        raise RepairError("ANCHOR_MISSING", "账本为空，不能插入漏牌")
    try:
        anchor = ledger._find(after_event_id)
    except LedgerError as exc:
        raise RepairError("ANCHOR_MISSING", str(exc)) from exc
    if ledger.is_voided(anchor.event_id) or anchor.etype in (UNDO, CORRECTION, SESSION_STARTED):
        raise RepairError("ANCHOR_VOIDED", "锚点必须是当前有效的非控制事件")
    suffix = _suffix_events(ledger, anchor)
    missed = {
        "seat": seat,
        "rank": rank,
        "hidden": bool(hidden),
        "unknown": bool(unknown),
        "hand_id": hand_id,
        "suit": suit,
        "track_id": track_id,
        "first_readable_at": first_readable_at,
        "occurred_at": occurred_at,
        "confirm_status": confirm_status,
    }
    if missed["hidden"] and missed["unknown"]:
        raise RepairError("ILLEGAL_MISSED_DEAL", "一张牌不能同时是规则性暗牌与观察未知牌")
    if not missed["hidden"] and not missed["unknown"] and not rank:
        raise RepairError("ILLEGAL_MISSED_DEAL", "可见漏牌必须带牌面")
    plan = MissedDealPlan(
        batch_id=uuid.uuid4().hex,
        anchor_event_id=anchor.event_id,
        anchor_seq=anchor.seq,
        base_event_count=len(ledger.events),
        base_head_event_id=ledger.events[-1].event_id,
        suffix_event_ids=tuple(ev.event_id for ev in suffix),
        missed=missed,
        confirmed_at=time.time() if confirmed_at is None else confirmed_at,
        reason=reason or "回溯插入漏牌",
    )
    try:
        apply_repair(copy.deepcopy(ledger), plan)
    except RepairError:
        raise
    except Exception as exc:
        raise RepairError("SUFFIX_REPLAY_FAILED", f"预演失败，未改账本：{exc}") from exc
    return plan


def apply_repair(ledger: EventLedger, plan: MissedDealPlan) -> EventLedger:
    """Append the repair batch. Failed replay leaves `ledger` unchanged.

    Disk atomicity remains the store's job. Callers that already copied the
    ledger still get the repaired copy back; a live ledger is not left with
    half-applied UNDOs if suffix replay fails.
    """
    _assert_plan_matches(ledger, plan)
    working = copy.deepcopy(ledger)
    try:
        _apply_repair_mutating(working, plan)
    except RepairError:
        raise
    except (LedgerError, TableError, ValueError) as exc:
        raise RepairError("SUFFIX_REPLAY_FAILED", f"预演失败，未改账本：{exc}") from exc
    ledger.__dict__.update(working.__dict__)
    return ledger


def _assert_plan_matches(ledger: EventLedger, plan: MissedDealPlan) -> None:
    if plan.schema != PLAN_SCHEMA:
        raise RepairError("STALE_PLAN", "不支持的修复计划格式")
    if (len(ledger.events) != plan.base_event_count
            or ledger.events[-1].event_id != plan.base_head_event_id):
        raise RepairError("STALE_PLAN", "账本已变化，请重新预演修复计划")
    try:
        anchor_now = ledger._find(plan.anchor_event_id)
    except LedgerError as exc:
        raise RepairError("STALE_PLAN", str(exc)) from exc
    if anchor_now.seq != plan.anchor_seq:
        raise RepairError("STALE_PLAN", "锚点事件已不在原序号")


def _apply_repair_mutating(ledger: EventLedger, plan: MissedDealPlan) -> EventLedger:
    suffix_ids = list(plan.suffix_event_ids)
    originals = {ev.event_id: ev for ev in ledger.events}
    for event_id in reversed(suffix_ids):
        undone = ledger.undo_last(
            plan.reason,
            repair={"repair_batch_id": plan.batch_id, "repair_role": "void_suffix"},
        )
        if undone.payload["target_event_id"] != event_id:
            raise RepairError("SUFFIX_REPLAY_FAILED", "撤销目标不是预期的后续事件")
    missed = plan.missed
    hand_id = missed.get("hand_id") or _hand_id_from_suffix(originals, suffix_ids, missed.get("seat"))
    repair_meta = {
        "repair_batch_id": plan.batch_id,
        "repair_role": "inserted_missed",
        "repair_anchor_id": plan.anchor_event_id,
        "first_readable_at": missed.get("first_readable_at"),
        "occurred_at": missed.get("occurred_at"),
    }
    ledger.deal(
        missed["seat"], missed.get("rank"),
        hidden=bool(missed.get("hidden")),
        unknown=bool(missed.get("unknown")),
        hand_id=hand_id,
        suit=missed.get("suit"),
        track_id=missed.get("track_id"),
        confirm_status=missed.get("confirm_status") or CONFIRMED,
        source=SOURCE_REPAIR,
        observed_at=plan.confirmed_at,
        repair=repair_meta,
    )
    id_map: Dict[str, str] = {}
    for event_id in suffix_ids:
        replayed = _replay_one(ledger, originals[event_id], id_map, plan)
        id_map[event_id] = replayed.event_id
    return ledger


def _suffix_events(ledger: EventLedger, anchor: Event) -> List[Event]:
    voided = ledger._voided_ids()
    suffix = []
    for ev in ledger.events:
        if ev.seq <= anchor.seq:
            continue
        if ev.etype in FORBIDDEN_SUFFIX:
            if ev.etype == SHOE_CREATED:
                raise RepairError("REPAIR_CROSSES_SHOE", "本版不能跨新牌靴插入漏牌；请先结束并撤销新靴")
            raise RepairError("REPAIR_CONTROL_SUFFIX", "后续含撤销或纠错时，本版不自动插入；请先逆序处理控制事件")
        if ev.event_id in voided:
            continue
        suffix.append(ev)
    return suffix


def _hand_id_from_suffix(originals, suffix_ids, seat):
    if not seat:
        return None
    for event_id in suffix_ids:
        original = originals[event_id]
        if original.etype == CARD_DEALT and original.payload.get("seat") == seat:
            return original.payload.get("hand_id")
    return None


def _replay_meta(plan: MissedDealPlan, original_event_id: str) -> dict:
    return {
        "repair_batch_id": plan.batch_id,
        "repair_role": "replay_suffix",
        "repair_original_event_id": original_event_id,
    }


def _stamp_replay(event: Event, original: Event) -> Event:
    event.source = original.source
    event.observed_at = original.observed_at
    event.evidence = original.evidence
    return event


def _replay_one(ledger: EventLedger, original: Event, id_map: Dict[str, str],
                plan: MissedDealPlan) -> Event:
    payload = original.payload
    repair = _replay_meta(plan, original.event_id)
    etype = original.etype
    if etype == CARD_DEALT:
        face = payload["face_state"]
        replayed = ledger.deal(
            payload["seat"], payload.get("rank"),
            hidden=face == FACE_HIDDEN,
            unknown=face == FACE_UNKNOWN,
            hand_id=payload.get("hand_id"),
            suit=payload.get("suit"),
            track_id=payload.get("track_id"),
            confirm_status=original.confirm_status,
            source=original.source,
            evidence=original.evidence,
            observed_at=original.observed_at,
            repair=repair,
        )
        return replayed
    if etype == CARD_REVEALED:
        target = id_map.get(payload["target_event_id"], payload["target_event_id"])
        replayed = ledger.reveal(
            target, payload["rank"], suit=payload.get("suit"),
            source=original.source, evidence=original.evidence,
            observed_at=original.observed_at, repair=repair,
        )
        return replayed
    if etype == PLAYER_ACTION:
        extra = {"new_hand_id": payload["new_hand_id"]} if payload.get("new_hand_id") else None
        replayed = ledger.player_action(
            payload["seat"], payload["hand_id"], payload["action"], extra, repair=repair)
        return _stamp_replay(replayed, original)
    if etype == PEEK_NEGATIVE:
        return _stamp_replay(ledger.peek_negative(repair=repair), original)
    if etype == BURN_CARDS:
        return _stamp_replay(ledger.burn(payload["count"], payload.get("note") or "", repair=repair), original)
    if etype == OBSERVATION_GAP:
        return _stamp_replay(ledger.gap(payload.get("reason") or "", repair=repair), original)
    if etype == ROUND_ENDED:
        return _stamp_replay(ledger.end_round(
            settle=payload.get("settle"), reason=payload.get("reason") or "",
            observation_status=payload.get("observation_status", "unknown"),
            repair=repair), original)
    if etype == ROUND_STARTED:
        return _stamp_replay(ledger.start_round(payload.get("participants"), repair=repair), original)
    if etype == SHOE_ENDED:
        return _stamp_replay(ledger.end_shoe(repair=repair), original)
    raise RepairError("SUFFIX_REPLAY_FAILED", f"本版不能重放后续事件类型 {etype}")
