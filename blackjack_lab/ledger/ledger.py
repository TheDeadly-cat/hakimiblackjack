# -*- coding: utf-8 -*-
"""可重放事件账本（开发大纲 3.2/4-B、V0.1 验收）。

保证：
1. event_id 幂等：同一事件重复提交只入账一次；
2. seq 单调递增，事件只追加，撤销/纠错不改写历史；
3. 同一物理牌（track_id）在同一牌靴只扣一次，重复帧/双展示不重复扣；
4. 重放 = 从空状态按序执行全部有效事件，结果与实时累计一致；
5. 撤销只允许逆序撤销最后一个有效事件；历史修改走 CORRECTION 追加纠错；
6. 新轮不重置牌靴；换靴（SHOE_CREATED）建立全新状态，不混入上一靴。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..core.cards import UNKNOWN, RANKS, TEN_BUCKET
from ..core.rules import RuleProfile, FINITE_NO_REPLACEMENT
from ..core.shoe import ShoeState
from ..core.table import (
    ACTION_DOUBLE, ACTION_SPLIT, DEALER, PHASE_DEALING, PHASE_IN_PROGRESS,
    TableState,
)
from .events import (
    BURN_CARDS, CANDIDATE, CARD_DEALT, CARD_REVEALED, CONFIRMED,
    CORRECTION, FACE_HIDDEN, FACE_UNKNOWN, OBSERVATION_GAP, PEEK_NEGATIVE,
    PLAYER_ACTION, ROUND_ENDED, ROUND_STARTED, SESSION_STARTED, SHOE_CREATED,
    SHOE_ENDED, UNDO, Event, new_event_id,
)


class LedgerError(Exception):
    pass


CORRECTABLE_FIELDS = {
    CARD_DEALT: {"rank", "suit", "face_state"},
    CARD_REVEALED: {"rank", "suit"},
    BURN_CARDS: {"count", "note"},
    OBSERVATION_GAP: {"reason", "resolved"},
    PEEK_NEGATIVE: {"invalidated"},
}


@dataclass
class ShoeSegment:
    """一只牌靴的完整信息：规则快照、事件区间、重放后的状态。"""
    shoe_id: str
    rules: RuleProfile
    events: List[Event] = field(default_factory=list)
    shoe: Optional[ShoeState] = None
    table: Optional[TableState] = None
    settlements: List[dict] = field(default_factory=list)
    # 庄家信息不完整、只结束未结算的轮次号
    unsettled_rounds: List[int] = field(default_factory=list)
    closed: bool = False
    round_id: Optional[str] = None
    unresolved: Dict[str, dict] = field(default_factory=dict)
    unallocated_unknown: int = 0


@dataclass
class ReplayResult:
    segments: List[ShoeSegment]
    current: Optional[ShoeSegment]

    @property
    def all_effective_events(self) -> List[Event]:
        out = []
        for seg in self.segments:
            out.extend(seg.events)
        return sorted(out, key=lambda e: e.seq)


class EventLedger:
    def __init__(self, session_id: str, rule_version: str = "v0.1"):
        self.session_id = session_id
        self.rule_version = rule_version
        self.events: List[Event] = []
        self._ids = set()
        self._seq = 0
        self._current_shoe_id: Optional[str] = None
        self._current_round_id: Optional[str] = None

    # ---------- 写入 ----------
    def append(self, event: Event) -> Event:
        """追加事件；相同 event_id 幂等返回原事件。"""
        if event.event_id in self._ids:
            for old in self.events:
                if old.event_id == event.event_id:
                    if (old.etype, old.payload, old.source, old.confirm_status, old.evidence) != (
                        event.etype, event.payload, event.source, event.confirm_status, event.evidence
                    ):
                        raise LedgerError("事件ID已存在但内容不同，拒绝静默丢弃冲突记录")
                    return old
            raise LedgerError("event_id 状态异常")
        candidate = copy.deepcopy(self)
        event = copy.deepcopy(event)
        event.seq = self._seq + 1
        event.session_id = self.session_id
        current = self.replay().current
        event.rule_version = str(current.rules.version) if current else self.rule_version
        if event.etype == SHOE_CREATED:
            event.shoe_id = event.payload.get("shoe_id")
            event.round_id = None
            event.rule_version = str(RuleProfile.from_json(event.payload["rules_snapshot"]).version)
        elif event.etype == ROUND_STARTED:
            event.shoe_id = self._current_shoe_id
            event.round_id = event.payload.get("round_id")
        else:
            event.shoe_id = event.shoe_id or self._current_shoe_id
            event.round_id = event.round_id or self._current_round_id
        candidate.events.append(event)
        candidate._ids.add(event.event_id)
        candidate._seq = event.seq
        candidate._sync_context(candidate.replay())  # 完整预演成功后才发布内存状态
        self.__dict__.update(candidate.__dict__)
        return event

    def _sync_context(self, replay: ReplayResult) -> None:
        cur = replay.current
        self._current_shoe_id = cur.shoe_id if cur and not cur.closed else None
        self._current_round_id = cur.round_id if cur and not cur.closed else None

    def start_session(self, note: str = "") -> Event:
        return self.append(Event(SESSION_STARTED, {"note": note}))

    def create_shoe(self, rules: RuleProfile, shoe_id: Optional[str] = None) -> Event:
        """新牌靴：牌副数与规则快照在此锁定。"""
        shoe_id = shoe_id or new_event_id()
        return self.append(Event(SHOE_CREATED, {
            "shoe_id": shoe_id,
            "n_decks": rules.n_decks,
            "rules_snapshot": rules.to_json(),
        }))

    def start_round(self, participants: Optional[List[str]] = None) -> Event:
        self._require_shoe()
        seg = self.replay().current
        round_no = (seg.table.round_no + 1) if seg and seg.table else 1
        round_id = f"{self._current_shoe_id}-R{round_no}"
        return self.append(Event(ROUND_STARTED, {
            "round_id": round_id, "round_no": round_no,
            "participants": participants,
        }))

    def _track_used_in_current_shoe(self, track_id: Optional[str]) -> bool:
        if not track_id:
            return False
        # 只检查最后一只鞋（最近一次 SHOE_CREATED 之后）的事件
        start = 0
        for i, ev in enumerate(self.events):
            if ev.etype == SHOE_CREATED:
                start = i
        voided = self._voided_ids()
        for ev in self.events[start:]:
            if ev.event_id in voided or ev.etype != CARD_DEALT:
                continue
            if ev.payload.get("track_id") == track_id:
                return True
        return False

    def deal(self, seat: str, rank: Optional[str] = None, *,
             hidden: bool = False, unknown: bool = False,
             hand_id: Optional[str] = None, suit: Optional[str] = None,
             track_id: Optional[str] = None,
             confirm_status: str = CONFIRMED, source: str = "手动录入",
             evidence: Optional[str] = None,
             event_id: Optional[str] = None) -> Event:
        self._require_shoe()
        if event_id and event_id in self._ids:
            old = self._find(event_id)
            face = FACE_HIDDEN if hidden else (FACE_UNKNOWN if unknown else "shown")
            proposed = {"seat": seat, "rank": rank, "suit": suit, "track_id": track_id,
                        "hand_id": hand_id or old.payload.get("hand_id"), "face_state": face}
            if (old.etype != CARD_DEALT or old.payload != proposed
                    or (old.source, old.confirm_status, old.evidence) != (source, confirm_status, evidence)):
                raise LedgerError("事件ID已存在但内容不同，拒绝静默丢弃冲突记录")
            return old
        if hidden and unknown:
            raise LedgerError("规则暗牌与观察未知不能同时设置")
        if (hidden or unknown) and rank not in (None, UNKNOWN):
            raise LedgerError("未知牌事件不得携带未来或猜测牌面")
        if self._track_used_in_current_shoe(track_id):
            raise LedgerError(
                f"物理牌 track_id={track_id} 在本牌靴已入账，"
                "多帧/双展示不得重复扣减")
        seg = self.replay().current
        seat_state = seg.table.seat(seat)
        hand_id = hand_id or (seat_state.hands[-1].hand_id if seat_state.hands else
                              f"R{seg.table.round_no}-{seat}-H{seg.table._hand_seq + 1}")
        ev = Event(CARD_DEALT, {
            "seat": seat, "rank": rank, "suit": suit, "track_id": track_id,
            "hand_id": hand_id,
            "face_state": FACE_HIDDEN if hidden else (
                FACE_UNKNOWN if unknown else "shown"),
        }, source=source, confirm_status=confirm_status, evidence=evidence,
           event_id=event_id or new_event_id())
        return self.append(ev)

    def reveal(self, target_event_id: str, rank: str,
               suit: Optional[str] = None) -> Event:
        target = self._find(target_event_id)
        if target.etype != CARD_DEALT:
            raise LedgerError("只能揭示发牌事件")
        return self.append(Event(CARD_REVEALED, {
            "target_event_id": target_event_id,
            "seat": target.payload["seat"],
            "hand_id": target.payload.get("hand_id"),
            "track_id": target.payload.get("track_id"),
            "rank": rank, "suit": suit,
        }))

    def player_action(self, seat: str, hand_id: str, action: str,
                      extra: Optional[dict] = None) -> Event:
        payload = {"seat": seat, "hand_id": hand_id, "action": action}
        if extra:
            if set(extra) - {"new_hand_id"}:
                raise LedgerError("动作附加字段不能覆盖目标身份或动作")
            payload.update(extra)
        if action == ACTION_SPLIT and "new_hand_id" not in payload:
            # 预演一次以取得分牌产生的新手牌 id（事件落账前完成合法性校验）
            self._require_shoe()
            preview = self.replay().current.table
            if preview.phase == PHASE_DEALING:
                preview.enter_play_phase()
            info = preview.apply_action(seat, hand_id, action)
            payload["new_hand_id"] = info.get("new_hand_id")
        return self.append(Event(PLAYER_ACTION, payload))

    def peek_negative(self) -> Event:
        return self.append(Event(PEEK_NEGATIVE, {}))

    def burn(self, count: int, note: str = "") -> Event:
        if count <= 0:
            raise LedgerError("烧牌数量必须为正")
        return self.append(Event(BURN_CARDS, {"count": count, "note": note}))

    def gap(self, reason: str) -> Event:
        return self.append(Event(OBSERVATION_GAP, {"reason": reason}))

    def end_round(self, *, settle: Optional[bool] = None, reason: str = "") -> Event:
        return self.append(Event(ROUND_ENDED, {"settle": settle, "reason": reason}))

    def end_shoe(self) -> Event:
        return self.append(Event(SHOE_ENDED, {}))

    # ---------- 撤销 / 纠错（追加式）----------
    def undo_last(self, reason: str = "") -> Event:
        """撤销最后一个有效（未被撤销、非控制类）事件。"""
        voided = self._voided_ids()
        for ev in reversed(self.events):
            if ev.etype in (UNDO, SESSION_STARTED):
                continue
            if ev.event_id in voided:
                continue
            return self.append(Event(UNDO, {
                "target_event_id": ev.event_id, "reason": reason,
                "target_etype": ev.etype,
            }))
        raise LedgerError("没有可撤销的事件")

    def correct(self, target_event_id: str, payload_fix: Dict[str, Any],
                reason: str = "") -> Event:
        """对历史事件追加纠错；重放时以修正负载执行，原事件保留。"""
        target = self._find(target_event_id)
        allowed = CORRECTABLE_FIELDS
        if target.etype not in allowed or not payload_fix or set(payload_fix) - allowed[target.etype]:
            raise LedgerError("仅允许纠正牌面/花色/未知状态、烧牌、缺口或检查结果；规则修改请新建牌靴")
        if self.is_voided(target_event_id):
            raise LedgerError("已撤销事件不可纠错")
        return self.append(Event(CORRECTION, {
            "target_event_id": target_event_id, "payload_fix": payload_fix,
            "reason": reason, "target_etype": target.etype,
        }))

    # ---------- 重放 ----------
    def replay(self, through_seq: Optional[int] = None) -> ReplayResult:
        """从空状态重放全部事件，返回每只牌靴的状态段。"""
        if through_seq is not None:
            prefix = copy.deepcopy(self)
            prefix.events = [e for e in self.events if e.seq <= through_seq]
            return prefix.replay()
        voided = self._voided_ids()
        corrections: Dict[str, dict] = {}
        seen = {}
        past_voided = set()
        for index, ev in enumerate(self.events):
            Event.from_dict(ev.to_dict())
            if ev.etype == SESSION_STARTED and index != 0:
                raise LedgerError("会话开始事件只能位于首条")
            if ev.etype in (UNDO, CORRECTION):
                target = ev.payload.get("target_event_id")
                if target not in seen or seen[target].etype in (UNDO, SESSION_STARTED):
                    raise LedgerError("控制事件必须引用此前可撤销/纠错的事件")
                if target in past_voided:
                    raise LedgerError("控制事件不可引用已撤销记录")
                if ev.etype == UNDO:
                    candidates = [e for e in seen.values() if e.etype not in (UNDO, SESSION_STARTED) and e.event_id not in past_voided]
                    if not candidates or candidates[-1].event_id != target:
                        raise LedgerError("只能逆序撤销最后有效事件，不能跳过后续记录")
                    past_voided.add(target)
            if ev.etype == CORRECTION and ev.event_id not in voided:
                original = seen[ev.payload["target_event_id"]]
                fix = ev.payload.get("payload_fix")
                if original.etype not in CORRECTABLE_FIELDS or not isinstance(fix, dict) or not fix or set(fix) - CORRECTABLE_FIELDS[original.etype]:
                    raise LedgerError("纠错包含不允许改写的身份、规则或操作字段")
                for name in ("resolved", "invalidated"):
                    if name in fix and type(fix[name]) is not bool:
                        raise LedgerError("纠错状态必须为布尔值")
                if "count" in fix and (type(fix["count"]) is not int or fix["count"] < 0):
                    raise LedgerError("烧牌纠错数量必须为非负整数")
                corrections.setdefault(ev.payload["target_event_id"], {}).update(ev.payload["payload_fix"])
            seen[ev.event_id] = ev
        segments: List[ShoeSegment] = []
        cur: Optional[ShoeSegment] = None
        dealt_tracks: set = set()
        # 发牌事件 -> 重放解析出的 hand_id（payload 是副本，单独维护映射）
        resolved_hand: Dict[str, str] = {}

        for ev in self.events:
            if ev.etype in (UNDO, CORRECTION, SESSION_STARTED):
                continue
            if ev.event_id in voided:
                continue
            payload = dict(ev.payload)
            if ev.event_id in corrections:
                payload.update(corrections[ev.event_id])

            if ev.etype == SHOE_CREATED:
                if ev.shoe_id != payload["shoe_id"] or ev.round_id is not None:
                    raise LedgerError("建靴事件身份与负载不一致")
                if cur and cur.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
                    raise LedgerError("当前轮尚未结束，不能换靴")
                if any(s.shoe_id == payload["shoe_id"] for s in segments):
                    raise LedgerError("新牌靴必须使用唯一身份")
                rules = RuleProfile.from_json(payload["rules_snapshot"])
                if rules.shoe_model != FINITE_NO_REPLACEMENT:
                    raise LedgerError("V0.1 仅支持有限不放回牌靴记录；其他牌靴模型未支持")
                if payload["n_decks"] != rules.n_decks:
                    raise LedgerError("牌副数与规则快照不一致")
                if cur:
                    cur.closed = True
                cur = ShoeSegment(shoe_id=payload["shoe_id"], rules=rules,
                                  shoe=ShoeState(rules.n_decks),
                                  table=TableState(rules))
                segments.append(cur)
                dealt_tracks = set()
                cur.events.append(ev)
                if rules.start_from_new_shoe is False or rules.burn_cards_known is False:
                    cur.shoe.mark_gap("中途开始记录或烧牌数量未知")
                if rules.initial_burn_count:
                    cur.shoe.burn_known_count(rules.initial_burn_count)
                continue

            if cur is None:
                raise LedgerError(f"事件 {ev.etype} 出现在任何牌靴创建之前")
            if cur.closed:
                raise LedgerError("牌靴已结束，请新建牌靴")
            if ev.shoe_id != cur.shoe_id:
                raise LedgerError("事件牌靴身份与重放上下文冲突")
            if ev.etype in (CARD_DEALT, CARD_REVEALED, PLAYER_ACTION, PEEK_NEGATIVE, ROUND_ENDED) and ev.round_id != cur.round_id:
                raise LedgerError("事件轮次身份与当前上下文冲突")
            cur.events.append(ev)
            shoe, table = cur.shoe, cur.table

            if ev.etype == ROUND_STARTED:
                if ev.round_id != payload["round_id"]:
                    raise LedgerError("开轮事件身份与负载不一致")
                table.start_round(payload.get("participants"))
                cur.round_id = payload["round_id"]
                if payload.get("round_no") != table.round_no:
                    raise LedgerError("轮次序号与重放上下文不一致")

            elif ev.etype == CARD_DEALT:
                if ev.round_id not in (None, cur.round_id):
                    raise LedgerError("发牌事件轮次身份不一致")
                face = payload["face_state"]
                rank = payload.get("rank")
                if face not in (FACE_HIDDEN, FACE_UNKNOWN, "shown"):
                    raise LedgerError("无效可见状态")
                if face != "shown" and rank not in (None, UNKNOWN):
                    raise LedgerError("未知牌事件不得保存未获知牌面")
                if face == "shown" and rank not in (*RANKS, TEN_BUCKET):
                    raise LedgerError("可见事件需要有效牌面")
                track = payload.get("track_id")
                seat = payload["seat"]
                if track:
                    if track in dealt_tracks:
                        raise LedgerError(
                            f"物理牌 {track} 在本牌靴已扣过，拒绝重复扣减")
                    dealt_tracks.add(track)
                # 牌靴层
                if face == FACE_HIDDEN:
                    if ev.confirm_status != CONFIRMED:
                        raise LedgerError("规则性底牌的存在必须已确认；不确定观察请记录未知牌")
                    if seat != DEALER or cur.rules.american_hole_card is False:
                        raise LedgerError("规则性暗牌只适用于有底牌庄家；漏牌请记录未知牌面")
                    shoe.place_unrevealed()
                elif face == FACE_UNKNOWN:
                    shoe.place_unrevealed()
                    shoe.add_pending_candidate(1)
                else:
                    shoe.remove_known(rank)
                    if ev.confirm_status != CONFIRMED and "face_state" not in corrections.get(ev.event_id, {}):
                        raise LedgerError("未确认候选不得按已知牌面扣牌，请先记录未知牌")
                # 牌桌层
                hand = table.add_card(
                    seat, rank or UNKNOWN, suit=payload.get("suit"),
                    track_id=track,
                    hidden=face in (FACE_HIDDEN, FACE_UNKNOWN),
                    hand_id=payload.get("hand_id"),
                    event_id=ev.event_id,
                )
                payload["_resolved_hand_id"] = hand.hand_id
                resolved_hand[ev.event_id] = hand.hand_id
                if face in (FACE_HIDDEN, FACE_UNKNOWN):
                    cur.unresolved[ev.event_id] = {"seat": seat, "hand_id": hand.hand_id,
                        "track_id": track, "face_state": face, "round_id": cur.round_id}
                table.validate_peek()

            elif ev.etype == CARD_REVEALED:
                target_id = payload["target_event_id"]
                target = cur.unresolved.get(target_id)
                if target is None or target["round_id"] != cur.round_id:
                    raise LedgerError("目标牌未处于本轮待揭示状态；历史牌请追加纠错")
                if payload["seat"] != target["seat"] or any(payload.get(k) is not None and payload[k] != target[k] for k in ("hand_id", "track_id")):
                    raise LedgerError("揭示事件身份与目标发牌不一致")
                if payload["rank"] not in (*RANKS, TEN_BUCKET):
                    raise LedgerError("揭示需要有效已知牌面")
                shoe.reveal_unrevealed(payload["rank"])
                if target["face_state"] == FACE_UNKNOWN:
                    shoe.add_pending_candidate(-1)
                # 发牌时未指定 hand_id 的，从重放解析结果里补定位
                hand_id = payload.get("hand_id")
                if hand_id is None:
                    hand_id = resolved_hand.get(payload["target_event_id"])
                table.reveal_card(
                    target["seat"], target["hand_id"],
                    target.get("track_id"), payload["rank"],
                    suit=payload.get("suit"),
                    event_id=target_id,
                )
                del cur.unresolved[target_id]

            elif ev.etype == PLAYER_ACTION:
                if table.phase == PHASE_DEALING:
                    table.enter_play_phase()
                info = table.apply_action(
                    payload["seat"], payload["hand_id"], payload["action"],
                    new_hand_id=payload.get("new_hand_id"))
                if payload["action"] == ACTION_SPLIT:
                    payload["_split_new_hand_id"] = info.get("new_hand_id")

            elif ev.etype == PEEK_NEGATIVE:
                if not payload.get("invalidated"):
                    table.mark_peek_negative()

            elif ev.etype == BURN_CARDS:
                if payload["count"] != 0 or ev.event_id not in corrections:
                    shoe.burn_known_count(payload["count"])

            elif ev.etype == OBSERVATION_GAP:
                if not payload.get("resolved"):
                    shoe.mark_gap(payload.get("reason", ""))

            elif ev.etype == ROUND_ENDED:
                table.ensure_playing()
                if table.phase == PHASE_DEALING:
                    table.enter_play_phase()
                # 庄家信息完整才做确定性结算；否则只结束轮次并标注未结算
                # （记录不得因信息缺口而崩溃，精确结算待信息补齐）
                results = None
                if payload.get("settle") is not False:
                    try:
                        results = table.settle()
                    except Exception as exc:
                        from ..core.table import TableError
                        if not isinstance(exc, TableError) or payload.get("settle") is True:
                            raise
                if results is not None:
                    cur.settlements.extend(results)
                else:
                    table.phase = "已结束未结算"
                    cur.unsettled_rounds.append(table.round_no)

            elif ev.etype == SHOE_ENDED:
                if table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
                    raise LedgerError("请先结束当前轮再结束牌靴")
                cur.closed = True

            ok, note = shoe.conservation_check()
            if not ok:
                raise LedgerError(note)

        return ReplayResult(segments=segments, current=segments[-1] if segments else None)

    # ---------- 辅助 ----------
    def _require_shoe(self) -> None:
        if self._current_shoe_id is None:
            raise LedgerError("尚未创建牌靴，请先选择 6/7/8 副并新建牌靴")

    def _find(self, event_id: str) -> Event:
        for ev in self.events:
            if ev.event_id == event_id:
                return ev
        raise LedgerError(f"找不到事件 {event_id}")

    def _voided_ids(self) -> set:
        voided = set()
        for ev in reversed(self.events):
            if ev.etype == UNDO and ev.event_id not in voided:
                voided.add(ev.payload["target_event_id"])
        return voided

    def is_voided(self, event_id: str) -> bool:
        return event_id in self._voided_ids()

    # ---------- 序列化 ----------
    def to_list(self) -> List[dict]:
        return copy.deepcopy([e.to_dict() for e in self.events])

    @classmethod
    def from_list(cls, session_id: str, data: List[dict],
                  rule_version: str = "v0.1") -> "EventLedger":
        ledger = cls(session_id, rule_version=rule_version)
        events = [Event.from_dict(copy.deepcopy(d)) for d in data]
        previous = 0
        for ev in events:
            if ev.session_id != session_id or type(ev.seq) is not int or ev.seq <= previous:
                raise LedgerError("导入事件必须同会话、按严格递增序号排列")
            if ev.event_id in ledger._ids:
                raise LedgerError("导入事件身份重复")
            previous = ev.seq
            ledger._ids.add(ev.event_id)
        ledger.events = events
        ledger._seq = previous
        ledger._sync_context(ledger.replay())
        return ledger
