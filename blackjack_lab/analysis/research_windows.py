"""Research window definitions plus the small-shoe pre-deal input builder.

Two windows must not be mixed:

- current_hand: after the player's cards and dealer upcard are known.
- pre_deal: before the next round is dealt, given remaining composition I_t.

A better action, a positive current-hand EV, and a positive next-round opening
EV are three different claims. Win rate is not a substitute for net EV.
"""
from __future__ import annotations

from .contracts import (
    INAPPLICABLE, PENDING, UNSUPPORTED, InputUnavailable, canonical, digest, research_rules,
)

WINDOW_CURRENT_HAND = "current_hand"
WINDOW_PRE_DEAL = "pre_deal"

WINDOW_ZH = {
    WINDOW_CURRENT_HAND: "当前已发手牌条件优势",
    WINDOW_PRE_DEAL: "下一轮发牌前开局优势",
}

PREDEAL_UNAVAILABLE_REASON = (
    "发牌前分析不能用当前手牌 EV 冒充；也不能把庄家 A／十点明牌预设为已经检查不是 blackjack"
)

CURRENT_HAND_SCOPE = (
    "研究窗口：当前已发手牌条件优势（看见自己的牌和庄家明牌之后）。"
    "不是下一轮发牌前开局优势；不提供注额建议。"
)


def result_heading(historical=False, live_applicable=True):
    if historical:
        return "历史分析 · "
    if live_applicable:
        return "当前 · "
    return "截至已确认记录 · 不适用于当前牌桌 · "


def net_outcome_summary(net_distribution):
    """Aggregate signed net units into win / push / lose. Blackjack +1.5 counts as a win."""
    win = push = lose = 0.0
    for net, probability in net_distribution.items():
        value = float(net)
        share = float(probability)
        if value > 0:
            win += share
        elif value < 0:
            lose += share
        else:
            push += share
    return {"win": win, "push": push, "lose": lose}


def net_ev(net_distribution):
    return sum(float(net) * float(p) for net, p in net_distribution.items())


def format_outcome_line(net_distribution):
    parts = net_outcome_summary(net_distribution)
    return (f"净赢 {parts['win']:.2%} · 打和 {parts['push']:.2%} · 净亏 {parts['lose']:.2%}"
            f"（赢次数多于输次数不是净优势的充分条件）")


def hypothetical_winrate_not_equal_to_ev():
    """Illustrative distribution only; not a table measurement or solver result."""
    distribution = {"1": 0.40, "1.5": 0.05, "-1": 0.47, "0": 0.08}
    return distribution, net_outcome_summary(distribution), net_ev(distribution)


def parse_remaining_tokens(text):
    """Parse a toy remaining pack: ranks or point values, comma/space separated."""
    if text is None or not str(text).strip():
        raise ValueError("剩余组成不能为空")
    parts = [item.strip().upper() for item in str(text).replace("，", ",").replace(" ", ",").split(",")
             if item.strip()]
    mapping = {"A": 1, "J": 10, "Q": 10, "K": 10, "T": 10, "10": 10}
    values = []
    for item in parts:
        if item in mapping:
            values.append(mapping[item])
        elif item.isdigit() and 1 <= int(item) <= 10:
            values.append(int(item))
        else:
            raise ValueError(f"发牌前组成不能识别: {item}")
    return tuple(values)


def counts_from_values(values):
    values = tuple(int(v) for v in values)
    if any(v < 1 or v > 10 for v in values):
        raise ValueError("发牌前组成只接受点值 1–10")
    return tuple(values.count(i) for i in range(1, 11))


