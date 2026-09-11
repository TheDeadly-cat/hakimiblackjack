"""Compose original-hand alternatives and the exact shared-shoe total objective."""
from time import perf_counter
import math

from .contracts import AVAILABLE, INAPPLICABLE, PENDING, TIMEOUT, FAILED, UNSUPPORTED, ACTION_ZH
from .probability import CalculationStopped, InsufficientCards, FiniteModel, LABELS, total
from .split_actions import solve_split_counts
from .split_contracts import SPLIT_ENGINE, SPLIT_STRATEGY
from .native_backend import solve_presplit_native

SPLIT_ACTION_ZH = {**ACTION_ZH, "deal": "录入已确定要发的一张牌", "complete": "两手完成，等待庄家结算"}


def calculate_split(snapshot, request_id, budget_seconds):
    from .service import base_result, calculate, _validate_distribution
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
            if set(numbers['actions']) != set(snapshot.legal_actions):
                raise ArithmeticError("求解器动作与当前顺序输入不一致")
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
            _validate_distribution(item['net_distribution'])
            if abs(item['ev']-sum(float(v)*p for v,p in item['net_distribution'].items())) > 1e-10:
                raise ArithmeticError("合计EV与完整净收益分布不一致")
            if 'joint_distribution' in item:
                joint = item['joint_distribution']
                _validate_distribution(joint)
                if set(joint) != {f'{a},{b}' for a in (-1,0,1) for b in (-1,0,1)}:
                    raise ArithmeticError("两手联合收益格不完整")
                for v,p in item['net_distribution'].items():
                    if abs(p-sum(weight for pair,weight in joint.items() if sum(map(int,pair.split(','))) == int(v))) > 1e-10:
                        raise ArithmeticError("合计收益分布与两手联合分布不一致")
                if abs(item['ev']-sum(item['hand_evs'])) > 1e-10:
                    raise ArithmeticError("两手边际EV与合计EV不一致")
            item['additional_investment'] = 1 if snapshot.pre_split and action in ('split','double') else 0
            item['total_investment'] = (1 if snapshot.pre_split else 2)+item['additional_investment']
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
            **{k:numbers[k] for k in ('nodes','backend','backend_source_sha256','peak_memory','caches') if k in numbers})
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
