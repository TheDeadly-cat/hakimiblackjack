"""Independent Fraction oracle: labeled-world multiplicities, visible-prefix decisions.

Each unique rank permutation represents the same product(count!) labeled-card
permutations. Filtering the hole and grouping only observed draws preserves
their exact conditional weights. No production probability/strategy code is used.
"""
from collections import Counter
from fractions import Fraction
from functools import lru_cache

PAYOFFS = tuple((a, b) for a in (-1, 0, 1) for b in (-1, 0, 1))
ZERO = (Fraction(0),) * 9


def points(cards):
    value = sum(11 if c == 1 else c for c in cards)
    aces = cards.count(1)
    while value > 21 and aces:
        value -= 10
        aces -= 1
    return value


def worlds(cards):
    counts = Counter(cards)
    def visit(prefix):
        if len(prefix) == len(cards):
            yield prefix
            return
        for value in sorted(counts):
            if counts[value]:
                counts[value] -= 1
                yield from visit(prefix + (value,))
                counts[value] += 1
    return tuple(visit(()))


def expected(dist):
    return sum((a+b)*p for (a,b),p in zip(PAYOFFS, dist))


def split_reference(cards, hands, up, peek=True, active=0, split_aces=False):
    excluded = 10 if peek and up == 1 else 1 if peek and up == 10 else None
    possible = tuple(w for w in worlds(tuple(cards)) if w[0] != excluded)
    if not possible:
        raise ValueError("No conditional physical worlds")
    hands = tuple(tuple(h) for h in hands)

    @lru_cache(None)
    def matching(prefix):
        return tuple(w for w in possible if w[1:1+len(prefix)] == prefix)

    def terminal(prefix, current):
        selected = matching(prefix)
        result = list(ZERO)
        for world in selected:
            scores = tuple(points(h) for h in current)
            if all(s > 21 for s in scores):
                pair = (-1, -1)
            else:
                dealer = (up, world[0])
                index = 1 + len(prefix)
                natural = sorted(dealer) == [1, 10]
                while points(dealer) < 17:
                    if index >= len(world):
                        raise ValueError("Dealer needs a missing physical card")
                    dealer += (world[index],)
                    index += 1
                d = points(dealer)
                pair = tuple(-1 if score > 21 or natural else
                             1 if d > 21 or score > d else 0 if score == d else -1 for score in scores)
            result[PAYOFFS.index(pair)] += Fraction(1, len(selected))
        return tuple(result)

    def draw(prefix, current, index):
        selected = matching(prefix)
        if 1+len(prefix) >= len(cards):
            raise ValueError("Player needs a missing physical card")
        frequencies = Counter(w[1+len(prefix)] for w in selected)
        result = list(ZERO)
        for value, count in frequencies.items():
            updated = list(current)
            updated[index] += (value,)
            child = best(prefix+(value,), tuple(updated), index)
            weight = Fraction(count, len(selected))
            for j, p in enumerate(child):
                result[j] += weight*p
        return tuple(result)

    @lru_cache(None)
    def best(prefix, current, index):
        if index == 2:
            return terminal(prefix, current)
        hand = current[index]
        if len(hand) == 1:
            return draw(prefix, current, index)
        standing = best(prefix, current, index+1)
        if split_aces or points(hand) >= 21:
            return standing
        if 1+len(prefix) >= len(cards):
            return standing
        hitting = draw(prefix, current, index)
        safe_tie = len(cards)-len(prefix)>=64 and sum(hand)<=11 and points(hand)<17
        return hitting if (expected(hitting)>expected(standing) or
                           safe_tie and expected(hitting)==expected(standing)) else standing

    if active == 2:
        actions = {"complete": best((), hands, 2)}
    elif len(hands[active]) == 1:
        actions = {"deal": best((), hands, active)}
    else:
        actions = {"stand": best((), hands, active+1)}
        if not split_aces and points(hands[active]) < 21:
            actions["hit"] = draw((), hands, active)
    return {name: {"ev": expected(dist), "joint_distribution": dict(zip(PAYOFFS, dist)),
                   "net_distribution": {v: sum(p for (a,b),p in zip(PAYOFFS,dist) if a+b==v) for v in range(-2,3)}}
            for name, dist in actions.items()}
