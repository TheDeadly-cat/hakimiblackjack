"""BJ happens before actions; only the non-BJ branch executes a strategy."""
from copy import deepcopy
import math
from time import perf_counter, time

from .ace_prepeek_contracts import (AcePrepeekInput, ACE_PREPEEK_RESULT_SCHEMA,
                                   ACE_PREPEEK_ENGINE, ACE_PREPEEK_STAGE)
from .contracts import ACTION_ZH, AVAILABLE, FAILED, INAPPLICABLE, TIMEOUT, UNSUPPORTED, STATUS_ZH, digest
from .native_backend import solve_presplit_native
from .probability import CalculationStopped, InsufficientCards, LABELS, DEALER_LABELS, FiniteModel, total
from .split_service import _validate_available_action, _validate_probability_map, _validate_hit_bust

DRAW_CONDITION = 'dealer_not_blackjack_and_draw_is_executed'


def result_envelope(snapshot, request_id):
    snapshot.validate()
    return dict(schema=ACE_PREPEEK_RESULT_SCHEMA, request_id=request_id, input=snapshot.to_dict(),
        input_digest=snapshot.input_digest, rules_digest=snapshot.rules_digest,
        engine_version=snapshot.engine_version, strategy_version=snapshot.strategy_version,
        created_at=time(), status='computing', reason_code='COMPUTING', reason='庄家A检查前评估计算中',
        actions={}, probabilities=None, highest_ev_action=None, partial_comparison=False, elapsed_seconds=0.0,
        ev_unit='原始1单位主注的最终净收益；含检查时BJ分支，不含保险',
        evaluation_stage=ACE_PREPEEK_STAGE, dealer_bj_probability=snapshot.dealer_bj_probability,
        dealer_bj_fraction=str(snapshot.dealer_bj_fraction), current_investment=1,
        conditional_non_bj=None, conditional_draw=None, terminal_evaluation=None)


def _validate_conditional(snapshot, numbers):
    if not isinstance(numbers, dict) or not isinstance(numbers.get('actions'), dict):
        raise ArithmeticError('缺少完整非BJ条件分支')
    if set(numbers['actions']) != set(snapshot.kernel_actions):
        raise ArithmeticError('非BJ条件动作集合与原始起手策略不一致')
    for action, item in numbers['actions'].items():
        _validate_available_action(snapshot, action, item)
    _validate_probability_map(numbers.get('next_draw'), LABELS, 'conditional.next_draw')
    _validate_hit_bust(numbers.get('hit_bust'))
    expected_draw = FiniteModel(1, True).target_draw(snapshot.counts)
    if any(abs(numbers['next_draw'][rank] - probability) > 1e-10
           for rank, probability in zip(LABELS, expected_draw)):
        raise ArithmeticError('非BJ条件抽牌分布与原始底牌池不一致')
    expected_bust = math.fsum(probability for index, probability in enumerate(expected_draw)
        if total(sum(snapshot.player) + index + 1, 1 in snapshot.player or index == 0) > 21)
    if abs(numbers['hit_bust'] - expected_bust) > 1e-10:
        raise ArithmeticError('条件爆牌率与原始手牌和抽牌分布不一致')
    _validate_probability_map(numbers.get('dealer_distribution'), DEALER_LABELS, 'conditional.dealer_distribution')
    if numbers['dealer_distribution']['blackjack'] != 0:
        raise ArithmeticError('非BJ条件核仍包含庄家BJ')
    if snapshot.natural_blackjack:
        dist = {float(net): p for net, p in numbers['actions']['stand']['net_distribution'].items()}
        if abs(dist.get(0, 0)) > 1e-10 or abs(dist.get(1.5, 0) - 1) > 1e-10:
            raise ArithmeticError('天然BJ在非BJ分支必须净赢1.5原注')
    for name in ('backend_source_sha256', 'backend_binary_sha256'):
        if name in numbers and (not isinstance(numbers[name], str) or len(numbers[name]) != 64
                or any(c not in '0123456789abcdef' for c in numbers[name])):
            raise ArithmeticError('非BJ条件核来源摘要无效')


def _net_key(value):
    return str(int(value)) if value == int(value) else str(value)


def _mixture(distribution, p_bj, bj_net):
    values = {}
    for net, probability in distribution.items():
        values.setdefault(float(net), []).append((1 - p_bj) * probability)
    values.setdefault(float(bj_net), []).append(p_bj)
    mixed = {_net_key(net): math.fsum(parts) for net, parts in sorted(values.items())}
    expectation = math.fsum(float(net) * probability for net, probability in mixed.items())
    return mixed, expectation


