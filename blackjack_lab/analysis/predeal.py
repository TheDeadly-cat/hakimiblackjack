"""Exact small-shoe pre-deal EV. Strategy never sees the hole except via US peek.

Visible initial cards are enumerated without replacement. Dealer blackjack on
Ace/Ten ups is a real branch; it is never assumed already peeked away.
After a negative peek or a non-Ace/Ten up, play uses the existing unsplit
solver and picks stand / hit / double / late surrender by visible EV.
"""
from __future__ import annotations

from time import perf_counter, time
import math
import uuid

from .actions import solve_counts
from .contracts import AVAILABLE, FAILED, TIMEOUT, UNSUPPORTED
from .predeal_contracts import (
    PREDEAL_ENGINE_VERSION, PREDEAL_RESULT_SCHEMA, PREDEAL_STRATEGY_VERSION,
)
from .probability import CalculationStopped, InsufficientCards, remove
from .research_windows import WINDOW_PRE_DEAL, net_ev, net_outcome_summary

ACTION_ORDER = ("stand", "hit", "double", "surrender")


def _net_key(value):
    number = float(value)
    if abs(number - round(number)) < 1e-12:
        return str(int(round(number)))
    return f"{number:.12g}"


def _mix(acc, dist, weight):
    if not weight:
        return
    for key, probability in dist.items():
        name = _net_key(key)
        acc[name] = acc.get(name, 0.0) + float(probability) * weight


def _best_play(counts, player, up, peek, budget, cancelled):
    actions = ("stand", "hit", "double", "surrender")
    solved = solve_counts(counts, player, up, peek, actions=actions,
                          budget_seconds=budget, cancelled=cancelled)
    best_name, best_ev, best_dist = None, None, None
    for name in ACTION_ORDER:
        item = solved["actions"].get(name)
        if not item:
            continue
        ev = item["ev"]
        if best_name is None or ev > best_ev + 1e-10:
            best_name, best_ev, best_dist = name, ev, item["net_distribution"]
    if best_dist is None:
        raise ValueError("可见信息下没有可执行动作")
    return best_dist, best_name, solved["nodes"]


def solve_predeal_counts(counts, budget_seconds=5.0, cancelled=None, *,
                         illegal_skip_dealer_bj=False):
    """Return net distribution for the next round under π. counts include no hole yet."""
    counts = tuple(counts)
    remaining = sum(counts)
    if remaining < 4:
        raise InsufficientCards("剩余牌不足下一轮初始四张")
    start = perf_counter()
    deadline = start + budget_seconds
    mixed = {}
    nodes = 0
    n0 = remaining
    for i in range(10):
        if not counts[i]:
            continue
        p1 = counts[i] / n0
        after_p1 = remove(counts, i)
        n1 = n0 - 1
        for up_i in range(10):
            if not after_p1[up_i]:
                continue
            p_up = after_p1[up_i] / n1
            after_up = remove(after_p1, up_i)
            n2 = n1 - 1
            for j in range(10):
                if not after_up[j]:
                    continue
                if cancelled is not None and cancelled():
                    raise CalculationStopped("CANCELLED")
                if perf_counter() >= deadline:
                    raise CalculationStopped("TIMEOUT")
                p2 = after_up[j] / n2
                visible = p1 * p_up * p2
                solver_counts = remove(after_up, j)
                player = (i + 1, j + 1)
                up = up_i + 1
                natural = sorted(player) == [1, 10]
                hole_mass = sum(solver_counts)
                if hole_mass <= 0:
                    raise InsufficientCards("发出三张可见牌后没有底牌候选")
                left = max(0.05, deadline - perf_counter())
                if up in (1, 10):
                    complete = 9 if up == 1 else 0
                    p_bj = solver_counts[complete] / hole_mass
                    if illegal_skip_dealer_bj:
                        play, _, used = _best_play(solver_counts, player, up, True, left, cancelled)
                        nodes += used
                        _mix(mixed, play if not natural else {"1.5": 1.0}, visible)
                        continue
                    if natural:
                        _mix(mixed, {"0": 1.0}, visible * p_bj)
                        _mix(mixed, {"1.5": 1.0}, visible * (1.0 - p_bj))
                    else:
                        _mix(mixed, {"-1": 1.0}, visible * p_bj)
                        if 1.0 - p_bj > 0:
                            play, _, used = _best_play(solver_counts, player, up, True, left, cancelled)
                            nodes += used
                            _mix(mixed, play, visible * (1.0 - p_bj))
                elif natural:
                    _mix(mixed, {"1.5": 1.0}, visible)
                else:
                    play, _, used = _best_play(solver_counts, player, up, False, left, cancelled)
                    nodes += used
                    _mix(mixed, play, visible)
    total = sum(mixed.values())
    if abs(total - 1.0) > 1e-8:
        raise ArithmeticError("发牌前结果概率未归一")
    ev = net_ev(mixed)
    second = sum(float(key) ** 2 * p for key, p in mixed.items())
    return {
        "net_distribution": mixed,
        "ev": ev,
        "variance": second - ev * ev,
        "outcomes": net_outcome_summary(mixed),
        "nodes": nodes,
        "elapsed_seconds": perf_counter() - start,
        "illegal_skip_dealer_bj": illegal_skip_dealer_bj,
        "method": "exact_visible_deal_enumeration_then_unsplit_solver",
        "approximation": "仅IEEE754双精度舍入；无抽样或截断；策略不读暗牌与后续顺序",
    }


