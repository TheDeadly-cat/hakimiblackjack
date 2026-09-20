"""Fixed initial-deal queue for keypad recording.

Plan identity `players_up_players_hole_v1`:
all participating players' first cards → dealer upcard → the same players'
second cards → dealer hole existence. Split-hand order is a different plan
and is never inferred from this template.

The cursor is stored in a versioned companion file, not in the SQLite event
table. If that file is missing after recovery, the next player is not guessed.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Iterable, Optional

from ..core.cards import TEN_BUCKET, is_ten_value
from ..core.table import DEALER, player_seat_name

PLAN_SCHEMA = "hakimi-round-entry-plan-v2"
PLAN_ID = "players_up_players_hole_v1"

FACE_SHOWN = "shown"
FACE_HIDDEN = "hidden"

MODE_INITIAL = "initial_auto"
MODE_MANUAL = "manual_override"
MODE_CONTINUATION = "player_continuation"
MODE_PEEK_WAIT = "peek_wait"
MODE_DEALER = "dealer_phase"
MODE_UNALIGNED = "needs_alignment"

ROLE_PLAYER_FIRST = "player_first"
ROLE_DEALER_UP = "dealer_up"
ROLE_PLAYER_SECOND = "player_second"
ROLE_DEALER_HOLE = "dealer_hole"

FORWARD_ORDER = tuple(player_seat_name(i) for i in range(1, 8))
REVERSE_ORDER = tuple(reversed(FORWARD_ORDER))


def seat_order(direction: str = "forward") -> tuple[str, ...]:
    if direction == "reverse":
        return REVERSE_ORDER
    return FORWARD_ORDER


def participating_in_order(selected: Iterable[str], direction: str = "forward") -> tuple[str, ...]:
    wanted = [name for name in selected if name != DEALER]
    seen = set()
    ordered = []
    for name in seat_order(direction):
        if name in wanted and name not in seen:
            ordered.append(name)
            seen.add(name)
    extra = [name for name in wanted if name not in seen]
    if extra:
        raise ValueError("参与座位必须使用固定编号 1–7，不能临时改号")
    if not ordered:
        raise ValueError("本轮没有参与玩家")
    return tuple(ordered)


def build_initial_slots(participants: Iterable[str]) -> tuple["EntrySlot", ...]:
    players = tuple(participants)
    if not players:
        raise ValueError("本轮没有参与玩家")
    slots = []
    index = 1
    for seat in players:
        slots.append(EntrySlot(f"s{index}", seat, 1, FACE_SHOWN, ROLE_PLAYER_FIRST))
        index += 1
    slots.append(EntrySlot(f"s{index}", DEALER, 1, FACE_SHOWN, ROLE_DEALER_UP))
    index += 1
    for seat in players:
        slots.append(EntrySlot(f"s{index}", seat, 2, FACE_SHOWN, ROLE_PLAYER_SECOND))
        index += 1
    slots.append(EntrySlot(f"s{index}", DEALER, 2, FACE_HIDDEN, ROLE_DEALER_HOLE))
    return tuple(slots)


def rank_label(rank: Optional[str]) -> str:
    if rank in (None, ""):
        return "（无）"
    if rank == TEN_BUCKET or is_ten_value(rank):
        return "10点" if rank == TEN_BUCKET else rank
    return rank


def dealer_up_requires_peek(rules, up_rank: Optional[str]) -> bool:
    if rules is None or not up_rank:
        return False
    return (getattr(rules, "american_hole_card", None) is True
            and getattr(rules, "check_bj_when", None) == "before_player_actions_A_T"
            and (up_rank == "A" or is_ten_value(up_rank)))


def next_open_hand(table, participants=None) -> Optional[tuple[str, Optional[str], int]]:
    """First unfinished player hand in this round's participation order."""
    if table is None:
        return None
    for seat_name in participants or table.participants:
        seat = table.players.get(seat_name)
        if seat is None or not seat.hands:
            return (seat_name, None, 1)
        for index, hand in enumerate(seat.hands, start=1):
            if not (table.split_hand_closed(hand) if hand.from_split else hand.is_closed):
                return (seat_name, hand.hand_id, index)
    return None


def all_player_hands_closed(table) -> bool:
    if table is None:
        return False
    return all(not table.seat(name).active_hands() for name in table.participants)


@dataclass(frozen=True)
class EntrySlot:
    slot_id: str
    seat: str
    ordinal: int
    expected_face: str
    role: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "EntrySlot":
        return cls(**data)