def format_predeal_result(result, historical=False, live_applicable=True, applicability_reason=None):
    heading = result_heading(historical, live_applicable)
    info = result["input"]
    remaining = info.get("physical_remaining", sum(info.get("counts") or ()))
    lines = [f"{heading}发牌前开局优势 · 剩余 {remaining} 张 · 策略 {result.get('strategy_version', '')}"]
    if result.get("status") != "available":
        lines.append(f"{result.get('reason_code', '')}：{result.get('reason', '')}")
        return "\n".join(lines)
    if historical:
        lines.append("原时点结果，不代表当前输入")
    elif not live_applicable:
        detail = applicability_reason or "观察状态已变化"
        lines.append("账本未变；数字对应已确认前缀，不适用于眼前牌桌：" + str(detail))
    source = ""
    raw_info = info.get("information_json")
    if isinstance(raw_info, str) and raw_info:
        import json
        try:
            source = json.loads(raw_info).get("source") or ""
        except (ValueError, TypeError, RecursionError):
            source = ""
    if source == "explicit-composition":
        lines.append("合成剩余组成；不是当前 6/7/8 副整靴开局，也不是已发手牌 EV。")
    lines.append(WINDOW_ZH[WINDOW_PRE_DEAL] + "：对下一轮所有初始发牌求期望。")
    lines.append("不是当前已发手牌 EV；未把庄家 A／十点明牌预设为已检查非 BJ。")
    lines.append(f"每原始单位净 EV {result['ev']:+.6f} · 方差 {result['variance']:.6f}")
    parts = result["outcomes"]
    lines.append(f"净赢 {parts['win']:.2%} · 打和 {parts['push']:.2%} · 净亏 {parts['lose']:.2%}")
    lines.append("口径：赢次数多于输次数不是净优势的充分条件。")
    lines.append("策略：可见信息下未分牌最优停/补/加倍/晚投降；无保险。")
    lines.append(result.get("approximation") or "")
    lines.append(f"数值不确定性：{result.get('numerical_uncertainty', '')}")
    lines.append(f"模型不确定性：{result.get('model_uncertainty', '')}")
    lines.append(f"耗时 {result['elapsed_seconds']:.3f}s · 引擎 {result['engine_version']}")
    lines.append(f"输入摘要：{result['input_digest'][:16]}")
    return "\n".join(line for line in lines if line)


def _predeal_rules_ok(rules):
    return (
        rules.shoe_model == "finite_no_replacement" and rules.dealer_soft17 == "S17"
        and rules.blackjack_payout == (3, 2) and rules.american_hole_card is True
        and rules.check_bj_when == "before_player_actions_A_T"
        and rules.dealer_bj_extra_bet_rule == "all_bets_lost"
        and rules.double_on_totals is None and rules.surrender in (None, "late")
    )


def _table_has_cards(table):
    seats = [table.dealer, *table.players.values()]
    return any(card for seat in seats for hand in seat.hands for card in hand.cards)


