"""Import a shoe-event draft into an open ledger. Cut cards are never dealt.

Preview on a ledger copy, then one save_ledger transaction. Retrying the same
draft must not deal cards again. Confirmed zero burn is known zero, not unknown.
"""
from __future__ import annotations

import copy
import json

from ..analysis.contracts import digest
from ..analysis.shoe_event_draft import (
    ALLOWED_SEATS, OBSERVATION_RISK_KINDS, PASSIVE_IMPORT_KINDS,
    blocking_observation_risks, coverage_attestation_valid, draft_source_span,
    observation_risk_blocks,
)
from ..core.table import DEALER, PHASE_DEALING, PHASE_IN_PROGRESS, player_seat_name
from ..storage.database import DraftReceiptCorrupt
from .events import CARD_DEALT, OBSERVATION_GAP, SOURCE_LICENSED_VIDEO

NOTE = (
    "占用候选没有座位真值。未确认牌面记观察缺口，不发明点数。"
    "切牌卡是流程标志，不得 CARD_DEALT，也不自动当成漏牌缺口。"
    "导入不等于验收通过，也不能把研究模板写成该真实桌。"
    "庄家只能在明确选定后入账，不能作为缺省座位。"
)
PLAN_SCHEMA = "hakimi-draft-import-plan-v1"
PLAYER_SEATS = frozenset(player_seat_name(i) for i in range(1, 8))


class DraftImportError(Exception):
    def __init__(self, code, message, *, committed=False, result=None):
        super().__init__(message)
        self.code = code
        self.committed = bool(committed)
        self.result = result


def _source_identity(events):
    identity = []
    for event in events:
        identity.append({
            "event_id": event.get("event_id"),
            "kind": event.get("kind"),
            "status": event.get("status"),
            "rank": event.get("rank"),
            "seat": event.get("seat"),
            "count": event.get("count"),
            "round_id": event.get("round_id"),
        })
    return identity


def _spans_overlap(left, right):
    if not left or not right:
        return True
    start_a, end_a = left.get("start_frame"), left.get("end_frame")
    start_b, end_b = right.get("start_frame"), right.get("end_frame")
    if None in (start_a, end_a, start_b, end_b):
        return True
    return not (end_a < start_b or end_b < start_a)


def _stable_source_identity(draft):
    """Physical observation identity. Regenerated event UUIDs are not a new clip."""
    return digest({
        "video_sha256": draft.get("video_sha256"),
        "video_bytes": draft.get("video_bytes"),
        "source_span": draft_source_span(draft),
        "events": [
            {
                "kind": event.get("kind"),
                "status": event.get("status"),
                "rank": event.get("rank"),
                "seat": event.get("seat"),
                "count": event.get("count"),
                "round_id": event.get("round_id"),
                "frame_index": event.get("frame_index"),
                "still_path": event.get("still_path"),
                "still_sha256": event.get("still_sha256"),
                "affects_composition": event.get("affects_composition"),
            }
            for event in draft.get("events") or []
        ],
    })


def _fingerprint(draft, *, seat, source):
    return digest({
        "events": _source_identity(draft.get("events") or []),
        "filename": draft.get("filename"),
        "video_sha256": draft.get("video_sha256"),
        "stable_source_identity": _stable_source_identity(draft),
        "seat": seat,
        "source": source,
    })


def _prefix_identity(ledger):
    events = ledger.events
    if not events:
        return {"seq": 0, "event_id": None}
    last = events[-1]
    return {"seq": last.seq, "event_id": last.event_id}


def _receipt_key(session_id, shoe_id, fingerprint):
    return f"draft_import:{session_id}:{shoe_id}:{fingerprint}"


class _BatchWriter:
    def __init__(self, ledger, source):
        self.ledger = ledger
        self.source = source
        self.written = []

    def _keep(self, event):
        if self.source:
            event.source = self.source
        self.written.append(event)
        return event

    def burn(self, count, note=""):
        return self._keep(self.ledger.burn(count, note))

    def mark_gap(self, reason):
        return self._keep(self.ledger.gap(reason))

    def start_round(self, participants=None):
        return self._keep(self.ledger.start_round(participants))

    def deal_shown(self, seat, rank, evidence=None, source=None):
        return self._keep(self.ledger.deal(
            seat, rank, evidence=evidence, source=source or self.source))

    def deal_hidden(self, seat, evidence=None, source=None):
        return self._keep(self.ledger.deal(
            seat, None, hidden=True, evidence=evidence, source=source or self.source))

    def end_round_unsettled(self, reason, observation_status="unknown"):
        return self._keep(self.ledger.end_round(
            settle=False, reason=reason, observation_status=observation_status))


def _confirmed_burn_total(burns):
    if not burns:
        return None
    total = 0
    for event in burns:
        if event.get("status") != "confirmed" or type(event.get("count")) is not int:
            return None
        if event["count"] < 0:
            return None
        total += event["count"]
    return total


