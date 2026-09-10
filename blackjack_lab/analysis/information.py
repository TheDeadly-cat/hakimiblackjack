"""Validate the selected event prefix and expose only the visible information set."""
from dataclasses import asdict
import json

from ..core.cards import TEN_RANKS, UNKNOWN
from ..core.table import DEALER, PHASE_DEALING, PHASE_IN_PROGRESS
from ..core.rules import CONFIRM_VERIFIED
from ..ledger.ledger import EventLedger
from .contracts import AnalysisInput, InputUnavailable, canonical, digest, ACTION_ZH, UNSUPPORTED, INAPPLICABLE


def build_input(ledger, seat, hand_id=None, through_seq=None):
    all_events = ledger.to_list()
    seq = all_events[-1]["seq"] if through_seq is None and all_events else through_seq
    if type(seq) is not int or not any(e["seq"] == seq for e in all_events):
        raise InputUnavailable("PREFIX_MISSING", "所选历史时点不存在")
    prefix = [e for e in all_events if e["seq"] <= seq]
    try:
        validated = EventLedger.from_list(ledger.session_id, prefix)
        current = validated.replay().current
    except Exception as error:
        raise InputUnavailable("INVALID_EVENTS", f"事件校验失败，不能用于分析：{error}") from error
    if current is None or current.closed or current.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
        raise InputUnavailable("ROUND_INACTIVE", "请选择进行中的手牌；已结束轮次请查看结束前的历史时点", INAPPLICABLE)
    rules, shoe, table = current.rules, current.shoe, current.table
    if rules.confirm_status != CONFIRM_VERIFIED:
        raise InputUnavailable("RULES_UNCONFIRMED", "规则尚未确认；可主动选择研究模板建立新牌靴")
    if rules.start_from_new_shoe is not True:
        raise InputUnavailable("START_UNKNOWN", "尚未确认从完整新牌靴开始记录")
    if rules.burn_cards_known is not True:
        raise InputUnavailable("BURN_COUNT_UNKNOWN", "烧牌数量尚未核实")
    if rules.initial_burn_count is None:
        raise InputUnavailable("INITIAL_BURN_UNKNOWN", "尚未明确初始烧牌数量，不能把未知默认为零")
    if shoe.gap or shoe.pending_candidates:
        raise InputUnavailable("RECORD_GAP", "存在观察缺口或待核对牌，暂停当前牌靴精确分析")
    if shoe.burn_unknown:
        raise InputUnavailable("NONZERO_BURN", "已知数量的未知烧牌尚未纳入本版模型；保留记录", UNSUPPORTED)
    supported = (
        rules.shoe_model == "finite_no_replacement" and rules.dealer_soft17 == "S17"
        and rules.blackjack_payout == (3, 2) and rules.american_hole_card is True
        and rules.check_bj_when == "before_player_actions_A_T"
        and rules.dealer_bj_extra_bet_rule == "all_bets_lost"
        and rules.double_on_totals is None and rules.surrender in (None, "late")
    )
    if not supported:
        raise InputUnavailable("RULE_COMBINATION_UNSUPPORTED", "本版分析仅验收S17、3:2、美式决策前检查、任意两张加倍、无投降/晚投降的模板", UNSUPPORTED)
    if seat == DEALER or len(table.participants) != 1 or seat not in table.participants:
        raise InputUnavailable("SINGLE_PLAYER_ONLY", "当前仅支持单参与玩家的目标手牌；7座位录牌仍可使用", UNSUPPORTED)
    hands = table.players[seat].hands
    if len(hands) != 1 or any(h.from_split for h in hands):
        raise InputUnavailable("SPLIT_HAND_UNSUPPORTED", "分牌后的EV尚未实现；可查看分牌前的部分动作比较", UNSUPPORTED)
    hand = hands[0]
    if hand_id is not None and hand.hand_id != hand_id:
        raise InputUnavailable("TARGET_CHANGED", "目标手牌与当前记录不一致")
    if hand.hidden_cards or len(hand.cards) < 2:
        raise InputUnavailable("PLAYER_INCOMPLETE", "目标手牌尚未完整确认")
    if hand.is_closed or hand.doubled or hand.awaiting_hit:
        raise InputUnavailable("NO_DECISION", "该手牌已结束或正在等待已选动作的补牌", INAPPLICABLE)
    dealer = table.dealer.hands[0].cards if table.dealer.hands else []
    shown = [c for c in dealer if c.rank != UNKNOWN]
    holes = [info for info in current.unresolved.values() if info["seat"] == DEALER
             and info["round_id"] == current.round_id and info["face_state"] == "hidden"]
    if len(dealer) != 2 or len(shown) != 1 or len(holes) != 1 or shoe.unrevealed_out != 1:
        raise InputUnavailable("DEALER_INFORMATION", "需要一张庄家明牌及一张正常未揭示底牌，且没有其他未核对移除；底牌已揭示时可选此前历史时点")
    values = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "T": 10}
    up = values[shown[0].rank]
    peek = table.dealer_hole_checked_negative
    if up in (1, 10) and not peek:
        raise InputUnavailable("PEEK_REQUIRED", "此模板需先记录庄家A/十点明牌的非BJ检查结果；未知底牌本身不会阻止分析")
    states = table.action_states(seat, hand.hand_id)
    legal = tuple(a for a, zh in ACTION_ZH.items() if states[zh].allowed)
    uncertain = tuple(a for a, zh in ACTION_ZH.items() if states[zh].status == "pending")
    if not legal:
        raise InputUnavailable("NO_LEGAL_ACTION", "当前无可比较动作", INAPPLICABLE)
    counts = tuple(shoe.remaining[r] for r in ("A", "2", "3", "4", "5", "6", "7", "8", "9")) + (sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    if any(n < 0 for n in counts) or shoe.physical_remaining() != sum(counts) - 1:
        raise InputUnavailable("COUNT_INCONSISTENT", "牌面集合与物理剩余不一致")
    information = {"exact_out": shoe.exact_out, "t_bucket_out": shoe.t_bucket_out,
        "unrevealed_out": shoe.unrevealed_out, "burn_unknown": shoe.burn_unknown,
        "gap": shoe.gap, "pending_candidates": shoe.pending_candidates,
        "dealer_hole": "one_unknown_physical_card", "negative_peek": peek,
        "action_states": {a: asdict(state) for a, state in states.items()}}
    return AnalysisInput(ledger.session_id, current.shoe_id, current.round_id, seq,
        digest(prefix), seat, hand.hand_id, rules.n_decks, canonical(json.loads(rules.to_json())),
        canonical(information), counts, shoe.physical_remaining(),
        tuple(values[c.rank] for c in hand.cards), tuple(c.rank for c in hand.cards), up, peek, legal, uncertain)
