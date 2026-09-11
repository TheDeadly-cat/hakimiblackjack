"""Compose original-hand alternatives and the exact shared-shoe total objective."""
from time import perf_counter
import math

from ..core.cards import is_natural_blackjack
from .contracts import AVAILABLE, INAPPLICABLE, PENDING, TIMEOUT, FAILED, UNSUPPORTED, ACTION_ZH
from .probability import CalculationStopped, InsufficientCards, FiniteModel, LABELS, DEALER_LABELS, total
from .split_actions import solve_split_counts
from .split_contracts import SPLIT_ENGINE, SPLIT_STRATEGY
from .native_backend import solve_presplit_native

SPLIT_ACTION_ZH = {**ACTION_ZH, "deal": "录入已确定要发的一张牌", "complete": "两手完成，等待庄家结算"}
JOINT_KEYS = {f'{a},{b}' for a in (-1, 0, 1) for b in (-1, 0, 1)}
TOTAL_NETS = {-2.0, -1.0, 0.0, 1.0, 2.0}


def _finite_number(value, name):
    if type(value) not in (int, float):
        raise ArithmeticError(f'{name}必须为有限数值（不能为布尔值）')
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ArithmeticError(f'{name}必须为有限数值')
    return float(value)


def _validate_probability_map(values, expected_keys, name):
    from .service import _validate_distribution
    if not isinstance(values, dict):
        raise ArithmeticError(f'{name}必须为概率对象，缺失时不能填零冒充已计算')
    if expected_keys is not None and set(values) != set(expected_keys):
        raise ArithmeticError(f'{name}概率集合键不完整，禁止填零冒充已计算')
    if not values:
        raise ArithmeticError(f'{name}概率集合为空')
    for probability in values.values():
        _finite_number(probability, name)
    _validate_distribution(values)


def _validate_hit_bust(value):
    probability = _finite_number(value, 'hit_bust')
    if not 0 <= probability <= 1.0000000001:
        raise ArithmeticError('补牌爆牌概率越界')


def _require_backend_actions(snapshot, numbers):
    actions = numbers.get('actions') if isinstance(numbers, dict) else None
    if not isinstance(actions, dict) or set(actions) != set(snapshot.legal_actions):
        raise ArithmeticError('求解器动作与当前顺序输入不一致')


def _net_support_for_action(snapshot, action):
    """Allowed net outcomes for a one-bet pre-split action; None uses the two-hand contract."""
    if not snapshot.pre_split or action == 'split':
        return None
    if action == 'surrender':
        return {-0.5}
    if action == 'double':
        return {-2.0, 0.0, 2.0}
    if action in ('stand', 'hit'):
        hand = snapshot.hands[0]
        if action == 'stand' and is_natural_blackjack(hand.ranks) and not hand.from_split:
            return {0.0, 1.5}
        return {-1.0, 0.0, 1.0}
    raise ArithmeticError('当前动作没有已声明的收益支持')