def compose_ace_prepeek_result(snapshot, request_id, conditional_non_bj, *, elapsed_seconds=0.0):
    """Validate and compose a raw native conditional result without changing it."""
    result = result_envelope(snapshot, request_id)
    p = snapshot.dealer_bj_probability
    bj_net = 0 if snapshot.natural_blackjack else -1
    if p == 1:
        if conditional_non_bj is not None:
            raise ArithmeticError('概率为1的BJ不能伪造可达的非BJ分支')
    else:
        _validate_conditional(snapshot, conditional_non_bj)
    result.update(status=AVAILABLE, reason_code='ACE_PREPEEK_EVALUATION',
        reason='尚未记录庄家检查；主注EV和净盈利概率已含BJ先结算分支。补牌概率仅适用于非BJ后实际补牌。',
        elapsed_seconds=elapsed_seconds,
        branches=dict(dealer_bj=dict(probability=p, actions_executed=False,
                                    net_distribution={str(bj_net): 1.0}, investment=1),
                      non_bj=dict(probability=1 - p, actions_executed='only_if_strategy_is_chosen')),
        conditional_non_bj=deepcopy(conditional_non_bj),
        method='exact_finite_conditional_native_then_BJ_before_action_mixture',
        approximation=False, numerical_tolerance=1e-10)
    if p == 1:
        dealer = {key: float(key == 'blackjack') for key in DEALER_LABELS}
    else:
        dealer = {key: p if key == 'blackjack' else (1 - p) * value
                  for key, value in conditional_non_bj['dealer_distribution'].items()}
    result['probabilities'] = dict(dealer_natural_blackjack=p, dealer_terminal_if_stand_now=dealer)
    result['probability_status'] = dict(dealer_natural_blackjack=AVAILABLE,
        dealer_terminal_if_stand_now=AVAILABLE, next_target_draw=INAPPLICABLE, hit_bust=INAPPLICABLE)
    if snapshot.natural_blackjack or p == 1:
        native_dist = {} if p == 1 else conditional_non_bj['actions']['stand']['net_distribution']
        distribution, ev = _mixture(native_dist, p, bj_net)
        result['terminal_evaluation'] = dict(status=AVAILABLE,
            reason_code='PLAYER_NATURAL_BJ' if snapshot.natural_blackjack else 'DEALER_BJ_CERTAIN',
            ev=ev, net_distribution=distribution,
            net_profit_probability=math.fsum(v for net, v in distribution.items() if float(net) > 0),
            push_probability=math.fsum(v for net, v in distribution.items() if float(net) == 0),
            loss_probability=math.fsum(v for net, v in distribution.items() if float(net) < 0))
        result['conditional_draw'] = dict(status=INAPPLICABLE, condition=DRAW_CONDITION,
            condition_probability=1 - p, reason='天然BJ或BJ确定分支没有玩家补牌动作')
    else:
        for action, item in conditional_non_bj['actions'].items():
            distribution, ev = _mixture(item['net_distribution'], p, bj_net)
            additional = int(action in ('double', 'split'))
            result['actions'][action] = dict(label=ACTION_ZH[action], status=AVAILABLE,
                reason_code='CONTINGENT_STRATEGY', reason='仅在实际检查非BJ后执行此策略',
                ev=ev, net_distribution=distribution, ev_if_non_bj=item['ev'],
                additional_investment=additional, expected_additional_investment=(1 - p) * additional,
                max_final_investment=1 + additional)
        ordered = sorted(((v['ev'], a) for a, v in result['actions'].items()), reverse=True)
        result['highest_ev_action'] = (ordered[0][1] if ordered and
            (len(ordered) == 1 or ordered[0][0] - ordered[1][0] > 1e-10) else None)
        result['all_computed_ev_negative'] = bool(ordered) and ordered[0][0] < 0
        draw = deepcopy(conditional_non_bj['next_draw']); bust = conditional_non_bj['hit_bust']
        result['conditional_draw'] = dict(status=AVAILABLE, condition=DRAW_CONDITION,
            condition_probability=1 - p, next_target_draw=draw, hit_bust=bust)
        result['probabilities'].update(next_target_draw=deepcopy(draw), hit_bust=bust)
        result['probability_status'].update(next_target_draw=AVAILABLE, hit_bust=AVAILABLE)
    if conditional_non_bj:
        for name in ('nodes','backend','backend_source_sha256','backend_binary_sha256','peak_memory',
                     'caches','information_bound_prunes'):
            if name in conditional_non_bj:
                result[name] = deepcopy(conditional_non_bj[name])
    return result