def build_predeal_input(ledger=None, *, counts=None, through_seq=None, rules=None,
                        session_id="synthetic-predeal"):
    """Independent pre-deal entry. Current-hand EV cannot satisfy this."""
    from .predeal_contracts import PREDEAL_MAX_REMAINING, PreDealInput
    from ..core.cards import TEN_RANKS
    from ..core.rules import CONFIRM_VERIFIED
    from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS, PHASE_NO_ROUND, PHASE_SETTLED
    from ..ledger.ledger import EventLedger

    if counts is not None:
        counts = tuple(int(n) for n in counts)
        remaining = sum(counts)
        if remaining > PREDEAL_MAX_REMAINING:
            raise InputUnavailable(
                "PREDEAL_SHOE_TOO_LARGE",
                f"本版发牌前精确入口只穷举剩余≤{PREDEAL_MAX_REMAINING}张的牌靴；当前剩余{remaining}张。"
                "不能把当前手牌 EV 改称为开局优势。",
                UNSUPPORTED,
            )
        if remaining < 4:
            raise InputUnavailable("PREDEAL_TOO_FEW_CARDS", "剩余牌不足下一轮初始四张", INAPPLICABLE)
        rules = rules or research_rules(6)
        if rules.confirm_status != CONFIRM_VERIFIED or not _predeal_rules_ok(rules):
            raise InputUnavailable("RULE_COMBINATION_UNSUPPORTED", PREDEAL_UNAVAILABLE_REASON, UNSUPPORTED)
        identity = {"kind": "explicit-remaining", "counts": counts}
        information = {"gap": False, "pending_candidates": 0, "unrevealed_out": 0,
                       "burn_unknown": 0, "t_bucket_out": 0, "remaining_is_complete": True,
                       "source": "explicit-composition"}
        return PreDealInput(
            counts=counts, physical_remaining=remaining, rules_json=rules.to_json(),
            information_json=canonical(information), session_id=session_id,
            shoe_id=digest(identity), round_id="predeal-next", through_seq=0,
            prefix_digest=digest(identity), n_decks=None,
        )

    if ledger is None:
        raise InputUnavailable("PREDEAL_INPUT_MISSING", "发牌前分析需要剩余牌靴组成，或已确认账本时点", PENDING)

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
    if current is None or current.closed:
        raise InputUnavailable("SHOE_INACTIVE", "没有进行中的牌靴，不能计算下一轮发牌前优势", INAPPLICABLE)
    rules, shoe, table = current.rules, current.shoe, current.table
    if _table_has_cards(table) or table.phase == PHASE_IN_PROGRESS:
        raise InputUnavailable(
            "ROUND_ALREADY_DEALT",
            "这一轮已经发牌；发牌前入口只回答下一轮尚未发出时的开局优势。已发手牌请用当前手牌计算。",
            INAPPLICABLE,
        )
    if table.phase not in (PHASE_NO_ROUND, PHASE_SETTLED, PHASE_DEALING):
        raise InputUnavailable("ROUND_INACTIVE", "当前阶段不能做发牌前分析", INAPPLICABLE)
    if table.phase == PHASE_DEALING and len(table.participants) != 1:
        raise InputUnavailable("SINGLE_PLAYER_ONLY", "发牌前精确分析当前仅支持单参与玩家", UNSUPPORTED)
    if table.phase == PHASE_SETTLED and len(table.participants) != 1:
        raise InputUnavailable("SINGLE_PLAYER_ONLY", "上一轮不是单参与玩家，不能把其他人的牌删掉后做开局分析", UNSUPPORTED)
    if rules.confirm_status != CONFIRM_VERIFIED:
        raise InputUnavailable("RULES_UNCONFIRMED", "规则尚未确认；可主动选择研究模板建立新牌靴")
    if rules.start_from_new_shoe is not True:
        raise InputUnavailable("START_UNKNOWN", "尚未确认从完整新牌靴开始记录")
    if rules.burn_cards_known is not True:
        raise InputUnavailable("BURN_COUNT_UNKNOWN", "烧牌数量尚未核实")
    if rules.initial_burn_count is None:
        raise InputUnavailable("INITIAL_BURN_UNKNOWN", "尚未明确初始烧牌数量，不能把未知默认为零")
    incomplete_rounds = [r for r in current.round_observations if r["status"] != "complete"]
    if incomplete_rounds:
        numbers = "、".join(str(r["round_no"]) for r in incomplete_rounds)
        raise InputUnavailable("PRIOR_ROUND_OBSERVATION", f"此前第{numbers}轮存在漏录或观察完整性未知；精确开局分析暂停")
    if shoe.gap or shoe.pending_candidates:
        raise InputUnavailable("RECORD_GAP", "存在观察缺口或待核对牌，暂停发牌前精确分析")
    if shoe.burn_unknown:
        raise InputUnavailable("NONZERO_BURN", "已知数量的未知烧牌尚未纳入本版模型；保留记录", UNSUPPORTED)
    if shoe.unrevealed_out or shoe.t_bucket_out:
        raise InputUnavailable("COMPOSITION_UNKNOWN", "未揭示牌或十点未细分会使下一轮组成不是精确已知", UNSUPPORTED)
    if not _predeal_rules_ok(rules):
        raise InputUnavailable("RULE_COMBINATION_UNSUPPORTED", "本版发牌前分析仅验收S17、3:2、美式决策前检查模板", UNSUPPORTED)
    counts = tuple(shoe.remaining[r] for r in ("A", "2", "3", "4", "5", "6", "7", "8", "9")) + (
        sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    remaining = shoe.physical_remaining()
    if remaining is None or remaining != sum(counts):
        raise InputUnavailable("COUNT_INCONSISTENT", "发牌前物理剩余与牌面十桶不一致")
    if remaining > PREDEAL_MAX_REMAINING:
        raise InputUnavailable(
            "PREDEAL_SHOE_TOO_LARGE",
            f"本版发牌前精确入口只穷举剩余≤{PREDEAL_MAX_REMAINING}张；当前剩余{remaining}张，6/7/8副整靴开局需另开离线实验。",
            UNSUPPORTED,
        )
    if remaining < 4:
        raise InputUnavailable("PREDEAL_TOO_FEW_CARDS", "剩余牌不足下一轮初始四张", INAPPLICABLE)
    information = {"gap": False, "pending_candidates": 0, "unrevealed_out": 0,
                   "burn_unknown": 0, "t_bucket_out": 0, "remaining_is_complete": True,
                   "source": "ledger-prefix", "phase": table.phase}
    return PreDealInput(
        counts=counts, physical_remaining=remaining, rules_json=rules.to_json(),
        information_json=canonical(information), session_id=ledger.session_id,
        shoe_id=current.shoe_id, round_id=current.round_id or "predeal-next",
        through_seq=seq, prefix_digest=digest(prefix), n_decks=rules.n_decks,
    )


def window_kind_for_current_analysis():
    return WINDOW_CURRENT_HAND
