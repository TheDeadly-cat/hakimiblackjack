"""Independent two-hand DAS Fraction oracle. No production strategy or settlement."""
from collections import Counter
from fractions import Fraction
from functools import lru_cache

from tests.das_contract import can_das, investment_view

JOINT_NETS = tuple((a, b) for a in range(-2, 3) for b in range(-2, 3))
ZERO = (Fraction(0),) * len(JOINT_NETS)


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
    return sum((a + b) * p for (a, b), p in zip(JOINT_NETS, dist))


def hand_evs(dist):
    return (
        sum(a * p for (a, b), p in zip(JOINT_NETS, dist)),
        sum(b * p for (a, b), p in zip(JOINT_NETS, dist)),
    )


def net_distribution(dist):
    totals = {}
    for (a, b), p in zip(JOINT_NETS, dist):
        totals[a + b] = totals.get(a + b, Fraction(0)) + p
    return totals


def pair_payoff(scores, stakes, dealer, dealer_natural):
    pair = []
    dealer_score = points(dealer)
    for score, stake in zip(scores, stakes):
        if score > 21:
            pair.append(-stake)
        elif dealer_natural:
            pair.append(-stake)
        elif dealer_score > 21 or score > dealer_score:
            pair.append(stake)
        elif score == dealer_score:
            pair.append(0)
        else:
            pair.append(-stake)
    return tuple(pair)


def remaining_das_hands(index, hands, stakes, split_aces, allow_das, action):
    """Upper bound on extra DAS units after this action, not a proved option set.

    stand/hit forgo this hand's DAS. double spends it. A 1-card deal keeps this
    hand in the bound. A 2-card deal (hit pending or DAS unique card) does not;
    the current hand is no longer eligible after that card arrives.
    """
    if not allow_das or split_aces:
        return 0
    later = 0
    for i in range(index + 1, 2):
        if stakes[i] == 1:
            later += 1
    if action in ("stand", "hit", "double"):
        return later
    if action == "deal" and index < 2 and len(hands[index]) >= 2:
        return later
    if index < 2 and stakes[index] == 1:
        hand = hands[index]
        if len(hand) < 2:
            return later + 1
        if can_das(split_aces, hand, False):
            return later + 1
    return later


def das_split_reference(cards, hands, up, peek=True, active=0, split_aces=False,
                        stakes=(1, 1), awaiting=None, allow_das=True):
    """Visible-prefix DAS reference. Does not inspect un-dealt second-hand cards."""
    if active == 0 and len(hands) == 2 and len(hands[1]) > 1:
        raise ValueError("不得包含第二手尚未轮到的未来牌")
    excluded = 10 if peek and up == 1 else 1 if peek and up == 10 else None
    possible = tuple(w for w in worlds(tuple(cards)) if w[0] != excluded)
    if not possible:
        raise ValueError("No conditional physical worlds")
    hands = tuple(tuple(h) for h in hands)
    stakes = tuple(stakes)
    if awaiting not in (None, "deal", "double"):
        raise ValueError("unknown awaiting state")

    @lru_cache(None)
    def matching(prefix):
        return tuple(w for w in possible if w[1:1 + len(prefix)] == prefix)

    def terminal(prefix, current, current_stakes):
        selected = matching(prefix)
        result = list(ZERO)
        for world in selected:
            scores = tuple(points(h) for h in current)
            dealer = (up, world[0])
            natural = sorted(dealer) == [1, 10]
            index = 1 + len(prefix)
            if not all(s > 21 for s in scores) and not natural:
                while points(dealer) < 17:
                    if index >= len(world):
                        raise ValueError("Dealer needs a missing physical card")
                    dealer += (world[index],)
                    index += 1
            pair = pair_payoff(scores, current_stakes, dealer, natural)
            result[JOINT_NETS.index(pair)] += Fraction(1, len(selected))
        return tuple(result)

    def take(prefix, current, index, current_stakes, then):
        selected = matching(prefix)
        if 1 + len(prefix) >= len(cards):
            raise ValueError("Player needs a missing physical card")
        frequencies = Counter(w[1 + len(prefix)] for w in selected)
        result = list(ZERO)
        for value, count in frequencies.items():
            updated = list(current)
            updated[index] += (value,)
            child = then(prefix + (value,), tuple(updated), current_stakes)
            weight = Fraction(count, len(selected))
            for j, p in enumerate(child):
                result[j] += weight * p
        return tuple(result)

    @lru_cache(None)
    def best(prefix, current, index, current_stakes):
        if index == 2:
            return terminal(prefix, current, current_stakes)
        hand = current[index]
        if len(hand) == 1:
            return take(prefix, current, index, current_stakes,
                        lambda p, c, s: best(p, c, index, s))
        if split_aces or points(hand) >= 21:
            return best(prefix, current, index + 1, current_stakes)
        standing = best(prefix, current, index + 1, current_stakes)
        if 1 + len(prefix) >= len(cards):
            return standing
        hitting = take(prefix, current, index, current_stakes,
                       lambda p, c, s: best(p, c, index, s))
        options = [("stand", standing), ("hit", hitting)]
        if allow_das and can_das(split_aces, hand, current_stakes[index] == 2):
            doubled = list(current_stakes)
            doubled[index] = 2

            def after_double(p, c, s):
                return best(p, c, index + 1, tuple(doubled))

            options.append(("double", take(prefix, current, index, current_stakes, after_double)))
        ranked = max(options, key=lambda item: expected(item[1]))
        return ranked[1]

    def package(name, dist, action):
        later = remaining_das_hands(active, hands, stakes, split_aces, allow_das, action)
        money = investment_view(stakes, action, later)
        joint = {pair: dist[JOINT_NETS.index(pair)] for pair in JOINT_NETS if dist[JOINT_NETS.index(pair)]}
        if not joint:
            joint = {pair: dist[i] for i, pair in enumerate(JOINT_NETS)}
        return {
            "ev": expected(dist),
            "joint_distribution": joint,
            "net_distribution": net_distribution(dist),
            "hand_evs": list(hand_evs(dist)),
            "stakes": stakes,
            **money,
        }

    if awaiting == "double":
        if stakes[active] != 2:
            raise ValueError("加倍待牌时该手注额必须已为2")
        doubled = take((), hands, active, stakes, lambda p, c, s: best(p, c, active + 1, s))
        return {"deal": package("deal", doubled, "deal")}
    if active == 2:
        return {"complete": package("complete", best((), hands, 2, stakes), "complete")}
    if awaiting == "deal" or len(hands[active]) == 1:
        dealt = take((), hands, active, stakes, lambda p, c, s: best(p, c, active, s))
        return {"deal": package("deal", dealt, "deal")}
    actions = {"stand": best((), hands, active + 1, stakes)}
    hand = hands[active]
    if not split_aces and points(hand) < 21:
        actions["hit"] = take((), hands, active, stakes, lambda p, c, s: best(p, c, active, s))
        if allow_das and can_das(split_aces, hand, stakes[active] == 2):
            doubled_stakes = list(stakes)
            doubled_stakes[active] = 2
            actions["double"] = take(
                (), hands, active, stakes,
                lambda p, c, s: best(p, c, active + 1, tuple(doubled_stakes)))
    return {name: package(name, dist, name) for name, dist in actions.items()}