@dataclass
class LastSaved:
    seat: str
    ordinal: int
    rank: Optional[str]
    event_id: str
    slot_id: Optional[str] = None
    hand_ordinal: int = 1
    kind: str = "shown"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> Optional["LastSaved"]:
        if not data:
            return None
        return cls(**data)


@dataclass
class RoundEntryPlan:
    session_id: str
    shoe_id: str
    round_id: str
    participating_seats: tuple[str, ...]
    my_seat: str
    deal_direction: str = "forward"
    slots: tuple[EntrySlot, ...] = field(default_factory=tuple)
    cursor_slot_id: Optional[str] = None
    filled_slots: dict = field(default_factory=dict)
    unresolved_slots: list = field(default_factory=list)
    mode: str = MODE_UNALIGNED
    paused: bool = False
    input_paused: bool = False
    input_pause_reason: str = ""
    ledger_seq: int = 0
    ledger_digest: str = ""
    observed_card_ids: list = field(default_factory=list)
    last_saved: Optional[LastSaved] = None
    continuation_seat: Optional[str] = None
    continuation_hand_id: Optional[str] = None
    continuation_hand_ordinal: int = 1
    dealer_up_rank: Optional[str] = None
    schema: str = PLAN_SCHEMA
    plan_id: str = PLAN_ID
    version: int = 2
    pause_reason: str = ""

    @classmethod
    def freeze(cls, *, session_id: str, shoe_id: str, round_id: str,
               selected_seats: Iterable[str], my_seat: str,
               deal_direction: str = "forward") -> "RoundEntryPlan":
        participants = participating_in_order(selected_seats, deal_direction)
        if my_seat not in participants:
            raise ValueError("本人座位必须是本轮参与座位之一")
        slots = build_initial_slots(participants)
        return cls(
            session_id=session_id, shoe_id=shoe_id, round_id=round_id,
            participating_seats=participants, my_seat=my_seat,
            deal_direction=deal_direction, slots=slots,
            cursor_slot_id=slots[0].slot_id, mode=MODE_INITIAL, paused=False,
        )

    @classmethod
    def unaligned(cls, *, session_id: str, shoe_id: str = "", round_id: str = "",
                  my_seat: str = "玩家1") -> "RoundEntryPlan":
        return cls(session_id=session_id, shoe_id=shoe_id, round_id=round_id,
                   participating_seats=(), my_seat=my_seat, mode=MODE_UNALIGNED,
                   paused=True, input_paused=True,
                   pause_reason="发牌计划未核对，不能猜测下一张该给谁")

    def slot(self, slot_id: Optional[str] = None) -> Optional[EntrySlot]:
        target = slot_id or self.cursor_slot_id
        return next((item for item in self.slots if item.slot_id == target), None)

    def slot_index(self, slot_id: Optional[str] = None) -> int:
        current = self.slot(slot_id)
        if current is None:
            return -1
        return self.slots.index(current)

    def unfilled_ids(self) -> tuple[str, ...]:
        return tuple(item.slot_id for item in self.slots if item.slot_id not in self.filled_slots)

    def visible_slot_count(self) -> int:
        return sum(1 for item in self.slots if item.expected_face == FACE_SHOWN)

    def hole_slot(self) -> Optional[EntrySlot]:
        return next((item for item in self.slots if item.role == ROLE_DEALER_HOLE), None)

    def initial_complete(self) -> bool:
        return bool(self.slots) and len(self.filled_slots) == len(self.slots)

    def current_recording_seat(self) -> Optional[str]:
        if self.mode == MODE_CONTINUATION:
            return self.continuation_seat
        current = self.slot()
        return current.seat if current else None

    def mark_filled(self, slot_id: str, event_id: str, rank: Optional[str] = None,
                    kind: str = "shown") -> EntrySlot:
        slot = self.slot(slot_id)
        if slot is None:
            raise ValueError("未知发牌槽位，拒绝入账后推进")
        if slot.slot_id in self.filled_slots:
            if self.filled_slots[slot.slot_id] == event_id:
                return slot
            raise ValueError("该槽位已保存，不能重复提交")
        self.filled_slots[slot.slot_id] = event_id
        if event_id not in self.observed_card_ids:
            self.observed_card_ids.append(event_id)
        if slot.slot_id in self.unresolved_slots:
            self.unresolved_slots.remove(slot.slot_id)
        if slot.role == ROLE_DEALER_UP:
            self.dealer_up_rank = rank
        self.last_saved = LastSaved(slot.seat, slot.ordinal, rank, event_id, slot.slot_id,
                                    kind=kind)
        return slot

    def advance_after_initial_success(self, requires_peek=None) -> None:
        if self.mode != MODE_INITIAL or self.paused:
            return
        remaining = self.unfilled_ids()
        if remaining:
            self.cursor_slot_id = remaining[0]
            return
        self.cursor_slot_id = None
        peek = bool(requires_peek(self.dealer_up_rank)) if requires_peek else False
        if peek:
            self.mode = MODE_PEEK_WAIT
            self.pause_reason = "庄家明牌为A或十点，等待实际检查结果；未写入确认非BJ"
        else:
            self.enter_continuation()

    def enter_continuation(self, seat: Optional[str] = None, hand_id: Optional[str] = None,
                           hand_ordinal: int = 1) -> None:
        self.mode = MODE_CONTINUATION
        self.paused = False
        self.pause_reason = ""
        self.continuation_seat = seat or (self.participating_seats[0] if self.participating_seats else None)
        self.continuation_hand_id = hand_id
        self.continuation_hand_ordinal = hand_ordinal
        self.cursor_slot_id = None

    def enter_dealer_phase(self) -> None:
        self.mode = MODE_DEALER
        self.continuation_seat = DEALER
        self.continuation_hand_id = None
        self.continuation_hand_ordinal = 1
        self.paused = False

    def pause_manual(self, reason: str, unresolved_slot_id: Optional[str] = None) -> None:
        self.mode = MODE_MANUAL if self.mode == MODE_INITIAL else self.mode
        self.paused = True
        self.pause_reason = reason
        if unresolved_slot_id and unresolved_slot_id not in self.filled_slots:
            if unresolved_slot_id not in self.unresolved_slots:
                self.unresolved_slots.append(unresolved_slot_id)

    def toggle_input_pause(self) -> None:
        if self.mode == MODE_UNALIGNED:
            raise ValueError("请先核对已保存记录，再选择人工录入；Space不能解除恢复核对")
        self.input_paused = not self.input_paused
        self.input_pause_reason = "录入已暂停；Space恢复原阶段、原手" if self.input_paused else ""

    def resume_at(self, slot_id: str) -> None:
        slot = self.slot(slot_id)
        if slot is None:
            raise ValueError("只能对齐到本轮已冻结的发牌槽")
        if slot.slot_id in self.filled_slots:
            raise ValueError("该槽位已保存，请选择仍未录的槽位")
        self.cursor_slot_id = slot.slot_id
        self.mode = MODE_INITIAL
        self.paused = False
        self.pause_reason = ""

    def navigate(self, step: int) -> Optional[EntrySlot]:
        """Move the recording cursor only. Skipped slots stay unfilled."""
        if not self.slots:
            return None
        if self.mode == MODE_INITIAL and not self.paused:
            current = self.slot()
            if current and current.slot_id not in self.filled_slots:
                self.pause_manual("导航跳过未录槽位，自动轮转已暂停", current.slot_id)
        index = self.slot_index()
        if index < 0:
            index = 0 if step > 0 else len(self.slots) - 1
        else:
            index = (index + step) % len(self.slots)
        self.cursor_slot_id = self.slots[index].slot_id
        if self.mode in (MODE_INITIAL, MODE_MANUAL, MODE_UNALIGNED):
            self.mode = MODE_MANUAL
            self.paused = True
        return self.slot()

    def jump_seat(self, seat: str) -> None:
        if self.mode == MODE_INITIAL and not self.paused:
            current = self.slot()
            self.pause_manual("直接选择座位，自动轮转已暂停",
                              current.slot_id if current and current.slot_id not in self.filled_slots else None)
        match = next((item for item in self.slots
                      if item.seat == seat and item.slot_id not in self.filled_slots), None)
        if match is not None:
            self.cursor_slot_id = match.slot_id
        # Inspecting another seat does not move the actual acting hand.


    def undo_event(self, event_id: str) -> Optional[EntrySlot]:
        slot_id = next((sid for sid, eid in self.filled_slots.items() if eid == event_id), None)
        if slot_id is None:
            if self.mode == MODE_CONTINUATION:
                return None
            return None
        del self.filled_slots[slot_id]
        slot = self.slot(slot_id)
        self.cursor_slot_id = slot_id
        self.mode = MODE_INITIAL
        self.paused = False
        self.pause_reason = ""
        self.last_saved = None
        if slot and slot.role == ROLE_DEALER_UP:
            self.dealer_up_rank = None
        return slot

    def accept_shown_on_cursor(self) -> EntrySlot:
        slot = self.slot()
        if slot is None:
            raise ValueError("没有可录的初始发牌槽")
        if slot.expected_face != FACE_SHOWN:
            raise ValueError("当前槽位是庄家暗牌，不能用点值键代替暗牌确认")
        if self.paused or self.mode != MODE_INITIAL:
            raise ValueError("自动轮转已暂停，请先对齐未录槽位")
        return slot

    def accept_hole_on_cursor(self) -> EntrySlot:
        slot = self.slot()
        if slot is None or slot.role != ROLE_DEALER_HOLE:
            raise ValueError("只有明确的庄家暗牌槽才接受暗牌已发确认")
        if self.paused and self.mode == MODE_MANUAL:
            raise ValueError("自动轮转已暂停，请先对齐到庄家暗牌槽")
        return slot

    def record_continuation_card(self, seat: str, event_id: str, rank: str,
                                 hand_ordinal: int = 1) -> None:
        if event_id not in self.observed_card_ids:
            self.observed_card_ids.append(event_id)
        self.last_saved = LastSaved(seat, hand_ordinal, rank, event_id,
                                    hand_ordinal=hand_ordinal, kind="shown")
        if self.mode == MODE_CONTINUATION:
            self.continuation_seat = seat
            self.continuation_hand_ordinal = hand_ordinal

    def note_stand(self, seat: str, event_id: str, next_target: Optional[tuple[str, Optional[str], int]]) -> None:
        self.last_saved = LastSaved(seat, self.continuation_hand_ordinal, None, event_id,
                                    kind="stand")
        if next_target is None:
            self.enter_dealer_phase()
            return
        self.enter_continuation(*next_target)

    def reconcile(self, live_event_ids: Iterable[str]) -> None:
        live = set(live_event_ids)
        # A voided card must not remain advertised as the latest saved card,
        # including when loading a plan written before this check existed.
        if (self.last_saved and self.last_saved.kind in ("shown", "hidden")
                and self.last_saved.event_id not in live):
            self.last_saved = None
        if live - set(self.observed_card_ids):
            self.mode = MODE_UNALIGNED
            self.paused = self.input_paused = True
            self.pause_reason = "账本存在计划未处理的牌；请核对已保存记录，不要重复录入"
        for slot_id, event_id in list(self.filled_slots.items()):
            if event_id not in live:
                del self.filled_slots[slot_id]
                if slot_id not in self.unresolved_slots:
                    self.unresolved_slots.append(slot_id)
        if self.mode == MODE_INITIAL and not self.paused:
            remaining = self.unfilled_ids()
            self.cursor_slot_id = remaining[0] if remaining else None

    def recording_complete_for_prompt(self) -> bool:
        return self.initial_complete() and not self.unresolved_slots

    def phase_title(self) -> str:
        if self.input_paused:
            return "录入已暂停"
        if self.mode == MODE_UNALIGNED:
            return "发牌计划未对齐"
        if self.mode == MODE_PEEK_WAIT:
            return "等待检查结果"
        if self.mode == MODE_CONTINUATION:
            return "玩家续牌"
        if self.mode == MODE_DEALER:
            return "庄家阶段"
        if self.paused or self.mode == MODE_MANUAL:
            return "手动纠偏 · 自动轮转已暂停"
        slot = self.slot()
        if slot is None:
            return "初始发牌"
        if slot.role == ROLE_PLAYER_FIRST:
            return "初始发牌 · 第一遍"
        if slot.role == ROLE_DEALER_UP:
            return "初始发牌 · 庄家明牌"
        if slot.role == ROLE_PLAYER_SECOND:
            return "初始发牌 · 第二遍"
        return "初始发牌 · 庄家暗牌"

    def progress_text(self) -> str:
        if not self.slots:
            return "进度 —"
        return f"进度{len(self.filled_slots)}/{len(self.slots)}"

    def next_card_text(self) -> str:
        if self.mode == MODE_PEEK_WAIT:
            return "下一张给：等待庄家 Blackjack 检查结果（不自动确认非BJ）"
        if self.mode == MODE_CONTINUATION:
            hand = f"／第{self.continuation_hand_ordinal}手" if self.continuation_hand_ordinal else ""
            return f"下一张给：{self.continuation_seat or '当前手'}{hand}（停留当前手）"
        if self.mode == MODE_DEALER:
            return "下一张给：庄家"
        slot = self.slot()
        if slot is None:
            return "下一张给：—"
        if slot.role == ROLE_DEALER_HOLE:
            return "下一张给：庄家暗牌位置（小键盘 . 确认已发）"
        return f"下一张给：{slot.seat}，第{slot.ordinal}张"

    def last_saved_text(self) -> str:
        item = self.last_saved
        if item is None:
            return "刚刚记入：—"
        if item.kind == "hidden":
            return f"刚刚记入：{item.seat}，暗牌存在，已保存"
        if item.kind == "stand":
            return f"刚刚记入：{item.seat} 实际停牌，已保存"
        rank = rank_label(item.rank)
        return f"刚刚记入：{item.seat}，第{item.ordinal}张，{rank}，已保存"

    def prompt(self, recording_target: str, analysis_target: str) -> str:
        mine = f"我／{analysis_target}" if analysis_target else "我／（未选）"
        lines = [
            f"{self.phase_title()} · {self.progress_text()}",
            "",
            self.last_saved_text(),
            ("下一张给：" + recording_target + "（人工录入，请核对）"
             if self.mode in (MODE_MANUAL, MODE_UNALIGNED) else self.next_card_text()),
            "",
            f"录入目标：{recording_target}",
            f"分析对象：{mine}",
        ]
        if self.unresolved_slots:
            names = [self.slot(sid).seat + f"第{self.slot(sid).ordinal}张" for sid in self.unresolved_slots if self.slot(sid)]
            lines.append("未录槽位：" + "、".join(names))
        if self.pause_reason:
            lines.append(self.pause_reason)
        if self.input_pause_reason:
            lines.append(self.input_pause_reason)
        if self.mode == MODE_UNALIGNED:
            lines.append("请确认参与座位与本人座位后重新对齐，不能根据已有张数猜测归属。")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["participating_seats"] = list(self.participating_seats)
        data["slots"] = [slot.to_dict() for slot in self.slots]
        data["last_saved"] = self.last_saved.to_dict() if self.last_saved else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "RoundEntryPlan":
        if not isinstance(data, dict):
            raise ValueError("发牌计划必须是JSON对象")
        if (data.get("schema") != PLAN_SCHEMA or type(data.get("version")) is not int
                or data["version"] != 2 or data.get("plan_id") != PLAN_ID):
            raise ValueError("旧版或未知发牌计划；需核对账本后人工继续")
        if set(data) != {item.name for item in fields(cls)}:
            raise ValueError("发牌计划字段缺失或含未知字段")
        payload = dict(data)
        payload["participating_seats"] = tuple(payload.get("participating_seats") or ())
        payload["slots"] = tuple(EntrySlot.from_dict(item) for item in payload.get("slots") or ())
        payload["last_saved"] = LastSaved.from_dict(payload.get("last_saved"))
        payload["filled_slots"] = dict(payload.get("filled_slots") or {})
        payload["unresolved_slots"] = list(payload.get("unresolved_slots") or [])
        plan = cls(**payload)
        if plan.mode not in (MODE_INITIAL, MODE_MANUAL, MODE_CONTINUATION,
                             MODE_PEEK_WAIT, MODE_DEALER, MODE_UNALIGNED):
            raise ValueError("未知录入阶段")
        if (type(plan.paused) is not bool or type(plan.input_paused) is not bool
                or type(plan.ledger_seq) is not int or plan.ledger_seq < 0
                or not isinstance(plan.ledger_digest, str)
                or len(plan.ledger_digest) != 64
                or any(c not in "0123456789abcdef" for c in plan.ledger_digest)):
            raise ValueError("发牌计划缺少有效事件前缀")
        if plan.participating_seats:
            if participating_in_order(plan.participating_seats, plan.deal_direction) != plan.participating_seats:
                raise ValueError("发牌参与顺序无效")
            if plan.slots != build_initial_slots(plan.participating_seats):
                raise ValueError("发牌槽与冻结参与顺序不符")
            if plan.my_seat not in plan.participating_seats:
                raise ValueError("本人座位不在参与列表")
        elif plan.slots or plan.mode not in (MODE_UNALIGNED, MODE_MANUAL):
            raise ValueError("发牌计划缺少参与座位")
        valid_ids = {s.slot_id for s in plan.slots}
        if (set(plan.filled_slots) - valid_ids or set(plan.unresolved_slots) - valid_ids
                or plan.cursor_slot_id is not None and plan.cursor_slot_id not in valid_ids
                or len(set(plan.filled_slots.values())) != len(plan.filled_slots)
                or not isinstance(plan.observed_card_ids, list)
                or any(not isinstance(e, str) for e in plan.observed_card_ids)
                or not set(plan.filled_slots.values()) <= set(plan.observed_card_ids)):
            raise ValueError("发牌事件与槽位身份无效")
        if plan.deal_direction not in ("forward", "reverse"):
            raise ValueError("未知发牌方向")
        return plan