def calculate_ace_prepeek(snapshot, request_id, budget_seconds=5.0):
    result = result_envelope(snapshot, request_id)
    start = perf_counter()
    try:
        if (type(budget_seconds) not in (int,float) or not math.isfinite(budget_seconds)
                or not 0 < budget_seconds <= 5):
            raise ValueError('检查前计算预算必须大于0且不超过5秒')
        numbers = None
        if snapshot.bj_numerator != snapshot.bj_denominator:
            remaining = budget_seconds - (perf_counter() - start)
            if remaining <= 0:
                raise CalculationStopped('TIMEOUT')
            # This flag is only the conditional numerical kernel. No input
            # identity, table, ledger event or observed peek fact is changed.
            numbers = solve_presplit_native(snapshot.counts, snapshot.player, 1, True,
                snapshot.kernel_actions, remaining, allow_das=False, both_initial=True)
        result = compose_ace_prepeek_result(snapshot, request_id, numbers,
                                           elapsed_seconds=perf_counter() - start)
        if perf_counter() - start >= budget_seconds:
            raise CalculationStopped('TIMEOUT')
    except CalculationStopped as error:
        result = result_envelope(snapshot, request_id)
        result.update(status=TIMEOUT, reason_code=str(error), reason='原计算预算到期；未发布部分分支')
    except InsufficientCards as error:
        result = result_envelope(snapshot, request_id)
        result.update(status=UNSUPPORTED, reason_code='INSUFFICIENT_CARDS', reason=str(error))
    except Exception as error:
        result = result_envelope(snapshot, request_id)
        result.update(status=FAILED, reason_code='ACE_PREPEEK_CALCULATION_FAILED', reason=str(error))
    result['elapsed_seconds'] = perf_counter() - start
    return result


def validate_ace_prepeek_result(result):
    """Reproduce every mixture field from the preserved conditional branch."""
    snapshot = AcePrepeekInput.from_dict(result['input'])
    if (result.get('schema') != ACE_PREPEEK_RESULT_SCHEMA or result.get('engine_version') != ACE_PREPEEK_ENGINE
            or result.get('strategy_version') != snapshot.strategy_version
            or result.get('evaluation_stage') != ACE_PREPEEK_STAGE
            or result.get('input_digest') != snapshot.input_digest or result.get('rules_digest') != snapshot.rules_digest
            or result.get('dealer_bj_probability') != snapshot.dealer_bj_probability
            or type(result.get('dealer_bj_probability')) not in (int,float)
            or result.get('dealer_bj_fraction') != str(snapshot.dealer_bj_fraction)
            or result.get('current_investment') != 1 or type(result.get('current_investment')) is not int
            or result.get('status') not in STATUS_ZH):
        raise ValueError('检查前结果的来源、事实、stage或BJ概率不一致')
    elapsed = result.get('elapsed_seconds')
    if type(elapsed) not in (int,float) or not math.isfinite(elapsed) or elapsed < 0:
        raise ValueError('检查前结果耗时无效')
    if result['status'] != AVAILABLE:
        if (result.get('actions') != {} or result.get('probabilities') is not None
                or result.get('conditional_non_bj') is not None or result.get('terminal_evaluation') is not None
                or result.get('conditional_draw') is not None or result.get('highest_ev_action') is not None):
            raise ValueError('未完成的检查前请求不能含旧值或部分分支')
        return
    expected = compose_ace_prepeek_result(snapshot, result.get('request_id'), result.get('conditional_non_bj'),
                                          elapsed_seconds=elapsed)
    # Request/creation timestamps are envelopes. All numerical, applicability,
    # branch and investment fields must match the independently rebuilt result.
    fields = set(expected) - {'created_at','request_id','input','elapsed_seconds'}
    if any(name not in result or digest(result[name]) != digest(expected[name]) for name in fields):
        raise ValueError('检查前完整分支、净收益分布、EV、概率或投入口径不一致')
    if any('joint_distribution' in item or 'hand_evs' in item for item in result['actions'].values()):
        raise ValueError('BJ时没有分牌两手，不能伪造无条件两手联合分布')