def _round_cards_confirmed(items):
    counted = [event for event in items if event.get("kind") in ("deal", "hidden")]
    if not counted:
        return False
    for event in counted:
        if event.get("status") != "confirmed":
            return False
        if event.get("kind") == "deal" and event.get("rank") in (None, "", "unknown"):
            return False
    return True


def _round_complete(draft, round_id, items):
    """Confirmed discovered cards are not coverage of the observed range."""
    if not _round_cards_confirmed(items):
        return False
    if blocking_observation_risks(draft, round_id):
        return False
    return coverage_attestation_valid(draft, round_id)


def _play_draft(ledger, draft, *, seat, source):
    writer = _BatchWriter(ledger, source)
    events = list(draft.get("events") or [])
    burns = [event for event in events if event.get("kind") == "burn"]
    burn_total = _confirmed_burn_total(burns)
    if burn_total is None:
        writer.mark_gap("烧牌数量未知，不得默认为 0")
    elif burn_total > 0:
        writer.burn(burn_total, "开发片已确认烧牌")
    for event in events:
        if observation_risk_blocks(event) and event.get("round_id") in (None, "", "unassigned"):
            writer.mark_gap("未解决的观察疑点可能漏牌，不能当作已完整观察")
    rounds = []
    seen = set()
    for event in events:
        if event.get("kind") not in ("round_start", "deal", "hidden", "round_end"):
            continue
        key = event.get("round_id") or "unassigned"
        if key in seen:
            continue
        seen.add(key)
        rounds.append(key)
    for key in rounds:
        items = [event for event in events if event.get("round_id") == key]
        if not any(event.get("kind") in ("deal", "hidden") for event in items):
            continue
        participant = seat if seat != DEALER else player_seat_name(1)
        player_seats = []
        for event in items:
            dest = event.get("seat")
            if dest in PLAYER_SEATS and dest not in player_seats:
                player_seats.append(dest)
        if player_seats:
            participant_list = sorted(
                player_seats, key=lambda name: int(str(name).replace("玩家", "") or 0))
        else:
            participant_list = [participant] if participant in PLAYER_SEATS else [player_seat_name(1)]
        writer.start_round(participant_list)
        counted = [event for event in items if event.get("kind") in ("deal", "hidden")]
        booked = 0
        for event in counted:
            dest = event.get("seat") if event.get("seat") in ALLOWED_SEATS else seat
            evidence = event.get("still_path")
            evidence = evidence if isinstance(evidence, str) else None
            if event.get("kind") == "deal" and event.get("status") == "confirmed" and event.get("rank") not in (None, "", "unknown"):
                writer.deal_shown(
                    dest, event["rank"], evidence=evidence, source=source)
                booked += 1
            elif event.get("kind") == "hidden" and event.get("status") == "confirmed":
                writer.deal_hidden(
                    dest, evidence=evidence, source=source)
                booked += 1
        if booked != len(counted):
            writer.mark_gap(f"{key} 座位或牌面未确认；占用候选不能当逐牌真值")
        for event in blocking_observation_risks(draft, key):
            if event.get("round_id") in (None, "", "unassigned"):
                continue
            writer.mark_gap("未解决的丢帧、遮挡或未知事件可能漏牌，不能当作整轮已覆盖")
        for event in items:
            kind = event.get("kind")
            if kind in ("deal", "hidden") or kind in PASSIVE_IMPORT_KINDS:
                continue
            if kind in OBSERVATION_RISK_KINDS or kind in ("between_rounds", "cut_card"):
                continue
            writer.mark_gap("未归属或未知事件，不能当作已完整观察")
        status = (
            "complete"
            if booked == len(counted) and _round_complete(draft, key, items)
            else "incomplete"
        )
        writer.end_round_unsettled(
            f"{key} 开发片导入，未结算" if status != "complete" else f"{key} 已核对导入",
            observation_status=status)
    types = [event.etype for event in writer.written]
    inspect_ready = False
    inspect_reason = None
    try:
        from ..analysis.research_windows import inspect_ledger_opening
        inspect_ledger_opening(ledger)
        inspect_ready = True
    except Exception as error:
        inspect_reason = getattr(error, "code", None) or type(error).__name__
    return {
        "accepted": False,
        "source": source,
        "seat": seat,
        "events_written": len(writer.written),
        "card_dealt": types.count(CARD_DEALT),
        "gaps": types.count(OBSERVATION_GAP),
        "burn_total": burn_total,
        "cut_card_marker": any(event.get("kind") == "cut_card" for event in events),
        "between_rounds_skipped": any(event.get("kind") == "between_rounds" for event in events),
        "written_event_ids": [event.event_id for event in writer.written],
        "offline_mc_ready": bool(inspect_ready),
        "inspect_reason": inspect_reason,
        "note": NOTE,
    }


