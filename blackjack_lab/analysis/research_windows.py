"""Research window definitions plus the small-shoe pre-deal input builder.

Two windows must not be mixed:

- current_hand: after the player's cards and dealer upcard are known.
- pre_deal: before the next round is dealt, given remaining composition I_t.

A better action, a positive current-hand EV, and a positive next-round opening
EV are three different claims. Win rate is not a substitute for net EV.
"""
from __future__ import annotations

import math

from .contracts import (
    AVAILABLE, FAILED, INAPPLICABLE, PENDING, UNSUPPORTED, InputUnavailable, canonical, digest,
    research_rules,
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

EV_POSITIVE = "positive"
EV_NONPOSITIVE = "nonpositive"
EV_INDETERMINATE = "indeterminate"
EV_UNAVAILABLE = "unavailable"
EVALUABLE_EV_STATES = (EV_POSITIVE, EV_NONPOSITIVE)
WINDOW_POSITIVE_SUPPORTED = "positive_supported"
WINDOW_NONPOSITIVE_SUPPORTED = "nonpositive_supported"
WINDOW_STATE_NAMES = {
    EV_POSITIVE: WINDOW_POSITIVE_SUPPORTED,
    EV_NONPOSITIVE: WINDOW_NONPOSITIVE_SUPPORTED,
    EV_INDETERMINATE: EV_INDETERMINATE,
    EV_UNAVAILABLE: EV_UNAVAILABLE,
}


def _finite_ev(value):
    if type(value) is bool:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _declared_near_zero_band(record):
    """Conservative sign band only. Not a verified bound on the whole solver."""
    if not isinstance(record, dict):
        return None
    value = record.get("numerical_tolerance")
    if type(value) is bool:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _opening_window_kind(record):
    if record.get("window_kind") == WINDOW_CURRENT_HAND or record.get("window") == WINDOW_CURRENT_HAND:
        return WINDOW_CURRENT_HAND
    if record.get("window_kind") == WINDOW_PRE_DEAL or record.get("window") == WINDOW_PRE_DEAL:
        return WINDOW_PRE_DEAL
    return None


def _opening_method_identity(record):
    for key in ("evaluation_method", "method"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def classify_ev_record(record):
    """Split evaluable sign from missing/failed evaluation.

    positive: status available and EV is finite and greater than any declared
        near-zero band
    nonpositive: available and EV is a finite number that is not > 0
    indeterminate: interval/model incomplete, a Monte Carlo point estimate
        that is not allowed to claim a window, or a positive value inside a
        declared near-zero band
    unavailable: unsupported, timeout, missing, failed, bool/non-finite EV
    """
    if not isinstance(record, dict):
        return EV_UNAVAILABLE
    if record.get("indeterminate") is True or record.get("window_claim_allowed") is False:
        return EV_INDETERMINATE
    ev = _finite_ev(record.get("ev"))
    if record.get("status") != AVAILABLE or ev is None:
        return EV_UNAVAILABLE
    if ev > 0:
        band = _declared_near_zero_band(record)
        if band is not None and ev <= band:
            return EV_INDETERMINATE
        return EV_POSITIVE
    return EV_NONPOSITIVE


def classify_opening_window(record):
    """Opening-window state. Current-hand EV cannot satisfy this."""
    if not isinstance(record, dict):
        return EV_UNAVAILABLE
    kind = _opening_window_kind(record)
    if kind != WINDOW_PRE_DEAL:
        return EV_UNAVAILABLE
    state = classify_ev_record(record)
    if state in EVALUABLE_EV_STATES and _opening_method_identity(record) is None:
        return EV_UNAVAILABLE
    return state


def window_state(record):
    """4.4 opening-window label: supported sign, indeterminate, or unavailable."""
    return WINDOW_STATE_NAMES[classify_opening_window(record)]


def deadline_supports_timely_claim(result):
    """True only when a comparable deadline exists and the result beat it.

    Missing deadline: research as-of is allowed; a timely-catch claim is not.
    """
    allowed, _reason = assess_timely_live_claim(result, live_applicable=True)
    return allowed


def assess_timely_live_claim(result, *, live_applicable=False, historical=False,
                             observation_moved=False, input_moved=False,
                             recomputed_from=None):
    """Separate 'matches the current record' from 'ready before the decision closed'."""
    if historical or recomputed_from is not None:
        return False, "historical_recompute"
    if observation_moved or input_moved:
        return False, "stale_input_or_knowledge"
    if live_applicable is not True:
        return False, "not_live_current"
    if not isinstance(result, dict):
        return False, "result_missing"
    deadline = result.get("decision_deadline")
    ready = result.get("result_ready_at")
    if deadline is None:
        return False, "decision_deadline_unknown"
    if ready is None:
        return False, "result_ready_at_unknown"
    deadline_value = _finite_ev(deadline)
    ready_value = _finite_ev(ready)
    if deadline_value is None or ready_value is None:
        return False, "illegal_timestamp"
    domain = result.get("clock_domain")
    deadline_clock = result.get("decision_deadline_clock") or domain
    ready_clock = result.get("result_ready_clock") or domain
    if not isinstance(deadline_clock, str) or not deadline_clock.strip():
        return False, "clock_domain_incomparable"
    if deadline_clock != ready_clock:
        return False, "clock_domain_incomparable"
    if ready_value > deadline_value:
        return False, "result_after_deadline"
    return True, "before_deadline"


def evaluation_scope(records):
    """Zero-window is only claimed after a complete declared evaluation."""
    states = [classify_opening_window(item) for item in records]
    n = len(states)
    positive = sum(1 for state in states if state == EV_POSITIVE)
    nonpositive = sum(1 for state in states if state == EV_NONPOSITIVE)
    indeterminate = sum(1 for state in states if state == EV_INDETERMINATE)
    unavailable = sum(1 for state in states if state == EV_UNAVAILABLE)
    complete = n > 0 and indeterminate == 0 and unavailable == 0
    no_positive_signal = positive == 0
    if complete:
        verified = no_positive_signal
    else:
        verified = None
    return {
        "n": n,
        "positive": positive,
        "nonpositive": nonpositive,
        "indeterminate": indeterminate,
        "unavailable": unavailable,
        "evaluated_count": positive + nonpositive,
        "unassessable_count": unavailable,
        "complete_evaluation": complete,
        "no_positive_signal_detected": no_positive_signal,
        "no_positive_detected": no_positive_signal,
        "no_positive_window_in_complete_evaluation": complete and no_positive_signal,
        "incomplete_cannot_claim_zero_window": n == 0 or not complete,
        "zero_window": complete and no_positive_signal,
        "verified_no_positive_over_declared_domain": verified,
    }


def confusion_matrix(rows, truth_key="truth", observer_key="observer"):
    """Score FP/FN only when truth itself was evaluable.

    Truth unavailable: do not call the observer a false positive or a joint negative.
    Truth positive and observer missing/timeout/unsupported: missed_unavailable, not FN.
    """
    counts = {
        "n": len(rows),
        "denominator_kept": len(rows),
        "scored": 0,
        "agree_positive": 0,
        "agree_negative": 0,
        "false_positive": 0,
        "false_negative": 0,
        "missed_unavailable": 0,
        "observer_unavailable_on_nonpositive": 0,
        "truth_unavailable": 0,
        "indeterminate_pairs": 0,
    }
    for row in rows:
        record = row if isinstance(row, dict) else {}
        truth = classify_opening_window(record.get(truth_key))
        observer = classify_opening_window(record.get(observer_key))
        if truth == EV_INDETERMINATE or observer == EV_INDETERMINATE:
            counts["indeterminate_pairs"] += 1
            continue
        if truth == EV_UNAVAILABLE:
            counts["truth_unavailable"] += 1
            continue
        if observer == EV_UNAVAILABLE:
            if truth == EV_POSITIVE:
                counts["missed_unavailable"] += 1
            else:
                counts["observer_unavailable_on_nonpositive"] += 1
            continue
        counts["scored"] += 1
        if truth == EV_POSITIVE and observer == EV_POSITIVE:
            counts["agree_positive"] += 1
        elif truth == EV_NONPOSITIVE and observer == EV_NONPOSITIVE:
            counts["agree_negative"] += 1
        elif observer == EV_POSITIVE:
            counts["false_positive"] += 1
        else:
            counts["false_negative"] += 1
    counts["missed_due_to_abstention"] = counts["missed_unavailable"]
    counts["classified_false_negative"] = counts["false_negative"]
    return counts


KIND_LIVE_CURRENT = "live_current"
KIND_MANUAL_ASOF = "manual_asof"
KIND_REPLAY = "replay"
KIND_SYNTHETIC = "synthetic"
KIND_STALE = "stale_asof"


def result_heading(historical=False, live_applicable=True, applicability_kind=None):
    if historical:
        return "历史分析 · "
    kind = applicability_kind
    if kind is None:
        kind = KIND_LIVE_CURRENT if live_applicable else KIND_STALE
    if kind == KIND_LIVE_CURRENT:
        return "当前 · 已核对至来源时点 · "
    if kind == KIND_REPLAY:
        return "录像回放 · 不适用于当前真实牌桌 · "
    if kind == KIND_SYNTHETIC:
        return "合成研究 · "
    if kind == KIND_MANUAL_ASOF:
        return "截至人工确认记录 · "
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


def require_point_values(values, *, what="发牌前组成"):
    """Reject bool/float/NaN before any int() coercion. Point values stay 1–10."""
    if isinstance(values, (str, bytes)) or values is None:
        raise ValueError(f"{what}必须是点值序列")
    try:
        items = tuple(values)
    except TypeError as error:
        raise ValueError(f"{what}必须是点值序列") from error
    if not items:
        raise ValueError(f"{what}不能为空")
    out = []
    for value in items:
        if type(value) is not int:
            raise ValueError(f"{what}只接受点值 1–10 的整数；不能把 {value!r} 截成整数")
        if not 1 <= value <= 10:
            raise ValueError(f"{what}只接受点值 1–10")
        out.append(value)
    return tuple(out)


def require_count_vector(counts, *, what="发牌前十桶"):
    if isinstance(counts, (str, bytes)) or counts is None:
        raise ValueError(f"{what}必须是十个非负整数点值桶")
    try:
        items = tuple(counts)
    except TypeError as error:
        raise ValueError(f"{what}必须是十个非负整数点值桶") from error
    if len(items) != 10:
        raise ValueError(f"{what}必须是十个非负整数点值桶")
    for value in items:
        if type(value) is not int or value < 0:
            raise ValueError(f"{what}必须是非负整数；不能把 {value!r} 截成整数")
    return items


def parse_surrender_token(text):
    """CLI/UI token to the pre-deal surrender rule. Missing is not silently late."""
    if text in ("none", "no-surrender", "不支持"):
        return None
    if text == "late":
        return "late"
    raise ValueError("投降规则只接受 none 或 late")


def parse_remaining_tokens(text):
    """Parse a toy remaining pack: ranks or point values, comma/space separated."""
    if text is None or not str(text).strip():
        raise ValueError("剩余组成不能为空")
    parts = [item.strip().upper() for item in str(text).replace("，", ",").replace(" ", ",").split(",")
             if item.strip()]
    mapping = {"A": 1, "J": 10, "Q": 10, "K": 10, "T": 10, "10": 10}
    values = []
    for item in parts:
        if any(marker in item for marker in (".", "+", "-", "E")) and item not in mapping:
            raise ValueError(f"发牌前组成只接受点值 1–10 的整数；不能把 {item!r} 截成整数")
        if item in mapping:
            values.append(mapping[item])
        elif item.isdigit() and 1 <= int(item) <= 10:
            values.append(int(item))
        else:
            raise ValueError(f"发牌前组成不能识别: {item}")
    return require_point_values(values)


def counts_from_values(values):
    values = require_point_values(values)
    return tuple(values.count(i) for i in range(1, 11))


def format_predeal_result(result, historical=False, live_applicable=True, applicability_reason=None,
                          applicability_kind=None):
    info = result["input"]
    source = ""
    info_obj = {}
    raw_info = info.get("information_json")
    if isinstance(raw_info, str) and raw_info:
        import json
        try:
            info_obj = json.loads(raw_info) or {}
            source = info_obj.get("source") or ""
        except (ValueError, TypeError, RecursionError):
            source = ""
    if not historical and source == "explicit-composition":
        applicability_kind = KIND_SYNTHETIC
    heading = result_heading(historical, live_applicable, applicability_kind)
    remaining = info.get("physical_remaining", sum(info.get("counts") or ()))
    lines = [f"{heading}发牌前开局优势 · 剩余 {remaining} 张 · 策略 {result.get('strategy_version', '')}"]
    state = result.get("window_state")
    if state == WINDOW_POSITIVE_SUPPORTED:
        lines.append("窗口状态：可评且为正；仍须看输入是否真实，合成组成不是可靠窗口")
    elif state == WINDOW_NONPOSITIVE_SUPPORTED:
        lines.append("窗口状态：可评且非正")
    elif state == EV_INDETERMINATE:
        lines.append("窗口状态：不确定，不能写成已证明开局窗")
    elif state == EV_UNAVAILABLE:
        lines.append("窗口状态：不可用，不能写成已证明无窗口")
    if result.get("status") != "available":
        lines.append(f"{result.get('reason_code', '')}：{result.get('reason', '')}")
        return "\n".join(lines)
    if historical:
        lines.append("原时点结果，不代表当前输入")
        if source == "explicit-composition":
            lines.append("合成剩余组成；不是当前 6/7/8 副整靴开局，也不是已发手牌 EV。")
    elif applicability_kind == KIND_SYNTHETIC:
        lines.append("合成剩余组成；不是当前 6/7/8 副整靴开局，也不是已发手牌 EV。")
    elif not live_applicable:
        detail = applicability_reason or "观察状态已变化"
        lines.append("账本未变；数字对应已确认前缀，不适用于眼前牌桌：" + str(detail))
    lines.append(WINDOW_ZH[WINDOW_PRE_DEAL] + "：对下一轮所有初始发牌求期望。")
    lines.append("不是当前已发手牌 EV；未把庄家 A／十点明牌预设为已检查非 BJ。")
    lines.append(f"每原始单位净 EV {result['ev']:+.6f} · 方差 {result['variance']:.6f}")
    parts = result["outcomes"]
    lines.append(f"净赢 {parts['win']:.2%} · 打和 {parts['push']:.2%} · 净亏 {parts['lose']:.2%}")
    lines.append("口径：赢次数多于输次数不是净优势的充分条件。")
    if "surrender" in result:
        surrender = result["surrender"]
    elif "surrender" in info:
        surrender = info["surrender"]
    else:
        surrender = "missing"
    if surrender is None:
        lines.append("策略：可见信息下未分牌最优停/补/加倍；无投降、无保险。")
    elif surrender == "late":
        lines.append("策略：可见信息下未分牌最优停/补/加倍/晚投降；无保险。")
    else:
        lines.append("策略：投降规则未写入结果，不能按研究模板补全。")
    if info_obj.get("t_bucket_out"):
        lines.append("十点未细分已计入十点桶；不是 10/J/Q/K 身份，也不能做同牌级分牌。")
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
    from .predeal_contracts import PREDEAL_MAX_REMAINING, PreDealInput, predeal_support_scope
    from ..core.rules import CONFIRM_VERIFIED

    if counts is not None:
        try:
            counts = require_count_vector(counts)
        except ValueError as error:
            raise InputUnavailable("PREDEAL_COUNTS_INVALID", str(error), FAILED) from error
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
        if rules is None:
            raise InputUnavailable(
                "PREDEAL_RULES_MISSING",
                "合成剩余组成必须给出规则，不能默认晚投降研究模板",
                FAILED,
            )
        if rules.confirm_status != CONFIRM_VERIFIED or not _predeal_rules_ok(rules):
            raise InputUnavailable("RULE_COMBINATION_UNSUPPORTED", PREDEAL_UNAVAILABLE_REASON, UNSUPPORTED)
        identity = {"kind": "explicit-remaining", "counts": counts}
        information = {"gap": False, "pending_candidates": 0, "unrevealed_out": 0,
                       "burn_unknown": 0, "t_bucket_out": 0, "remaining_is_complete": True,
                       "ten_rank_identity_known": True,
                       "source": "explicit-composition"}
        return PreDealInput(
            counts=counts, physical_remaining=remaining, rules_json=rules.to_json(),
            information_json=canonical(information), session_id=session_id,
            shoe_id=digest(identity), round_id="predeal-next", through_seq=0,
            prefix_digest=digest(identity), n_decks=None,
            surrender=rules.surrender, support_scope=predeal_support_scope(rules.surrender),
        )

    if ledger is None:
        raise InputUnavailable("PREDEAL_INPUT_MISSING", "发牌前分析需要剩余牌靴组成，或已确认账本时点", PENDING)
    state = inspect_ledger_opening(ledger, through_seq)
    if state["remaining"] > PREDEAL_MAX_REMAINING:
        raise InputUnavailable(
            "PREDEAL_SHOE_TOO_LARGE",
            f"本版发牌前精确入口只穷举剩余≤{PREDEAL_MAX_REMAINING}张；当前剩余{state['remaining']}张，6/7/8副整靴开局需另开离线实验。",
            UNSUPPORTED,
        )
    if state["remaining"] < 4:
        raise InputUnavailable("PREDEAL_TOO_FEW_CARDS", "剩余牌不足下一轮初始四张", INAPPLICABLE)
    return PreDealInput(
        counts=state["counts"], physical_remaining=state["remaining"],
        rules_json=state["rules"].to_json(),
        information_json=canonical(state["information"]), session_id=ledger.session_id,
        shoe_id=state["current"].shoe_id, round_id=state["current"].round_id or "predeal-next",
        through_seq=state["seq"], prefix_digest=state["prefix_digest"],
        n_decks=state["rules"].n_decks, surrender=state["rules"].surrender,
        support_scope=predeal_support_scope(state["rules"].surrender),
    )


def inspect_ledger_opening(ledger, through_seq=None):
    """Freeze a confirmed ledger prefix. Unknown cards are not averaged into a pack."""
    from ..core.cards import TEN_RANKS
    from ..core.rules import CONFIRM_VERIFIED
    from ..core.table import (
        PHASE_DEALING, PHASE_IN_PROGRESS, PHASE_NO_ROUND, PHASE_SETTLED, PHASE_UNSETTLED,
    )
    from ..ledger.ledger import EventLedger

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
    if table.phase not in (PHASE_SETTLED, PHASE_UNSETTLED) and (
            _table_has_cards(table) or table.phase == PHASE_IN_PROGRESS):
        raise InputUnavailable(
            "ROUND_ALREADY_DEALT",
            "这一轮已经发牌；发牌前入口只回答下一轮尚未发出时的开局优势。已发手牌请用当前手牌计算。",
            INAPPLICABLE,
        )
    if table.phase not in (PHASE_NO_ROUND, PHASE_SETTLED, PHASE_UNSETTLED, PHASE_DEALING):
        raise InputUnavailable("ROUND_INACTIVE", "当前阶段不能做发牌前分析", INAPPLICABLE)
    if table.phase == PHASE_DEALING and len(table.participants) != 1:
        raise InputUnavailable("SINGLE_PLAYER_ONLY", "发牌前精确分析当前仅支持单参与玩家", UNSUPPORTED)
    if table.phase in (PHASE_SETTLED, PHASE_UNSETTLED) and len(table.participants) != 1:
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
    if shoe.unrevealed_out:
        raise InputUnavailable("COMPOSITION_UNKNOWN", "未揭示牌会使下一轮组成不是精确已知", UNSUPPORTED)
    if not _predeal_rules_ok(rules):
        raise InputUnavailable("RULE_COMBINATION_UNSUPPORTED", "本版发牌前分析仅验收S17、3:2、美式决策前检查模板", UNSUPPORTED)
    counts = tuple(shoe.remaining[r] for r in ("A", "2", "3", "4", "5", "6", "7", "8", "9")) + (
        sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    remaining = shoe.physical_remaining()
    if remaining is None or remaining != sum(counts):
        raise InputUnavailable("COUNT_INCONSISTENT", "发牌前物理剩余与牌面十桶不一致")
    information = {"gap": False, "pending_candidates": 0, "unrevealed_out": 0,
                   "burn_unknown": 0, "t_bucket_out": shoe.t_bucket_out,
                   "remaining_is_complete": True,
                   "ten_rank_identity_known": shoe.t_bucket_out == 0,
                   "source": "ledger-prefix", "phase": table.phase}
    return {
        "seq": seq,
        "prefix": prefix,
        "prefix_digest": digest(prefix),
        "current": current,
        "rules": rules,
        "shoe": shoe,
        "table": table,
        "counts": counts,
        "remaining": remaining,
        "information": information,
    }


def build_offline_mc_input(ledger, *, policy, n_samples, seed, through_seq=None,
                           family_size=1, alpha=0.05, play_budget_seconds=2.0):
    """Confirmed ledger remaining → frozen-policy MC. Does not use the 16-card exact cap."""
    from .offline_mc_contracts import OfflineMcInput, pack_from_counts
    if ledger is None:
        raise InputUnavailable("PREDEAL_INPUT_MISSING", "离线MC需要已确认账本时点", PENDING)
    state = inspect_ledger_opening(ledger, through_seq)
    if state["remaining"] < 4:
        raise InputUnavailable("PREDEAL_TOO_FEW_CARDS", "剩余牌不足下一轮初始四张", INAPPLICABLE)
    pack = pack_from_counts(state["counts"])
    return OfflineMcInput(
        counts=state["counts"], pack=pack, physical_remaining=state["remaining"],
        rules_json=state["rules"].to_json(),
        information_json=canonical(state["information"]),
        session_id=ledger.session_id, shoe_id=state["current"].shoe_id,
        round_id=state["current"].round_id or "predeal-next",
        through_seq=state["seq"], prefix_digest=state["prefix_digest"],
        n_decks=state["rules"].n_decks, surrender=state["rules"].surrender,
        policy_id=policy, n_samples=n_samples, seed=seed, family_size=family_size,
        alpha=alpha, play_budget_seconds=play_budget_seconds,
    )


def source_mode_from_information(information_json):
    """Map pre-deal information source to a 4.4 source_mode. Unknown stays unknown."""
    source = None
    if isinstance(information_json, dict):
        source = information_json.get("source")
    elif isinstance(information_json, str) and information_json:
        import json
        try:
            source = (json.loads(information_json) or {}).get("source")
        except (ValueError, TypeError, RecursionError):
            source = None
    if source == "explicit-composition":
        return "synthetic-composition"
    if source == "ledger-prefix":
        return "ledger-prefix"
    if source:
        return str(source)
    return "unknown"


def knowledge_revision_token(revision):
    """Stable digest of request-time knowledge identity. None if no observation."""
    if revision is None:
        return None
    identity = revision.knowledge_identity() if hasattr(revision, "knowledge_identity") else revision
    return digest(list(identity))


def window_kind_for_current_analysis():
    return WINDOW_CURRENT_HAND
