"""Independent small-shoe reference: physical permutations and information sets.

No imports from production cards, probability, strategy, or settlement modules.
All arithmetic is Fraction. Worlds differ in actual hole and future order, but
decisions group worlds by the same visible prefix BEFORE comparing actions.
"""
from collections import defaultdict
from fractions import Fraction as F
from functools import lru_cache
from itertools import permutations


def points(cards):
    # Different representation: enumerate all possible ace elevations.
    base = sum(cards)
    possibilities = [base + 10 * n for n in range(cards.count(1) + 1)]
    valid = [v for v in possibilities if v <= 21]
    return max(valid) if valid else min(possibilities)


def dealer_end(up, world, drawn):
    cards = (up, world[0])
    if sorted(cards) == [1, 10]:
        return "blackjack"
    index = 1 + drawn
    while points(cards) < 17:
        if index == len(world):
            raise ValueError("Reference world runs out of dealer cards")
        cards += (world[index],)
        index += 1
    return "bust" if points(cards) > 21 else str(points(cards))


def net(player, end, stake=1):
    score = points(player)
    if score > 21:
        return F(-stake)
    natural = len(player) == 2 and sorted(player) == [1, 10]
    if end == "blackjack":
        return F(0 if natural else -stake)
    if natural:
        return F(3, 2)
    if end == "bust":
        return F(stake)
    dealer = int(end)
    return F(stake * ((score > dealer) - (score < dealer)))


def reference(cards, player, up, peek=False, actions=("stand", "hit", "double", "surrender")):
    # Equal-valued permutations have identical multiplicity, hence uniform worlds.
    worlds = tuple(sorted(set(permutations(cards))))
    excluded = 10 if up == 1 else 1 if up == 10 else None
    if peek:
        worlds = tuple(w for w in worlds if w[0] != excluded)
    if not worlds:
        raise ValueError("Impossible peek")

    def average(entries):
        out = defaultdict(F)
        for dist, weight in entries:
            for outcome, probability in dist.items():
                out[outcome] += probability * weight
        return dict(out)

    def expected(dist):
        return sum(k * p for k, p in dist.items())

    @lru_cache(None)
    def stand(ws, hand, depth, stake=1):
        out = defaultdict(F)
        for w in ws:
            out[net(hand, dealer_end(up, w, depth), stake)] += F(1, len(ws))
        return dict(out)

    @lru_cache(None)
    def hit(ws, hand, depth, double=False):
        groups = defaultdict(list)
        for w in ws:
            if depth + 1 >= len(w):
                raise ValueError("Reference world runs out of player cards")
            groups[w[depth + 1]].append(w)
        entries = []
        for value, group in groups.items():
            group = tuple(group)
            next_hand = hand + (value,)
            if points(next_hand) > 21:
                dist = {F(-2 if double else -1): F(1)}
            elif double:
                dist = stand(group, next_hand, depth + 1, 2)
            else:
                dist = optimal(group, next_hand, depth + 1)
            entries.append((dist, F(len(group), len(ws))))
        return average(entries)

    @lru_cache(None)
    def optimal(ws, hand, depth):
        staying = stand(ws, hand, depth)
        if points(hand) == 21:
            return staying
        drawing = hit(ws, hand, depth)
        return drawing if expected(drawing) > expected(staying) else staying

    draw = defaultdict(F)
    dealer = defaultdict(F)
    bust = F(0)
    for w in worlds:
        weight = F(1, len(worlds))
        draw[w[1]] += weight
        dealer[dealer_end(up, w, 0)] += weight
        if points(tuple(player) + (w[1],)) > 21:
            bust += weight
    results = {}
    for action in actions:
        if action == "stand":
            dist = stand(worlds, tuple(player), 0)
        elif action == "hit":
            dist = hit(worlds, tuple(player), 0)
        elif action == "double":
            dist = hit(worlds, tuple(player), 0, True)
        elif action == "surrender":
            dist = {F(-1, 2): F(1)}
        results[action] = {"ev": expected(dist), "net_distribution": dist}
    # Deliberately illegal benchmark: observe the actual hole before continuation.
    if "hit" in actions:
        per_hole = defaultdict(list)
        for w in worlds:
            per_hole[w[0]].append(w)
        oracle = sum(F(len(group), len(worlds)) * expected(hit(tuple(group), tuple(player), 0))
                     for group in per_hole.values())
    else:
        oracle = None
    return {"next_draw": dict(draw), "hit_bust": bust, "dealer_distribution": dict(dealer),
            "actions": results, "illegal_hole_oracle_hit_ev": oracle, "worlds": len(worlds)}
