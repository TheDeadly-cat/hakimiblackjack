"""Read-only presentation of a published result; never starts or narrows a solve."""
from dataclasses import dataclass
import math

from .contracts import ACTION_ZH, AVAILABLE, STATUS_ZH
from .split_contracts import DAS_ENGINE_LEGACY
from ..core.cards import hand_total


@dataclass(frozen=True)
class Choice:
    action: str
    label: str
    rank_label: str
    ev: float
    profit: float
    push: float
    loss: float
    additional: float


@dataclass(frozen=True)
class DecisionSummary:
    state: str
    identity: str
    message: str = ""
    choices: tuple[Choice, ...] = ()
    all_choices: tuple[Choice, ...] = ()
    ev_gap: float | None = None
    partial: bool = False
    tied: bool = False
    historical: bool = False
    notes: tuple[str, ...] = ()


def hand_text(ranks):
    ranks = tuple(ranks)
    total, soft = hand_total(ranks)
    score = "待确认" if total is None else ("软" if soft else "") + str(total)
    return f"{' '.join(ranks) or '未发牌'}  ·  {score}"


def input_identity(info, dealer_text=None):
    hands = info.get("hands")
    if hands:
        selected = next((i for i, h in enumerate(hands) if h['hand_id'] == info['hand_id']), 0)
        active = next((i for i, h in enumerate(hands) if h['hand_id'] == info.get('active_hand_id')), None)
        shown = active if active is not None else selected
        text = f"{info['seat']} · 第{shown + 1}手  {hand_text(hands[shown]['ranks'])}"
        if active is not None and active != selected:
            text += f"（当前行动；查看第{selected + 1}手）"
    else:
        text = f"{info['seat']} · 第1手  {hand_text(info.get('player_ranks', ())) }"
    up = {1: 'A', 10: '10点'}.get(info.get('dealer_up'), info.get('dealer_up', '—'))
    return f"{dealer_text or f'庄家明牌 {up}'}    |    {text}"


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("结果含无效数值")
    return value


def _choice(action, item, rank_label):
    ev = _number(item['ev'])
    distribution = item['net_distribution']
    if not isinstance(distribution, dict) or not distribution:
        raise ValueError("缺少完整净收益分布")
    values = [(float(net), _number(p)) for net, p in distribution.items()]
    if any(not math.isfinite(net) or p < 0 or p > 1 + 1e-10 for net, p in values):
        raise ValueError("净收益分布无效")
    if abs(math.fsum(p for _, p in values) - 1) > 1e-10:
        raise ValueError("净收益分布未归一")
    if abs(math.fsum(net * p for net, p in values) - ev) > 1e-10:
        raise ValueError("EV与净收益分布不一致")
    additional = _number(item.get('additional_investment', 1 if action in ('double', 'split') else 0))
    if additional < 0:
        raise ValueError("追加投入无效")
    return Choice(action, ACTION_ZH.get(action, item.get('label', action)), rank_label, ev,
                  math.fsum(p for net, p in values if net > 0),
                  math.fsum(p for net, p in values if net == 0),
                  math.fsum(p for net, p in values if net < 0), additional)


