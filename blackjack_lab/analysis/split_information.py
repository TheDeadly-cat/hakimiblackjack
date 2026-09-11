"""Adapt an already validated, observation-complete event prefix to v2 input."""
from dataclasses import asdict
import json

from ..core.cards import TEN_RANKS, UNKNOWN
from ..core.table import DEALER
from .contracts import ACTION_ZH, InputUnavailable, INAPPLICABLE, UNSUPPORTED, canonical, digest
from .split_contracts import (
    SplitAnalysisInput, SplitHand, VALUES, supported_split_rules, supported_das_rules,
    DAS_ENGINE, DAS_STRATEGY, _hand_can_das)


def build_split_input(session_id, current, seat, selected_id, seq, prefix):
    rules, shoe, table = current.rules, current.shoe, current.table
    das = supported_das_rules(rules)
    if not supported_split_rules(rules) and not das:
        raise InputUnavailable("SPLIT_RULE_UNSUPPORTED", "两手分析需要明确的顺序、无再分、无DAS或已声明DAS、分A一张研究模板", UNSUPPORTED)
    hands = table.players[seat].hands
    if not hands or len(hands) > 2:
        raise InputUnavailable("PLAYER_INCOMPLETE", "需要原始一手或顺序分牌后的两手")
    selected_id = selected_id or hands[0].hand_id
    if selected_id not in [h.hand_id for h in hands]:
        raise InputUnavailable("TARGET_CHANGED", "选中手牌不属于此事件前缀")
    if table.split_order_violations:
        raise InputUnavailable("SPLIT_DEAL_ORDER", "已保留录入：第二手在首手结束前收到牌或采取动作，不能套用顺序分牌分析", UNSUPPORTED)
    if any(h.hidden_cards or not h.cards or h.surrendered for h in hands):
        raise InputUnavailable("SPLIT_HAND_INFORMATION", "分牌手存在未知牌或首发未支持的投入/动作状态")
    if not das and any(h.doubled or h.bet_units != 1 for h in hands):
        raise InputUnavailable("SPLIT_HAND_INFORMATION", "分牌手存在未知牌或首发未支持的投入/动作状态")
    if das and any(h.bet_units not in (1, 2) for h in hands):
        raise InputUnavailable("SPLIT_HAND_INFORMATION", "分牌手存在未知牌或首发未支持的投入/动作状态")
    if len(hands) == 1 and (hands[0].from_split or len(hands[0].cards) < 2
            or hands[0].is_closed or hands[0].awaiting_hit):
        raise InputUnavailable("NO_DECISION", "原始手牌尚无可比较的当前动作", INAPPLICABLE)
    dealer = table.dealer.hands[0].cards if table.dealer.hands else []
    shown = [c for c in dealer if c.rank != UNKNOWN]
    holes = [info for info in current.unresolved.values() if info["seat"] == DEALER
             and info["round_id"] == current.round_id and info["face_state"] == "hidden"]
    if len(dealer) != 2 or len(shown) != 1 or len(holes) != 1 or shoe.unrevealed_out != 1:
        raise InputUnavailable("DEALER_INFORMATION", "需要庄家一张明牌及一张正常未知底牌；可选择揭示前历史时点")
    up, peek = VALUES[shown[0].rank], table.dealer_hole_checked_negative
    if up in (1, 10) and not peek:
        raise InputUnavailable("PEEK_REQUIRED", "请先记录庄家A/十点明牌的非BJ检查")
    items = []
    for h in hands:
        origin_length = 1 if h.from_split else 2
        # An unsplit natural 21 still permits the legacy stand comparison.
        closed = table.split_hand_closed(h) if len(hands) == 2 else False
        units = h.bet_units
        if type(units) is float and units in (1.0, 2.0):
            units = int(units)
        items.append(SplitHand(h.hand_id, h.parent_id, tuple(c.rank for c in h.cards),
            tuple(c.event_id for c in h.cards), tuple(c.rank for c in h.cards[:origin_length]),
            tuple(c.event_id for c in h.cards[:origin_length]), h.from_split, h.is_split_ace,
            closed, len(h.cards) == 1 or h.awaiting_hit, units))
    pending = tuple(h.hand_id for h in items if not h.closed)
    active = pending[0] if pending else None
    states = table.action_states(seat, active) if active else {}
    uncertain = ()
    if len(hands) == 1:
        legal = tuple(a for a, zh in ACTION_ZH.items() if states[zh].allowed)
        uncertain = tuple(a for a, zh in ACTION_ZH.items() if states[zh].status == "pending")
    else:
        hand = next((h for h in items if h.hand_id == active), None)
        if hand is None:
            legal = ("complete",)
        elif hand.forced_draw:
            legal = ("deal",)
        elif das and _hand_can_das(hand):
            legal = ("stand", "hit", "double")
        else:
            legal = ("stand", "hit")
    counts = tuple(shoe.remaining[r] for r in ("A", "2", "3", "4", "5", "6", "7", "8", "9")) + (
        sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    info = {"exact_out": shoe.exact_out, "t_bucket_out": shoe.t_bucket_out,
        "unrevealed_out": shoe.unrevealed_out, "burn_unknown": shoe.burn_unknown,
        "gap": shoe.gap, "pending_candidates": shoe.pending_candidates,
        "dealer_hole": "one_unknown_physical_card", "negative_peek": peek,
        "split_order_violations": list(table.split_order_violations),
        "action_states": {a: asdict(state) for a, state in states.items()}}
    extra = {}
    if das:
        extra = dict(engine_version=DAS_ENGINE, strategy_version=DAS_STRATEGY,
                     support_scope="S17/3:2/US-peek/zero-burn/single-player/two-sequential/DAS-non-ace/no-resplit")
    snapshot = SplitAnalysisInput(session_id, current.shoe_id, current.round_id, seq, digest(prefix),
        seat, selected_id, rules.n_decks, canonical(json.loads(rules.to_json())), canonical(info),
        counts, shoe.physical_remaining(), tuple(items), active, pending, up, peek, legal, uncertain,
        **extra)
    try:
        snapshot.validate()
    except ValueError as error:
        raise InputUnavailable("SPLIT_INPUT_INVALID", str(error), UNSUPPORTED) from error
    return snapshot