def _validate_available_action(snapshot, action, item):
    ev = _finite_number(item.get('ev'), 'ev')
    dist = item.get('net_distribution')
    _validate_probability_map(dist, None, 'net_distribution')
    totals = {}
    for key, probability in dist.items():
        try:
            outcome = float(key)
        except (TypeError, ValueError) as error:
            raise ArithmeticError('净收益键无效') from error
        _finite_number(outcome, 'net_distribution.net')
        if not -2 <= outcome <= 2:
            raise ArithmeticError('净收益越界')
        totals[outcome] = totals.get(outcome, 0.0) + probability
    if abs(ev - sum(outcome * probability for outcome, probability in totals.items())) > 1e-10:
        raise ArithmeticError('合计EV与完整净收益分布不一致')
    allowed = _net_support_for_action(snapshot, action)
    if allowed is not None:
        if not set(totals).issubset(allowed):
            raise ArithmeticError('净收益格超出当前动作允许集合')
        return
    if set(totals) != TOTAL_NETS:
        raise ArithmeticError('需要完整的五个合计收益格')
    hand_evs = item.get('hand_evs')
    if not isinstance(hand_evs, (list, tuple)) or len(hand_evs) != 2:
        raise ArithmeticError('必须包含两手边际')
    hands = [_finite_number(value, 'hand_evs') for value in hand_evs]
    if abs(sum(hands) - ev) > 1e-10:
        raise ArithmeticError('两手边际EV与合计EV不一致')
    joint = item.get('joint_distribution')
    _validate_probability_map(joint, JOINT_KEYS, 'joint_distribution')
    for index in (0, 1):
        try:
            marginal = sum(int(pair.split(',')[index]) * probability for pair, probability in joint.items())
        except (TypeError, ValueError) as error:
            raise ArithmeticError('两手联合收益格不完整') from error
        if abs(marginal - hands[index]) > 1e-10:
            raise ArithmeticError('联合分布边际EV不匹配')
    for net, probability in dist.items():
        expected = sum(weight for pair, weight in joint.items()
                       if sum(map(int, pair.split(','))) == int(float(net)))
        if abs(probability - expected) > 1e-10:
            raise ArithmeticError('合计收益分布与两手联合分布不一致')


def _validate_split_probabilities(snapshot, probabilities):
    if not isinstance(probabilities, dict):
        raise ArithmeticError('概率结果必须为对象')
    draw_required = snapshot.pre_split or snapshot.active_index < 2
    if not draw_required:
        if any(key in probabilities for key in ('next_target_draw', 'hit_bust', 'dealer_terminal_if_stand_now')):
            raise ArithmeticError('两手已完成，目标抽牌指标不适用，不能填零冒充已算')
        return
    _validate_probability_map(probabilities.get('next_target_draw'), LABELS, 'next_target_draw')
    _validate_hit_bust(probabilities.get('hit_bust'))
    if snapshot.pre_split:
        _validate_probability_map(probabilities.get('dealer_terminal_if_stand_now'), DEALER_LABELS,
                                  'dealer_terminal_if_stand_now')
    elif 'dealer_terminal_if_stand_now' in probabilities:
        _validate_probability_map(probabilities['dealer_terminal_if_stand_now'], DEALER_LABELS,
                                  'dealer_terminal_if_stand_now')


