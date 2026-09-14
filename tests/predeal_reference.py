"""Independent pre-deal reference: physical permutations, Fraction arithmetic.

No imports from production probability, strategy, or settlement modules.
Visible decisions use only player cards, dealer up, and the US peek result.
"""
from collections import defaultdict
from fractions import Fraction as F
from itertools import permutations

from tests.analysis_reference import reference


def expected(dist):
    return sum(k * p for k, p in dist.items())


def _unique_worlds(cards):
    return tuple(sorted(set(permutations(tuple(cards)))))


def _remaining(cards, p1, up, p2):
    left = list(cards)
    for value in (p1, up, p2):
        left.remove(value)
    return tuple(left)


def _pick(actions):
    best, best_ev = "stand", actions["stand"]["ev"]
    for name in ("hit", "double", "surrender"):
        if name in actions and actions[name]["ev"] > best_ev:
            best, best_ev = name, actions[name]["ev"]
    return best


def predeal_reference(cards, *, illegal_skip_dealer_bj=False):
    cards = tuple(cards)
    worlds = _unique_worlds(cards)
    if len(cards) < 4:
        raise ValueError("Need four cards to deal a round")
    grouped = defaultdict(list)
    for world in worlds:
        grouped[(world[0], world[1], world[2])].append(world)
    mixed = defaultdict(F)
    n_worlds = len(worlds)
    for (p1, up, p2), group in grouped.items():
        if illegal_skip_dealer_bj and up in (1, 10):
            group = [world for world in group if sorted((up, world[3])) != [1, 10]]
            if not group:
                continue
        weight = F(len(group), n_worlds)
        player = (p1, p2)
        natural = sorted(player) == [1, 10]
        if up in (1, 10) and not illegal_skip_dealer_bj:
            n_bj = sum(1 for world in group if sorted((up, world[3])) == [1, 10])
            p_bj = F(n_bj, len(group))
            if natural:
                mixed[F(0)] += weight * p_bj
                mixed[F(3, 2)] += weight * (1 - p_bj)
            else:
                mixed[F(-1)] += weight * p_bj
                if p_bj < 1:
                    inner = reference(_remaining(cards, p1, up, p2), player, up, True,
                                      actions=("stand", "hit", "double", "surrender"))
                    chosen = _pick(inner["actions"])
                    for payoff, probability in inner["actions"][chosen]["net_distribution"].items():
                        mixed[payoff] += probability * weight * (1 - p_bj)
            continue
        if natural:
            mixed[F(3, 2)] += weight
            continue
        inner = reference(_remaining(cards, p1, up, p2), player, up, up in (1, 10),
                          actions=("stand", "hit", "double", "surrender"))
        chosen = _pick(inner["actions"])
        for payoff, probability in inner["actions"][chosen]["net_distribution"].items():
            mixed[payoff] += probability * weight
    total = sum(mixed.values())
    if illegal_skip_dealer_bj:
        if total == 0:
            raise ValueError("Illegal skip removed every world")
        mixed = {key: value / total for key, value in mixed.items()}
    ev = expected(mixed)
    return {
        "net_distribution": dict(mixed),
        "ev": ev,
        "outcomes": {
            "win": sum(p for k, p in mixed.items() if k > 0),
            "push": sum(p for k, p in mixed.items() if k == 0),
            "lose": sum(p for k, p in mixed.items() if k < 0),
        },
        "worlds": n_worlds,
        "illegal_skip_dealer_bj": illegal_skip_dealer_bj,
    }
