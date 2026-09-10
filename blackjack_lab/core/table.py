# -*- coding: utf-8 -*-
"""牌桌状态：庄家 + 最多 7 位玩家、手牌、分牌父子关系与合法动作。

记录层职责（V0.1）：
- 维护每轮发牌与动作的状态机，校验动作合法性，不支持的组合直接拒绝；
- 结算只做确定性记账（输赢/平局/Blackjack 赔付倍率），不计算 EV（V0.2）；
- 庄家未翻底牌属于规则性隐藏信息：记录其存在，未揭示前不参与结算。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .cards import UNKNOWN, TEN_BUCKET, Card, hand_total, is_natural_blackjack, is_ten_value
from .rules import RuleProfile

DEALER = "庄家"
PHASE_NO_ROUND = "未开轮"
PHASE_DEALING = "发牌中"
PHASE_IN_PROGRESS = "进行中"
PHASE_SETTLED = "已结算"

ACTION_HIT = "补牌"
ACTION_STAND = "停牌"
ACTION_DOUBLE = "加倍"
ACTION_SPLIT = "分牌"
ACTION_SURRENDER = "投降"


def player_seat_name(i: int) -> str:
    if not 1 <= i <= 7:
        raise ValueError("玩家座位号为 1~7")
    return f"玩家{i}"


@dataclass(frozen=True)
class ActionState:
    action: str
    allowed: bool
    status: str
    reason_code: str
    reason: str


@dataclass
class HandInstance:
    hand_id: str
    cards: List[Card] = field(default_factory=list)
    actions: List[str] = field(default_factory=list)
    parent_id: Optional[str] = None
    from_split: bool = False
    is_split_ace: bool = False
    doubled: bool = False
    surrendered: bool = False
    stood: bool = False
    settled_result: Optional[str] = None
    settled_net: Optional[float] = None
    bet_units: float = 1.0
    awaiting_hit: bool = False

    @property
    def ranks(self) -> List[str]:
        return [c.rank for c in self.cards if not c.is_unknown]

    @property
    def hidden_cards(self) -> List[Card]:
        return [c for c in self.cards if c.rank == UNKNOWN]

    def total(self) -> Tuple[Optional[int], bool]:
        return hand_total([c.rank for c in self.cards])

    @property
    def is_bust(self) -> bool:
        t, _ = self.total()
        return t is not None and t > 21

    @property
    def is_closed(self) -> bool:
        """该手是否已结束（停牌/爆牌/投降/加倍完成/分A限补完成）。"""
        if self.surrendered or self.stood or self.is_bust:
            return True
        return False

    def display(self) -> str:
        parts = []
        for c in self.cards:
            parts.append("?" if c.rank == UNKNOWN else c.rank)
        t, soft = self.total()
        ttxt = "点数未知" if t is None else f"{'软' if soft else ''}{t}"
        tags = []
        if self.doubled:
            tags.append("已加倍")
        if self.surrendered:
            tags.append("已投降")
        if self.settled_result:
            tags.append(self.settled_result)
        tag = f"[{'/'.join(tags)}]" if tags else ""
        return f"{' '.join(parts)}（{ttxt}）{tag}"


@dataclass
class SeatState:
    name: str
    hands: List[HandInstance] = field(default_factory=list)

    def get_hand(self, hand_id: str) -> HandInstance:
        for h in self.hands:
            if h.hand_id == hand_id:
                return h
        raise TableError(f"座位 {self.name} 不存在手牌 {hand_id}")

    def active_hands(self) -> List[HandInstance]:
        return [h for h in self.hands if not h.is_closed]


class TableError(Exception):
    """牌桌动作非法或状态不一致。"""


class TableState:
    def __init__(self, rules: RuleProfile):
        self.rules = rules
        self.round_no = 0
        self.phase = PHASE_NO_ROUND
        self.dealer = SeatState(DEALER)
        self.players: Dict[str, SeatState] = {
            player_seat_name(i): SeatState(player_seat_name(i))
            for i in range(1, rules.n_seats + 1)
        }
        self.participants: List[str] = []
        self.dealer_hole_checked_negative = False  # 已检查且庄家不是 BJ
        self._hand_seq = 0
        self.split_order_violations = []

    # ---------- 轮次 ----------
    def start_round(self, participants: Optional[List[str]] = None) -> int:
        if self.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
            raise TableError("当前轮尚未结束，不能开新轮（新轮不重置牌靴，牌靴在账本层维护）")
        participants = list(self.players) if participants is None else list(participants)
        if len(participants) != len(set(participants)) or any(p not in self.players for p in participants):
            raise TableError("参与座位必须唯一且属于本桌")
        self.round_no += 1
        self.phase = PHASE_DEALING
        self.dealer = SeatState(DEALER)
        for name in self.players:
            self.players[name] = SeatState(name)
        self.dealer_hole_checked_negative = False
        self.split_order_violations = []
        if participants is None:
            participants = list(self.players.keys())
        for p in participants:
            if p not in self.players:
                raise TableError(f"未知座位: {p}")
        self.participants = participants
        return self.round_no

    def enter_play_phase(self) -> None:
        if self.phase != PHASE_DEALING:
            raise TableError("只有发牌中可以进入玩家操作阶段")
        self.phase = PHASE_IN_PROGRESS

    def new_hand_id(self, seat_name: str) -> str:
        self._hand_seq += 1
        return f"R{self.round_no}-{seat_name}-H{self._hand_seq}"

    def seat(self, name: str) -> SeatState:
        if name == DEALER:
            return self.dealer
        if name not in self.players:
            raise TableError(f"未知座位: {name}")
        return self.players[name]

    def ensure_playing(self) -> None:
        if self.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
            raise TableError("当前没有进行中的轮次")

    # ---------- 发牌 / 揭示 ----------
    def add_card(self, seat_name: str, rank: str,
                 suit: Optional[str] = None, track_id: Optional[str] = None,
                 hidden: bool = False, hand_id: Optional[str] = None,
                 event_id: Optional[str] = None) -> HandInstance:
        """向座位手牌加一张牌。hidden=True 表示牌面未知（记录为 UNKNOWN）。"""
        self.ensure_playing()
        if seat_name != DEALER and seat_name not in self.participants:
            raise TableError("该座位本轮未参与，请在开轮时选择参与座位")
        seat = self.seat(seat_name)
        card_rank = UNKNOWN if hidden else rank
        card = Card(rank=card_rank, suit=suit, track_id=track_id, event_id=event_id)
        if not seat.hands:
            generated_id = self.new_hand_id(seat_name)
            seat.hands.append(HandInstance(hand_id=hand_id or generated_id))
        hand = seat.get_hand(hand_id) if hand_id else seat.hands[-1]
        if hand.is_closed:
            raise TableError(f"{seat_name} 的该手牌已结束，不能再收牌")
        if hand.is_split_ace and self.rules.split_ace_hit_once and len(hand.cards) >= 2:
            raise TableError("分A已补一张；若允许再分A，请先执行分牌，不能直接补第三张")
        if seat_name != DEALER and len(hand.cards) >= 2 and self._needs_decision_peek():
            raise TableError("该模板在玩家补牌前要求明确非BJ检查")
        if seat_name != DEALER and hand.total()[0] == 21:
            raise TableError("已达21点，不能继续收牌")
        # 同一物理牌（track_id）在同一手只能出现一次，防重复帧重复入账
        if track_id and any(c.track_id == track_id for c in hand.cards):
            raise TableError(f"同一物理牌 {track_id} 已在该手牌中，拒绝重复入账")
        hand.cards.append(card)
        try:
            self.validate_dealer_path()
        except Exception:
            hand.cards.pop()
            raise
        hand.awaiting_hit = False
        self._observe_split_order(seat_name, hand, event_id or "card")
        # 加倍后只允许补一张，补完自动停牌
        if hand.doubled and len(hand.cards) >= 3:
            hand.stood = True
        # 分 A 限补一张
        if hand.is_split_ace and self.rules.split_ace_hit_once and len(hand.cards) >= 2:
            can_resplit, _ = self._pair_split_eligible(hand)
            hand.stood = not can_resplit
        return hand

    def reveal_card(self, seat_name: str, hand_id: str, track_id: Optional[str],
                    rank: str, suit: Optional[str] = None,
                    event_id: Optional[str] = None) -> Card:
        """把一张已发出的未知牌揭示为已知牌面（不新扣牌，牌靴层同步揭示）。"""
        hand = self.seat(seat_name).get_hand(hand_id)
        for c in hand.cards:
            if c.rank == UNKNOWN and (event_id is None or c.event_id == event_id) and (track_id is None or c.track_id == track_id):
                # frozen dataclass → 用新对象替换
                idx = hand.cards.index(c)
                new_card = Card(rank=rank, suit=suit if suit else c.suit,
                                track_id=c.track_id, event_id=c.event_id)
                if rank == UNKNOWN:
                    raise TableError("揭示结果必须为已知牌面")
                hand.cards[idx] = new_card
                try:
                    self.validate_peek()
                    self.validate_dealer_path()
                except Exception:
                    hand.cards[idx] = c
                    raise
                if hand.is_split_ace and self.rules.split_ace_hit_once and len(hand.cards) == 2:
                    can_resplit, _ = self._pair_split_eligible(hand)
                    hand.stood = not can_resplit
                return new_card
        raise TableError(f"{seat_name} 没有可揭示的未知牌")

    # ---------- 动作合法性 ----------
    def _pair_split_eligible(self, hand: HandInstance) -> Tuple[bool, str]:
        if len(hand.cards) != 2:
            return False, "只有两张牌的手牌可以分牌"
        r1, r2 = hand.cards[0].rank, hand.cards[1].rank
        if UNKNOWN in (r1, r2):
            return False, "存在未揭示牌，不能判断能否分牌"
        if TEN_BUCKET in (r1, r2) and self.rules.split_match == "same_rank":
            if not all(is_ten_value(r) for r in (r1, r2)):
                return False, "规则要求相同牌面才能分牌"
            return False, "10点未细分无法判断配对规则，拒绝分牌，请先细分牌面"
        seat = None
        for s in [self.dealer, *self.players.values()]:
            if hand in s.hands:
                seat = s
                break
        if seat and len(seat.hands) >= self.rules.max_split_hands:
            return False, f"已达到最大分牌手数 {self.rules.max_split_hands}"
        if self.rules.split_match == "same_rank":
            if r1 != r2:
                return False, "规则要求相同牌面才能分牌"
        else:  # same_value
            v1 = 11 if r1 == "A" else (10 if is_ten_value(r1) else int(r1))
            v2 = 11 if r2 == "A" else (10 if is_ten_value(r2) else int(r2))
            if v1 != v2:
                return False, "规则要求相同点值才能分牌"
        if r1 == "A" and r2 == "A":
            has_split_ace = any(h.is_split_ace for h in (seat.hands if seat else []))
            if has_split_ace and not self.rules.resplit_aces:
                return False, "当前规则不允许再分 A"
            if self.rules.split_ace_hit_once is None:
                return False, "分A补牌限制未确认"
        return True, ""

    def action_states(self, seat_name: str, hand_id: str):
        """Structured legality shared by commands, analysis coverage, and UI."""
        actions = (ACTION_HIT, ACTION_STAND, ACTION_DOUBLE, ACTION_SPLIT, ACTION_SURRENDER)
        def disabled(code, reason):
            return {a: ActionState(a, False, "inapplicable", code, reason) for a in actions}
        def state(action, allowed, reason, code="NOT_APPLICABLE", status="inapplicable"):
            return ActionState(action, allowed, "available" if allowed else status,
                               "LEGAL" if allowed else code, reason)
        if self.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
            return disabled("ROUND_INACTIVE", "尚未进入玩家操作阶段")
        if seat_name == DEALER:
            return disabled("DEALER_TARGET", "玩家动作不适用于庄家；请直接录入庄家牌")
        hand = self.seat(seat_name).get_hand(hand_id)
        if self.dealer.hands and is_natural_blackjack([c.rank for c in self.dealer.hands[0].cards]):
            return disabled("DEALER_BLACKJACK", "庄家已确认Blackjack，本轮停止玩家操作")
        if self._needs_decision_peek():
            return disabled("PEEK_REQUIRED", "等待庄家A/十点明牌的非BJ检查")
        if hand.is_closed:
            return disabled("HAND_CLOSED", "该手牌已结束")
        if len(hand.cards) < 2 or hand.hidden_cards:
            return disabled("PLAYER_INCOMPLETE", "初始两张牌未完整确认")
        if hand.doubled or hand.awaiting_hit:
            return disabled("DRAW_PENDING", "已选择补牌或加倍，请先录入该张牌")
        result = {a: state(a, False, "不适用") for a in actions}
        result[ACTION_STAND] = state(ACTION_STAND, True, "可停牌")
        if hand.total()[0] == 21:
            for a in actions:
                if a != ACTION_STAND:
                    result[a] = state(a, False, "已达21点", "TOTAL_21")
            return result
        limited_ace = hand.is_split_ace and self.rules.split_ace_hit_once
        result[ACTION_HIT] = state(ACTION_HIT, not limited_ace, "分A仅限一张补牌" if limited_ace else "可补牌", "SPLIT_ACE_LIMIT")
        double_reason = "可加倍（加倍后只补一张）"
        double_ok = True
        if limited_ace:
            double_ok, double_reason = False, "分A仅限一张补牌"
        elif len(hand.cards) != 2:
            double_ok, double_reason = False, "仅两张牌时可加倍"
        elif hand.from_split and self.rules.double_after_split is not True:
            double_ok, double_reason = False, "分牌后加倍未获规则允许（禁止或未确认）"
        elif self.rules.double_on_totals is not None and hand.total()[0] not in self.rules.double_on_totals:
            double_ok, double_reason = False, f"仅允许在 {self.rules.double_on_totals} 点加倍"
        result[ACTION_DOUBLE] = state(ACTION_DOUBLE, double_ok, double_reason, "DOUBLE_RULE")
        split_ok, split_reason = self._pair_split_eligible(hand)
        ranks = tuple(c.rank for c in hand.cards)
        room_to_split = len(self.seat(seat_name).hands) < self.rules.max_split_hands
        uncertain_pair = (not split_ok and len(ranks) == 2 and room_to_split and (
            (self.rules.split_match == "same_rank" and TEN_BUCKET in ranks and all(is_ten_value(r) for r in ranks))
            or (ranks == ("A", "A") and (self.rules.split_ace_hit_once is None
                or hand.from_split and self.rules.resplit_aces is None))))
        result[ACTION_SPLIT] = state(ACTION_SPLIT, split_ok, "可分牌" if split_ok else split_reason,
                                     "PAIR_UNKNOWN" if uncertain_pair else "SPLIT_RULE",
                                     "pending" if uncertain_pair else "inapplicable")
        surrender_ok, surrender_reason = True, "可投降（损失半注）"
        if self.rules.surrender is None:
            surrender_ok, surrender_reason = False, "当前规则不支持投降"
        elif len(hand.cards) != 2 or hand.actions or hand.from_split:
            surrender_ok, surrender_reason = False, "仅初始两张牌、未采取动作前可投降"
        elif self.rules.surrender == "late" and (not self.dealer.hands or (
            self._dealer_may_have_bj() and not self.dealer_hole_checked_negative and not self._dealer_revealed())):
            surrender_ok, surrender_reason = False, "晚投降需等庄家完成 Blackjack 检查"
        result[ACTION_SURRENDER] = state(ACTION_SURRENDER, surrender_ok, surrender_reason, "SURRENDER_RULE")
        return result

    def legal_actions(self, seat_name: str, hand_id: str) -> Dict[str, str]:
        # Compatibility presentation API. No consumer should infer legality from wording.
        return {a: item.reason for a, item in self.action_states(seat_name, hand_id).items()}

    def _dealer_may_have_bj(self) -> bool:
        up = [c for c in self.dealer.hands[0].cards] if self.dealer.hands else []
        known = [c.rank for c in up if c.rank != UNKNOWN]
        return any(r == "A" or is_ten_value(r) for r in known)

    def _needs_decision_peek(self):
        return (self.rules.american_hole_card is True
                and self.rules.check_bj_when == "before_player_actions_A_T"
                and self._dealer_may_have_bj() and not self.dealer_hole_checked_negative
                and not self._dealer_revealed())

    def _dealer_revealed(self) -> bool:
        return self.dealer.hands and len(self.dealer.hands[0].cards) >= 2 and not any(
            c.rank == UNKNOWN for c in self.dealer.hands[0].cards)

    def apply_action(self, seat_name: str, hand_id: str, action: str,
                     new_hand_id: Optional[str] = None) -> dict:
        """执行动作并返回附带信息（如分牌产生的新手牌）。"""
        legal = self.action_states(seat_name, hand_id)
        if action not in legal:
            raise TableError(f"未知动作: {action}")
        if not legal[action].allowed:
            raise TableError(f"{action} 不被允许：{legal[action].reason}")
        seat = self.seat(seat_name)
        hand = seat.get_hand(hand_id)
        if action == ACTION_SPLIT and new_hand_id and any(
            h.hand_id == new_hand_id for s in [self.dealer, *self.players.values()] for h in s.hands
        ):
            raise TableError("分牌后的手牌ID必须唯一")
        self._observe_split_order(seat_name, hand, action)
        hand.actions.append(action)
        self.phase = PHASE_IN_PROGRESS
        info: dict = {"action": action}
        if action == ACTION_STAND:
            hand.stood = True
        elif action == ACTION_HIT:
            hand.awaiting_hit = True
        elif action == ACTION_DOUBLE:
            hand.doubled = True
            hand.bet_units *= 2
        elif action == ACTION_SURRENDER:
            hand.surrendered = True
        elif action == ACTION_SPLIT:
            cards = hand.cards
            is_ace_pair = cards[0].rank == "A"
            # 原手保留第一张，新建第二只手继承第二张
            moved = cards.pop()
            new_hand = HandInstance(
                hand_id=new_hand_id or self.new_hand_id(seat_name),
                cards=[moved], parent_id=hand.hand_id,
                from_split=True, is_split_ace=is_ace_pair,
            )
            if new_hand_id:
                self._hand_seq += 1
            hand.from_split = True
            hand.is_split_ace = is_ace_pair
            hand.stood = False
            # 插入到原手后面
            idx = seat.hands.index(hand)
            seat.hands.insert(idx + 1, new_hand)
            hand.actions[-1] = f"{ACTION_SPLIT}->{new_hand.hand_id}"
            info["new_hand_id"] = new_hand.hand_id
        return info

    @staticmethod
    def split_hand_closed(hand):
        # Existing custom resplit-A recording may keep A/A open. The two-hand
        # template closes limited aces in add_card(), using its actual rules.
        return hand.is_closed or hand.total()[0] == 21

    def _observe_split_order(self, seat_name, hand, cause):
        """Retain accepted recording; derive analysis incompatibility on replay."""
        if seat_name == DEALER or not hand.from_split:
            return
        hands = self.seat(seat_name).hands
        prior = hands[:hands.index(hand)]
        if any(not self.split_hand_closed(h) for h in prior):
            self.split_order_violations.append({"hand_id": hand.hand_id, "cause": cause})

    def mark_peek_negative(self) -> None:
        """庄家已检查底牌且确认不是 Blackjack（检查否定结果也是当时信息）。"""
        self.ensure_playing()
        if self.rules.american_hole_card is False:
            raise TableError("无底牌规则不支持底牌检查")
        if not self.dealer.hands or len(self.dealer.hands[0].cards) != 2 or not self._dealer_may_have_bj():
            raise TableError("检查非BJ需要庄家两张牌及A或10点明牌")
        self.dealer_hole_checked_negative = True
        try:
            self.validate_peek()
        except Exception:
            self.dealer_hole_checked_negative = False
            raise

    def validate_peek(self) -> None:
        if (self.dealer_hole_checked_negative and self.dealer.hands
                and is_natural_blackjack([c.rank for c in self.dealer.hands[0].cards])):
            raise TableError("庄家牌面与此前非BJ检查结果矛盾，请追加纠错")

    def validate_dealer_path(self) -> None:
        """Every visible dealer prefix must obey the declared stopping rule."""
        if not self.dealer.hands:
            return
        cards = self.dealer.hands[0].cards
        for length in range(2, len(cards)):
            score, soft = hand_total([c.rank for c in cards[:length]])
            if score is None:
                continue
            stops = score > 21 or score >= 18 or (score == 17 and (not soft or self.rules.dealer_soft17 == "S17"))
            if stops:
                raise TableError("庄家已达到桌规停牌终点，后续牌与规则矛盾")

    def missing_observations(self) -> List[dict]:
        """Provable missing draws, independent of payout or settlement availability."""
        missing = []
        dealer = self.dealer.hands[0] if self.dealer.hands else None
        if dealer is None or len(dealer.cards) < 2:
            missing.append({"code": "DEALER_INITIAL_MISSING", "seat": DEALER})
        hands = []
        for name in self.participants:
            seat_hands = self.players[name].hands
            if not seat_hands:
                missing.append({"code": "PLAYER_INITIAL_MISSING", "seat": name})
            for hand in seat_hands:
                hands.append(hand)
                code = ("PLAYER_INITIAL_MISSING" if len(hand.cards) < 2 else
                        "PLAYER_DRAW_PENDING" if hand.awaiting_hit or (hand.doubled and len(hand.cards) < 3) else None)
                if code:
                    missing.append({"code": code, "seat": name, "hand_id": hand.hand_id})
        if dealer and len(dealer.cards) >= 2:
            score, soft = dealer.total()
            comparing = any(not h.surrendered and not h.is_bust and not (
                is_natural_blackjack(h.ranks) and not h.from_split) for h in hands)
            if comparing and score is not None and (score < 17 or (
                    score == 17 and soft and self.rules.dealer_soft17 == "H17")):
                missing.append({"code": "DEALER_DRAW_PENDING", "seat": DEALER})
        return missing

    # ---------- 结算（确定性记账，非 EV）----------
    def settle(self) -> List[dict]:
        """本轮结束时逐手结算。庄家底牌必须已揭示。"""
        self.ensure_playing()
        if not self.dealer.hands or len(self.dealer.hands[0].cards) < 2 or any(
                c.rank == UNKNOWN for c in self.dealer.hands[0].cards):
            raise TableError("庄家底牌尚未揭示，不能结算")
        dealer_ranks = self.dealer.hands[0].ranks
        dealer_total, _ = hand_total(dealer_ranks)
        dealer_bj = is_natural_blackjack(dealer_ranks)
        self.validate_peek()
        self.validate_dealer_path()
        if not self.participants or any(not self.players[name].hands for name in self.participants):
            raise TableError("已声明参与座位存在漏录手牌，不能省略该玩家结算")
        hands = [h for name in self.participants for h in self.players[name].hands]
        for h in hands:
            if h.awaiting_hit:
                raise TableError("已选择补牌但牌尚未录入，不能结算")
            if len(h.cards) < 2 or h.hidden_cards:
                raise TableError("玩家手牌未完整确认，不能结算")
            if h.doubled and len(h.cards) != 3:
                raise TableError("加倍手尚未补完唯一一张牌，不能结算")
            if is_natural_blackjack(h.ranks) and not h.from_split and not dealer_bj and self.rules.blackjack_payout is None:
                raise TableError("Blackjack赔付未确认，不能计算净收益")
        if dealer_bj and any(h.from_split or h.doubled for h in hands) and self.rules.dealer_bj_extra_bet_rule != "all_bets_lost":
            raise TableError("庄家BJ追加注结算规则未知或未支持，不能计算净收益")
        comparing = any(not h.surrendered and not h.is_bust and not (
            is_natural_blackjack(h.ranks) and not h.from_split) for h in hands)
        if comparing and not dealer_bj and self.rules.dealer_soft17 is None:
            raise TableError("庄家S17/H17未确认，不能认定当前牌为终局")
        if comparing and self.rules.dealer_soft17 and (dealer_total < 17 or (
            dealer_total == 17 and hand_total(dealer_ranks)[1] and self.rules.dealer_soft17 == "H17")):
            raise TableError("按已声明桌规庄家仍需补牌，不能结算")
        results = []
        for name in self.participants:
            seat = self.players[name]
            for h in seat.hands:
                stake = h.bet_units
                if h.surrendered:
                    # 投降只可能发生在初始两张牌、加倍之前，按原始注损失半注
                    outcome, net = "投降", -0.5
                elif h.is_bust:
                    outcome, net = "爆牌负", -stake
                else:
                    p_total, _ = h.total()
                    p_bj = is_natural_blackjack(h.ranks) and not h.from_split
                    if dealer_bj and p_bj:
                        outcome, net = "BJ平", 0.0
                    elif dealer_bj:
                        outcome, net = "庄家BJ负", -stake
                    elif p_bj:
                        b, a = self.rules.blackjack_payout
                        outcome, net = "玩家BJ", stake * b / a
                    elif dealer_total is not None and dealer_total > 21:
                        outcome, net = "庄家爆牌胜", stake
                    elif p_total == dealer_total:
                        outcome, net = "平", 0.0
                    elif (dealer_total is None) or (p_total is not None and p_total > dealer_total):
                        outcome, net = "胜", stake
                    else:
                        outcome, net = "负", -stake
                h.settled_result = outcome
                h.settled_net = net
                results.append({
                    "round": self.round_no, "seat": name, "hand_id": h.hand_id,
                    "result": outcome, "net_units": net,
                })
        self.phase = PHASE_SETTLED
        return results

    def all_player_hands_closed(self) -> bool:
        for name in self.participants:
            if self.seat(name).active_hands():
                return False
        return True