def calculate_split(snapshot, request_id, budget_seconds):
    from .service import base_result
    result = base_result(snapshot, request_id)
    start = perf_counter()

    def remaining():
        value = budget_seconds - (perf_counter()-start)
        if value <= 0:
            raise CalculationStopped("TIMEOUT")
        return value

    try:
        snapshot.validate()
        if snapshot.engine_version != SPLIT_ENGINE or snapshot.strategy_version != SPLIT_STRATEGY:
            raise ValueError("分牌引擎/策略已升级，请按原事件前缀另建输入复算")
        if type(budget_seconds) not in (int, float) or not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 5:
            raise ValueError("计算预算必须大于0且不超过5秒")
        if snapshot.pre_split:
            numbers = solve_presplit_native(snapshot.counts, snapshot.hands[0].values, snapshot.dealer_up,
                snapshot.peek_negative, snapshot.legal_actions, remaining())
            _require_backend_actions(snapshot, numbers)
            for action,label in ACTION_ZH.items():
                if action in numbers['actions']:
                    item=dict(status=AVAILABLE,reason_code='CALCULATED',reason='已计算',**numbers['actions'][action])
                elif action in snapshot.uncertain_actions:
                    item=dict(status=PENDING,reason_code='LEGALITY_UNCERTAIN',reason='原始牌面未细分，动作合法性待核对')
                elif action in snapshot.legal_actions:
                    raise ArithmeticError('未完整计算分牌前所有合法动作')
                else:
                    item=dict(status=INAPPLICABLE,reason_code='NOT_LEGAL',reason='当前状态无此合法动作')
                result['actions'][action]=dict(label=label,**item)
            result['probabilities']=dict(next_target_draw=numbers['next_draw'],hit_bust=numbers['hit_bust'],
                                        dealer_terminal_if_stand_now=numbers['dealer_distribution'])
            result['probability_status']=dict(next_target_draw=AVAILABLE if any(a in snapshot.legal_actions for a in ('hit','double')) else INAPPLICABLE)
        else:
            active = snapshot.active_index
            numbers = solve_split_counts(snapshot.counts, tuple(h.values for h in snapshot.hands),
                snapshot.dealer_up, snapshot.peek_negative, active=active,
                split_aces=snapshot.hands[0].split_ace,
                force_active=active < 2 and snapshot.hands[active].forced_draw,
                budget_seconds=remaining())
            _require_backend_actions(snapshot, numbers)
            result['actions'] = {a: dict(status=AVAILABLE, label=SPLIT_ACTION_ZH[a], reason_code='CALCULATED',
                                        reason='当前行动手的合计净收益', **v) for a,v in numbers['actions'].items()}
            result['probabilities'] = {}
            if active < 2:
                model = FiniteModel(snapshot.dealer_up, snapshot.peek_negative, remaining())
                draw = model.target_draw(snapshot.counts)
                hand = snapshot.hands[active].values
                result['probabilities'] = dict(next_target_draw=dict(zip(LABELS,draw)),
                    hit_bust=sum(p for i,p in enumerate(draw) if total(sum(hand)+i+1,1 in hand or i==0)>21))
        for action,item in result['actions'].items():
            if item['status'] != AVAILABLE:
                continue
            _validate_available_action(snapshot, action, item)
            item['additional_investment'] = 1 if snapshot.pre_split and action in ('split','double') else 0
            item['total_investment'] = (1 if snapshot.pre_split else 2)+item['additional_investment']
        _validate_split_probabilities(snapshot, result['probabilities'])
        partial = bool(snapshot.uncertain_actions) or any(result['actions'][a]['status'] != AVAILABLE for a in snapshot.legal_actions)
        ordered = sorted(((v['ev'],a) for a,v in result['actions'].items() if v['status'] == AVAILABLE),reverse=True)
        highest = (ordered[0][1] if not partial and ordered and
                   (len(ordered) == 1 or ordered[0][0]-ordered[1][0] > 1e-10) else None)
        remaining()
        result.update(status=AVAILABLE, reason_code='PARTIAL_ACTION_COMPARISON' if partial else 'CALCULATED',
            reason='动作合法性尚未完整确认，仅作部分比较' if partial else '两手顺序研究模型下的原始投注合计净收益',
            highest_ev_action=highest, partial_comparison=partial,
            all_computed_ev_negative=bool(ordered) and ordered[0][0]<0,
            method='exact_finite_shared_shoe_float64', approximation=False, numerical_tolerance=1e-10,
            decision_tolerance=1e-12, current_investment=1 if snapshot.pre_split else 2,
            active_hand_id=snapshot.active_hand_id,
            **{k:numbers[k] for k in ('nodes','backend','backend_source_sha256','backend_binary_sha256','peak_memory','caches','information_bound_prunes') if k in numbers})
    except CalculationStopped as error:
        result.update(status=TIMEOUT, reason_code=str(error), reason='请求预算到期，分牌比较未完成；未发布部分搜索值')
    except InsufficientCards as error:
        result.update(status=UNSUPPORTED, reason_code='INSUFFICIENT_CARDS', reason=str(error))
    except Exception as error:
        result.update(status=FAILED, reason_code='SPLIT_CALCULATION_FAILED', reason=str(error))
    finally:
        if result['status'] != AVAILABLE:
            result.update(actions={}, probabilities=None, highest_ev_action=None)
        result['elapsed_seconds'] = perf_counter()-start
    return result