def preview_event_draft(ctrl, draft, *, seat, source=SOURCE_LICENSED_VIDEO):
    """Validate the draft on a copy. Does not write the live ledger."""
    _require_open_shoe(ctrl, seat)
    seat = seat.strip()
    events = list(draft.get("events") or [])
    fingerprint = _fingerprint(draft, seat=seat, source=source)
    state = ctrl.state()
    shoe_id = state.current.shoe_id
    prefix = _prefix_identity(ctrl.ledger)
    candidate = copy.deepcopy(ctrl.ledger)
    result = _play_draft(candidate, draft, seat=seat, source=source)
    return {
        "schema": PLAN_SCHEMA,
        "accepted": False,
        "session_id": ctrl.session_id,
        "shoe_id": shoe_id,
        "prefix": prefix,
        "draft_fingerprint": fingerprint,
        "source_event_ids": [event.get("event_id") for event in events if event.get("event_id")],
        "filename": draft.get("filename"),
        "video_sha256": draft.get("video_sha256"),
        "source_span": draft_source_span(draft),
        "stable_source_identity": _stable_source_identity(draft),
        "result": result,
    }


def _require_open_shoe(ctrl, seat):
    if not isinstance(seat, str) or seat.strip() not in ALLOWED_SEATS:
        raise ValueError("导入必须声明座位（庄家或玩家1–7）；占用页不能发明座次")
    state = ctrl.state()
    if state.current is None or state.current.closed:
        raise ValueError("需要先建立牌靴。研究模板可离线使用，不能冒充该真实桌。")
    if state.current.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
        raise ValueError("请先结束当前轮再导入草稿")


def _receipts_or_corrupt(ctrl, shoe_id):
    try:
        return ctrl.store.list_draft_import_receipts(ctrl.session_id, shoe_id)
    except DraftReceiptCorrupt as error:
        raise DraftImportError(
            "RECEIPT_CORRUPT",
            "导入回执无法解析，已保留原字节；不能当作从未导入，须先与账本核对",
            result={"corrupt_receipts": error.records},
        ) from error


def _existing_receipt(ctrl, *, shoe_id, fingerprint, source_ids, draft):
    receipts = _receipts_or_corrupt(ctrl, shoe_id)
    stable = _stable_source_identity(draft)
    video = draft.get("video_sha256")
    span = draft_source_span(draft)
    for receipt in receipts:
        previous_ids = set(receipt.get("source_event_ids") or [])
        if receipt.get("draft_fingerprint") == fingerprint:
            return receipt
        if previous_ids & set(source_ids):
            raise DraftImportError(
                "DRAFT_CHANGED",
                "草稿已修改；请走纠错，不要整份重新导入")
    for receipt in receipts:
        if video and receipt.get("video_sha256") == video:
            if receipt.get("stable_source_identity") == stable:
                return receipt
            if _spans_overlap(span, receipt.get("source_span")):
                raise DraftImportError(
                    "SOURCE_OVERLAP",
                    "同一原片片段无法证明与已导入观察不重叠；请关联已有批次或进入修订，不能再扣一次牌")
    return None


def apply_event_draft(ctrl, draft, *, seat, source=SOURCE_LICENSED_VIDEO):
    """Preview, then commit the whole batch once. Same draft retries are no-ops."""
    _require_open_shoe(ctrl, seat)
    seat = seat.strip()
    fingerprint = _fingerprint(draft, seat=seat, source=source)
    shoe_id = ctrl.state().current.shoe_id
    source_ids = [event.get("event_id") for event in (draft.get("events") or []) if event.get("event_id")]
    existing = _existing_receipt(
        ctrl, shoe_id=shoe_id, fingerprint=fingerprint, source_ids=source_ids, draft=draft)
    if existing is not None:
        result = dict(existing.get("result") or {})
        result["replayed"] = True
        result["accepted"] = False
        return result
    plan = preview_event_draft(ctrl, draft, seat=seat, source=source)
    prefix = _prefix_identity(ctrl.ledger)
    if prefix != plan["prefix"]:
        raise DraftImportError("PREFIX_MOVED", "预演后账本已变化，拒绝提交过期导入计划")
    candidate = copy.deepcopy(ctrl.ledger)
    result = _play_draft(candidate, draft, seat=seat, source=source)
    receipt = {
        "schema": PLAN_SCHEMA,
        "session_id": ctrl.session_id,
        "shoe_id": plan["shoe_id"],
        "draft_fingerprint": plan["draft_fingerprint"],
        "source_event_ids": plan["source_event_ids"],
        "stable_source_identity": plan["stable_source_identity"],
        "video_sha256": plan.get("video_sha256"),
        "source_span": plan.get("source_span"),
        "prefix": plan["prefix"],
        "result": result,
    }
    extra_meta = {
        _receipt_key(ctrl.session_id, plan["shoe_id"], plan["draft_fingerprint"]):
        json.dumps(receipt, ensure_ascii=False),
    }
    try:
        ctrl.store.save_ledger(candidate, extra_meta=extra_meta)
    except Exception as error:
        raise DraftImportError("IMPORT_WRITE_FAILED", f"导入未提交：{error}") from error
    ctrl.ledger = candidate
    ctrl.commit_revision += 1
    try:
        ctrl._publish_context_change()
    except Exception as error:
        raise DraftImportError(
            "COMMITTED_DISPLAY_FAILED",
            "已提交，显示失败。不要再次导入同一份草稿。",
            committed=True, result=result) from error
    result["replayed"] = False
    return result
