"""Single unsplit hand, exact finite enumeration in double-precision arithmetic.

After HIT, continuation chooses HIT or STAND at each visible-information state.
The choice is made after marginalizing the hole card, never separately per hole.
"""
from functools import lru_cache
from time import perf_counter

from .probability import FiniteModel, remove, total, LABELS, DEALER_LABELS


def expectation(distribution):
    return distribution[2] - distribution[0]


def solve_counts(counts, player, dealer_up, peek_negative, actions=("stand", "hit", "double"),
                 budget_seconds=5.0, cancelled=None):
    counts, player = tuple(counts), tuple(player)
    if len(counts) != 10 or any(type(n) is not int or n < 0 for n in counts):
        raise ValueError("需要十个非负整数点值桶")
    if any(type(v) is not int or not 1 <= v <= 10 for v in (*player, dealer_up)) or len(player) < 2:
        raise ValueError("非法玩家或庄家牌面")
    if type(peek_negative) is not bool:
        raise ValueError("检查状态必须明确")
    start = perf_counter()
    model = FiniteModel(dealer_up, peek_negative, budget_seconds, cancelled)
    hard, ace = sum(player), 1 in player
    if total(hard, ace) > 21:
        raise ValueError("已爆牌的手牌没有决策")

    @lru_cache(maxsize=50_000)
    def stand(c, h, a):
        model.check()
        score = total(h, a)
        if score > 21:
            return (1.0, 0.0, 0.0)
        dealer = model.dealer_distribution(c)
        lose, push, win = dealer[0], 0.0, dealer[6]
        for i in range(1, 6):
            if i + 16 > score:
                lose += dealer[i]
            elif i + 16 == score:
                push += dealer[i]
            else:
                win += dealer[i]
        return lose, push, win

    @lru_cache(maxsize=50_000)
    def continuation(c, h, a):
        model.check()
        if total(h, a) > 21:
            return (1.0, 0.0, 0.0)
        standing = stand(c, h, a)
        if total(h, a) == 21 or sum(c) < 2:
            return standing
        hitting = hit(c, h, a, False)
        # This comparison happens in the common information set across holes.
        return hitting if expectation(hitting) > expectation(standing) else standing

    def hit(c, h, a, one_card):
        result = [0.0, 0.0, 0.0]
        for i, probability in enumerate(model.target_draw(c)):
            if not probability:
                continue
            next_hard, next_ace = h + i + 1, a or i == 0
            if total(next_hard, next_ace) > 21:
                result[0] += probability
                continue
            child = (stand if one_card else continuation)(remove(c, i), next_hard, next_ace)
            for j, p in enumerate(child):
                result[j] += probability * p
        return tuple(result)

    probabilities = model.target_draw(counts)
    bust = sum(p for i, p in enumerate(probabilities) if total(hard + i + 1, ace or i == 0) > 21)
    dealer = model.dealer_distribution(counts)
    outcomes = {}
    natural = len(player) == 2 and sorted(player) == [1, 10]
    for action in actions:
        if action == "stand":
            if natural:
                outcomes[action] = {"0": dealer[0], "1.5": 1.0 - dealer[0]}
            else:
                outcomes[action] = dict(zip(("-1", "0", "1"), stand(counts, hard, ace)))
        elif action in ("hit", "double"):
            if natural or total(hard, ace) == 21:
                continue
            if action == "double" and len(player) != 2:
                continue
            distribution = hit(counts, hard, ace, action == "double")
            outcomes[action] = dict(zip(("-2", "0", "2") if action == "double" else ("-1", "0", "1"), distribution))
        elif action == "surrender":
            outcomes[action] = {"-0.5": 1.0}
    return {
        "next_draw": dict(zip(LABELS, probabilities)), "hit_bust": bust,
        "dealer_distribution": dict(zip(DEALER_LABELS, dealer)),
        "actions": {action: {"ev": sum(float(k) * p for k, p in dist.items()),
                             "net_distribution": dist} for action, dist in outcomes.items()},
        "method": "exact_finite_enumeration_float64", "approximation": "仅IEEE754双精度舍入；无抽样或截断",
        "elapsed_seconds": perf_counter() - start, "nodes": model.nodes,
        "strategy": "after-hit-visible-composition-optimal-hit-stand-v1",
        "ev_unit": "相对原始1单位初始注的最终净收益（不含返还本金）",
    }