def calculate_predeal(snapshot, request_id=None, budget_seconds=5.0):
    request_id = request_id or uuid.uuid4().hex
    if not math.isfinite(budget_seconds) or not 0 < budget_seconds <= 5.0:
        raise ValueError("计算预算必须在0到5秒之间")
    result = {
        "schema": PREDEAL_RESULT_SCHEMA,
        "request_id": request_id,
        "window": WINDOW_PRE_DEAL,
        "input": snapshot.to_dict(),
        "input_digest": snapshot.input_digest,
        "rules_digest": snapshot.rules_digest,
        "engine_version": snapshot.engine_version,
        "strategy_version": snapshot.strategy_version,
        "created_at": time(),
        "status": "computing",
        "reason_code": "COMPUTING",
        "reason": "计算中",
        "ev": None,
        "variance": None,
        "net_distribution": {},
        "outcomes": None,
        "probabilities": None,
        "actions": {},
        "highest_ev_action": None,
        "partial_comparison": False,
        "elapsed_seconds": 0.0,
        "ev_unit": "相对原始1单位初始注的下一轮最终净收益",
        "numerical_uncertainty": "IEEE754 float64；小牌靴可见发牌穷举",
        "model_uncertainty": "策略不分牌、不买保险；庄家A/十点明牌保留BJ分支",
    }
    start = perf_counter()
    try:
        snapshot.validate()
        if snapshot.engine_version != PREDEAL_ENGINE_VERSION:
            raise ValueError("发牌前引擎版本不匹配")
        if snapshot.strategy_version != PREDEAL_STRATEGY_VERSION:
            raise ValueError("发牌前策略版本不匹配")
        numbers = solve_predeal_counts(snapshot.counts, budget_seconds=budget_seconds)
        outcomes = numbers["outcomes"]
        result.update(
            status=AVAILABLE, reason_code="CALCULATED",
            reason="发牌前开局优势：对下一轮所有初始发牌求期望，已计入庄家BJ",
            ev=numbers["ev"], variance=numbers["variance"],
            net_distribution=numbers["net_distribution"], outcomes=outcomes,
            probabilities={"win": outcomes["win"], "push": outcomes["push"], "lose": outcomes["lose"]},
            method=numbers["method"], approximation=numbers["approximation"],
            numerical_tolerance=1e-10, nodes=numbers["nodes"],
        )
    except CalculationStopped as error:
        result.update(status=TIMEOUT, reason_code=str(error), reason="预算到期，发牌前请求未完成；未使用当前手牌结果")
    except InsufficientCards as error:
        result.update(status=UNSUPPORTED, reason_code="INSUFFICIENT_CARDS", reason=str(error))
    except Exception as error:
        result.update(status=FAILED, reason_code="CALCULATION_FAILED", reason=str(error))
    result["elapsed_seconds"] = perf_counter() - start
    return result
