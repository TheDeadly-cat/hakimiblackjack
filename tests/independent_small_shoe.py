"""Independent Fraction/permutation oracles for small remaining packs.

Not an implementation of general blackjack. No production imports.
Distinct card positions are equally likely even where rank values repeat.
"""
from collections import defaultdict
from fractions import Fraction
from itertools import permutations
import json

FOUR_TENS = (10, 10, 10, 10)
ACE_THREE_TENS = (1, 10, 10, 10)


def _physical_worlds(pack):
    worlds = []
    for positions in permutations(range(len(pack))):
        worlds.append([pack[i] for i in positions])
    return worlds


def four_tens():
    """20 vs 20 after the initial deal; no extra card is required. Push."""
    worlds = _physical_worlds(FOUR_TENS)
    payoffs = []
    for cards in worlds:
        p1, up, p2, hole = cards
        assert sorted((p1, p2)) == [10, 10]
        assert up == 10 and hole == 10
        assert sorted((up, hole)) != [1, 10]
        payoffs.append(0)
    ev = Fraction(sum(payoffs), len(payoffs))
    return {
        "fixture": list(FOUR_TENS),
        "allow_surrender": False,
        "ev_fraction": str(ev),
        "ev": float(ev),
        "physical_permutations": len(worlds),
        "push": True,
        "needs_draw": False,
    }


def ace_three_tens():
    """Natural vs 20 pays +1.5; 20 vs dealer BJ pays -1. No extra draw."""
    worlds = _physical_worlds(ACE_THREE_TENS)
    payoffs = []
    for cards in worlds:
        p1, up, p2, hole = cards
        player = (p1, p2)
        natural = sorted(player) == [1, 10]
        dealer_bj = sorted((up, hole)) == [1, 10]
        if dealer_bj:
            payoffs.append(0 if natural else -1)
        elif natural:
            payoffs.append(Fraction(3, 2))
        else:
            dealer = up + hole
            player_total = p1 + p2
            assert dealer >= 17 and player_total <= 21
            payoffs.append(1 if player_total > dealer else (-1 if player_total < dealer else 0))
    ev = sum(payoffs, Fraction(0)) / len(payoffs)
    return {
        "fixture": list(ACE_THREE_TENS),
        "ev_fraction": str(ev),
        "ev": float(ev),
        "physical_permutations": len(worlds),
        "needs_draw": False,
    }


def five_five_vs_nine_three_tens():
    """Current-hand oracle: 5,5 vs 9, remaining three tens including the hole.

    Dealer 19 after the hole. Stand loses. Hitting a ten makes 20 and wins.
    Double is uniquely best. No production imports.
    """
    remaining = (10, 10, 10)
    stand = []
    hit = []
    double = []
    worlds = 0
    for hole_index in range(len(remaining)):
        hole = remaining[hole_index]
        undealt = remaining[:hole_index] + remaining[hole_index + 1:]
        dealer = 9 + hole
        assert dealer == 19
        worlds += 1
        stand.append(Fraction(-1))
        draw = undealt[0]
        assert draw == 10
        player_after = 10 + draw
        assert player_after == 20
        hit.append(Fraction(1))
        double.append(Fraction(2))
    n = Fraction(worlds)
    return {
        "player": [5, 5],
        "up": 9,
        "peek": False,
        "remaining_including_hole": list(remaining),
        "stand_ev": float(sum(stand, Fraction(0)) / n),
        "hit_ev": float(sum(hit, Fraction(0)) / n),
        "double_ev": float(sum(double, Fraction(0)) / n),
        "stand_ev_fraction": str(sum(stand, Fraction(0)) / n),
        "hit_ev_fraction": str(sum(hit, Fraction(0)) / n),
        "double_ev_fraction": str(sum(double, Fraction(0)) / n),
        "best": "double",
        "allow_surrender": False,
        "physical_holes": worlds,
    }


def ace_six_vs_two_three_tens():
    """Soft 17 vs 2 with three tens remaining, including the hole.

    Dealer 12 then 10 busts. Hitting a ten is hard 17, not 27. If the ace
    stayed 11, hit would bust; with conversion both stand and hit win.
    """
    remaining = (10, 10, 10)
    stand = []
    hit = []
    hit_if_ace_stays_eleven = []
    worlds = 0
    for hole_index in range(len(remaining)):
        hole = remaining[hole_index]
        undealt = remaining[:hole_index] + remaining[hole_index + 1:]
        dealer = 2 + hole
        assert dealer == 12
        worlds += 1
        dealer_after_stand = dealer + undealt[0]
        assert dealer_after_stand == 22
        stand.append(Fraction(1))
        draw = undealt[0]
        player_after = (1, 6, draw)
        hard = sum(player_after)
        aces = player_after.count(1)
        converted = hard + 10 if aces and hard + 10 <= 21 else hard
        eleven = 11 + 6 + draw
        assert converted == 17
        assert eleven == 27
        dealer_after_hit = dealer + undealt[1]
        assert dealer_after_hit == 22
        hit.append(Fraction(1) if dealer_after_hit > 21 or converted > dealer_after_hit else (
            Fraction(0) if converted == dealer_after_hit else Fraction(-1)))
        hit_if_ace_stays_eleven.append(Fraction(-1))
    n = Fraction(worlds)
    return {
        "player": [1, 6],
        "up": 2,
        "peek": False,
        "remaining_including_hole": list(remaining),
        "stand_ev": float(sum(stand, Fraction(0)) / n),
        "hit_ev": float(sum(hit, Fraction(0)) / n),
        "hit_if_ace_stays_eleven": float(sum(hit_if_ace_stays_eleven, Fraction(0)) / n),
        "stand_ev_fraction": str(sum(stand, Fraction(0)) / n),
        "hit_ev_fraction": str(sum(hit, Fraction(0)) / n),
        "soft_converts": True,
        "physical_holes": worlds,
    }


if __name__ == "__main__":
    tens = four_tens()
    ace = ace_three_tens()
    doubled = five_five_vs_nine_three_tens()
    soft = ace_six_vs_two_three_tens()
    assert tens["ev_fraction"] == "0"
    assert ace["ev_fraction"] == "1/4"
    assert doubled["double_ev_fraction"] == "2"
    assert doubled["hit_ev_fraction"] == "1"
    assert doubled["stand_ev_fraction"] == "-1"
    assert soft["stand_ev_fraction"] == "1"
    assert soft["hit_ev_fraction"] == "1"
    assert soft["hit_if_ace_stays_eleven"] == -1.0
    print(json.dumps({"four_tens": tens, "ace_three_tens": ace, "double_five_five": doubled,
                      "soft_ace_six": soft}, ensure_ascii=False, indent=2))
