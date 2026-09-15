"""Independent Fraction/permutation oracle for ONE artificial six-card fixture.

Not an implementation of general blackjack. No production imports.
The fixture contains no Ace and any player hit busts; thus compare stand and,
only when the rules permit, late surrender. Distinct card positions are equally
likely even where rank values repeat. All choices average over unknown holes.
"""
from collections import defaultdict
from fractions import Fraction
from itertools import permutations
import json

PACK = (10, 10, 9, 9, 8, 7)


def compute(allow_surrender: bool) -> dict:
    worlds = defaultdict(list)
    for positions in permutations(range(len(PACK))):
        cards = [PACK[i] for i in positions]
        p1, up, p2, hole = cards[:4]
        player_total = p1 + p2
        assert all(player_total + card > 21 for card in cards[4:])
        dealer_total = up + hole
        k = 4
        while dealer_total < 17:
            if k >= len(cards):
                raise ValueError("Fixture unexpectedly exhausts before settlement")
            dealer_total += cards[k]
            k += 1
        payoff = 1 if dealer_total > 21 or player_total > dealer_total else (
            -1 if player_total < dealer_total else 0)
        worlds[(p1, up, p2)].append(payoff)
    total_worlds = sum(map(len, worlds.values()))
    ev = Fraction(0)
    for payoffs in worlds.values():
        conditional = Fraction(sum(payoffs), len(payoffs))
        if allow_surrender:
            conditional = max(conditional, Fraction(-1, 2))
        ev += Fraction(len(payoffs), total_worlds) * conditional
    return {"allow_surrender": allow_surrender, "ev_fraction": str(ev), "ev": float(ev),
            "physical_permutations": total_worlds, "visible_states": len(worlds)}


if __name__ == "__main__":
    no = compute(False)
    yes = compute(True)
    assert no["ev_fraction"] == "0"
    assert yes["ev_fraction"] == "5/36"
    print(json.dumps({"fixture": PACK, "scope": "artificial six cards, not six decks",
                      "no_surrender": no, "late_surrender": yes}, ensure_ascii=False, indent=2))