def summarize_result(result, historical=False):
    """Rank ALL available legal actions by EV; aggregate original-bet total nets."""
    identity = input_identity(result['input'])
    prefix = "历史 · " if historical else ""
    notes = ("历史时点结果，不代表当前输入",) if historical else ()
    from .seat_scenario import note_for
    if result['input'].get('information_json'):
        scenario_note = note_for(result['input'])
        if scenario_note:
            notes += (scenario_note,)
    if result['status'] != AVAILABLE:
        return DecisionSummary(prefix + STATUS_ZH.get(result['status'], '需核对'), identity,
                               result.get('reason', '结果未完成'), historical=historical, notes=notes)
    if result.get('engine_version') == DAS_ENGINE_LEGACY:
        return DecisionSummary("历史 · 需核对", identity, "旧算法结果保留在详情，请按原时点另存复算。",
                               historical=True, notes=notes)
    legal = tuple(result['input'].get('legal_actions', ()))
    if legal == ('deal',):
        return DecisionSummary(prefix + "等待补牌", identity, "已选动作，只需录入确定要发的一张牌。",
                               historical=historical, notes=notes)
    if legal == ('complete',):
        return DecisionSummary(prefix + "等待庄家", identity, "两手已完成，当前没有玩家操作建议。",
                               historical=historical, notes=notes)
    items = result.get('actions', {})
    missing = [a for a in legal if items.get(a, {}).get('status') != AVAILABLE]
    uncertain = result['input'].get('uncertain_actions', ())
    partial = bool(result.get('partial_comparison') or missing or uncertain)
    try:
        if not legal or any(a not in ACTION_ZH for a in legal):
            raise ValueError("没有可比较的玩家动作")
        computed = [_choice(a, items[a], '') for a in legal if items.get(a, {}).get('status') == AVAILABLE]
        computed.sort(key=lambda c: (-c.ev, c.action))
        if not computed:
            raise ValueError("尚无完整动作结果")
        tolerance = _number(result.get('numerical_tolerance', 1e-10))
        if tolerance < 0:
            raise ValueError("数值区分范围无效")
    except (ValueError, KeyError, TypeError, OverflowError) as error:
        return DecisionSummary(prefix + "需核对", identity, str(error), historical=historical, notes=notes)
    gap = computed[0].ev - computed[1].ev if len(computed) > 1 else None
    tied = gap is not None and gap <= tolerance
    top_ties = sum(computed[0].ev - c.ev <= tolerance for c in computed)
    runner_ties = sum(abs(computed[1].ev - c.ev) <= tolerance for c in computed[1:]) if len(computed) > 1 else 0
    ranked = []
    for i, choice in enumerate(computed):
        if partial:
            rank_label = '已算中并列' if computed[0].ev - choice.ev <= tolerance and tied else f'已算第{i + 1}'
        elif len(computed) == 1:
            rank_label = '唯一合法动作'
        elif i < top_ties and tied:
            rank_label = '并列'
        elif i == 0:
            rank_label = '最佳'
        elif abs(computed[1].ev - choice.ev) <= tolerance:
            rank_label = '次佳并列' if runner_ties > 1 else '次佳'
        else:
            rank_label = f'第{i + 1}'
        ranked.append(Choice(**{**choice.__dict__, 'rank_label': rank_label}))
    state = '部分比较' if partial else '并列／差距不可区分' if tied else '已更新'
    reasons = []
    if partial:
        names = [ACTION_ZH.get(a, a) for a in dict.fromkeys([*missing, *uncertain])]
        reasons.append('仅比较已计算动作；' + ('、'.join(names) + '未完成或待核对。' if names else '不能标为全局最佳。'))
    if top_ties > 2 or runner_ties > 1 and not tied:
        reasons.append('还有并列动作，展开查看完整比较。')
    if all(c.ev < 0 for c in computed):
        reasons.append('已算动作均为负；' + ('较高EV仅代表相对少亏。' if partial else '最佳仅代表相对少亏。'))
    return DecisionSummary(prefix + state, identity, ' '.join(reasons), tuple(ranked[:2]), tuple(ranked),
                           gap, partial, tied, historical,
                           notes + ('当前手牌分析，不代表下一轮有优势。',))


def format_summary_details(summary):
    if not summary.all_choices:
        return ''
    lines = ['净盈利概率 = 本次原始投注最终合计净收益 > 0（分牌后合并两手）',
             '概率不是识别置信度或算法正确性的置信度。']
    for c in summary.all_choices:
        lines.append(f'{c.label}：净盈利 {c.profit:.4%} / 不盈不亏 {c.push:.4%} / 净亏损 {c.loss:.4%}')
    if summary.ev_gap is not None:
        lines.append(f'前两项EV差距：{summary.ev_gap:.10g}' + ('（数值范围内不可区分）' if summary.tied else ''))
    return '\n'.join(lines) + '\n\n'
