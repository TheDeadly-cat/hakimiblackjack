"""Full-shoe / late-shoe pre-deal window study. Not a second EV engine.

The simulator may know the shuffle. Strategy π sees only public remaining
composition, the visible initial cards, and the US peek result. Dealer
blackjack is a real branch. Remaining >16 is recorded as unsupported, not
retitled current-hand EV. Timeouts stay timeouts. Zero-window shoes are valid.
"""
from __future__ import annotations

from random import Random

from .actions import solve_counts
from .contracts import UNSUPPORTED, TIMEOUT, AVAILABLE, FAILED
from .predeal import solve_predeal_counts
from .predeal_contracts import PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION
from .probability import CalculationStopped, InsufficientCards
from .research_windows import WINDOW_PRE_DEAL, counts_from_values
from ..core.cards import hand_total

SCHEMA = "hakimi-shoe-window-study-v1"
KIND_LATE_DEPLETE = "late-deplete"
KIND_LATE_RESHUFFLE = "late-reshuffle-control"
KIND_FULL_RESHUFFLE = "full-reshuffle-control"
KIND_FULL_DEPLETE = "full-deplete-stand-then-exact"
CONSUMPTION_STAND = "always-stand-v1"
CONSUMPTION_PI = PREDEAL_STRATEGY_VERSION
CONSUMPTION_BASIC = "basic-s17-unsplit-no-double-v1"
ACTION_ORDER = ("stand", "hit", "double", "surrender")


def basic_unsplit_action(player, up):
    """Toy hard-total chart for consumption contrast; not a published basic-strategy chart."""
    total = _score(player)
    if total >= 17:
        return "stand"
    if total <= 11:
        return "hit"
    if up in (2, 3, 4, 5, 6):
        return "stand"
    return "hit"


def _rank(value):
    return "A" if int(value) == 1 else str(int(value))


def _score(values):
    total, _soft = hand_total([_rank(v) for v in values])
    if total is None:
        raise ValueError("点数不可知")
    return total


def _dealer_stands(cards):
    total, _soft = hand_total([_rank(v) for v in cards])
    return total is not None and total >= 17


def full_pack(n_decks):
    if n_decks not in (6, 7, 8):
        raise ValueError("整靴研究只接受 6/7/8 副")
    pack = []
    for value in range(1, 10):
        pack.extend([value] * (4 * n_decks))
    pack.extend([10] * (16 * n_decks))
    return pack


def sample_pack(n_decks, remaining, rng):
    if remaining < 4:
        raise ValueError("采样剩余必须至少 4 张")
    pack = full_pack(n_decks)
    if remaining > len(pack):
        raise ValueError("采样张数超过整靴")
    return rng.sample(pack, remaining)


def choose_action(counts, player, up, peek, actions=None, budget_seconds=2.0):
    actions = tuple(actions or ACTION_ORDER)
    solved = solve_counts(counts, tuple(player), up, peek, actions=actions,
                          budget_seconds=budget_seconds)
    best_name, best_ev = None, None
    for name in ACTION_ORDER:
        item = solved["actions"].get(name)
        if not item:
            continue
        if best_name is None or item["ev"] > best_ev + 1e-10:
            best_name, best_ev = name, item["ev"]
    if best_name is None:
        raise ValueError("可见信息下没有可执行动作")
    return best_name, solved


def _settle(player, dealer, stake):
    p, d = _score(player), _score(dealer)
    if p > 21:
        return -stake
    if d > 21:
        return stake
    if p > d:
        return stake
    if p < d:
        return -stake
    return 0.0


def play_round(pack, *, policy=CONSUMPTION_PI, budget_seconds=2.0, decisions=None):
    """Play one unsplit round. `pack[0:4]` is P, up, P, hole. Returns remaining undealt."""
    pack = list(pack)
    if len(pack) < 4:
        raise InsufficientCards("剩余牌不足下一轮初始四张")
    p1, up, p2, hole = pack[0], pack[1], pack[2], pack[3]
    undealt = pack[4:]
    player = [p1, p2]
    natural = sorted(player) == [1, 10]
    dealer_bj = sorted((up, hole)) == [1, 10]
    peek = up in (1, 10) and not dealer_bj
    public = {"player": tuple(player), "up": up, "peek": peek, "policy": policy}
    if dealer_bj:
        return {"net": 0.0 if natural else -1.0, "remaining": undealt, "action": None,
                "public": public, "natural": natural, "dealer_bj": True, "stake": 1}
    if natural:
        return {"net": 1.5, "remaining": undealt, "action": "stand",
                "public": public, "natural": True, "dealer_bj": False, "stake": 1}

    action = "stand"
    stake = 1
    if policy == CONSUMPTION_PI:
        counts = counts_from_values([hole, *undealt])
        action, _solved = choose_action(counts, player, up, peek, budget_seconds=budget_seconds)
        if decisions is not None:
            decisions.append({"player": tuple(player), "up": up, "peek": peek,
                              "action": action, "counts": counts})
        if action == "surrender":
            return {"net": -0.5, "remaining": undealt, "action": action,
                    "public": public, "natural": False, "dealer_bj": False, "stake": 1}
        if action == "double":
            if not undealt:
                raise InsufficientCards("加倍时没有可补的牌")
            player.append(undealt.pop(0))
            stake = 2
            action = "double"
        elif action == "hit":
            while True:
                if not undealt:
                    raise InsufficientCards("补牌时牌靴耗尽")
                player.append(undealt.pop(0))
                if _score(player) > 21:
                    break
                counts = counts_from_values([hole, *undealt])
                nxt, _solved = choose_action(counts, player, up, peek, actions=("stand", "hit"),
                                             budget_seconds=budget_seconds)
                if decisions is not None:
                    decisions.append({"player": tuple(player), "up": up, "peek": peek,
                                      "action": nxt, "counts": counts})
                if nxt == "stand":
                    break
    elif policy == CONSUMPTION_BASIC:
        while True:
            action = basic_unsplit_action(player, up)
            if action != "hit":
                break
            if not undealt:
                raise InsufficientCards("补牌时牌靴耗尽")
            player.append(undealt.pop(0))
            if _score(player) > 21:
                break
    elif policy != CONSUMPTION_STAND:
        raise ValueError("未知消耗策略")

    if _score(player) > 21:
        return {"net": -float(stake), "remaining": undealt, "action": action,
                "public": public, "natural": False, "dealer_bj": False, "stake": stake}
    dealer = [up, hole]
    while not _dealer_stands(dealer):
        if not undealt:
            raise InsufficientCards("庄家尚需补牌但合成小牌靴已耗尽")
        dealer.append(undealt.pop(0))
    return {"net": float(_settle(player, dealer, stake)), "remaining": undealt, "action": action,
            "public": public, "natural": False, "dealer_bj": False, "stake": stake}


def evaluate_predeal(pack, budget_seconds=5.0):
    remaining = len(pack)
    counts = counts_from_values(pack)
    record = {"physical_remaining": remaining, "counts": list(counts), "window": WINDOW_PRE_DEAL}
    if remaining > PREDEAL_MAX_REMAINING:
        record.update(status=UNSUPPORTED, reason_code="PREDEAL_SHOE_TOO_LARGE",
                      reason=f"剩余{remaining}张超过精确穷举上限{PREDEAL_MAX_REMAINING}", ev=None)
        return record
    if remaining < 4:
        record.update(status="inapplicable", reason_code="PREDEAL_TOO_FEW_CARDS",
                      reason="剩余牌不足下一轮初始四张", ev=None)
        return record
    try:
        numbers = solve_predeal_counts(counts, budget_seconds=budget_seconds)
        record.update(status=AVAILABLE, reason_code="CALCULATED", ev=numbers["ev"],
                      variance=numbers["variance"], outcomes=numbers["outcomes"],
                      elapsed_seconds=numbers["elapsed_seconds"])
    except CalculationStopped as error:
        record.update(status=TIMEOUT, reason_code=str(error),
                      reason="预算到期，发牌前请求未完成；未使用当前手牌结果", ev=None)
    except InsufficientCards as error:
        record.update(status=UNSUPPORTED, reason_code="INSUFFICIENT_CARDS", reason=str(error), ev=None)
    except Exception as error:
        record.update(status=FAILED, reason_code="CALCULATION_FAILED", reason=str(error), ev=None)
    return record


def _pay_distribution(pays):
    buckets = {}
    for pay in pays:
        key = f"{round(float(pay), 4):+g}"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _summarize(kind, rounds, *, n_decks, seed, margin, consumption):
    available = [item for item in rounds if item["predeal"].get("status") == AVAILABLE]
    positive = [item for item in available if item["predeal"]["ev"] > 0]
    margin_hits = [item for item in available if item["predeal"]["ev"] > margin]
    realized = [item["realized_net"] for item in rounds if item.get("realized_net") is not None]
    streaks = _positive_streaks(rounds)
    return {
        "schema": SCHEMA,
        "kind": kind,
        "n_decks": n_decks,
        "seed": seed,
        "margin": margin,
        "strategy_version": PREDEAL_STRATEGY_VERSION,
        "consumption_policy": consumption,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "rounds": rounds,
        "summary": {
            "round_count": len(rounds),
            "predeal_available": len(available),
            "predeal_unsupported": sum(1 for item in rounds if item["predeal"].get("status") == UNSUPPORTED),
            "predeal_timeout": sum(1 for item in rounds if item["predeal"].get("status") == TIMEOUT),
            "predeal_failed": sum(1 for item in rounds if item["predeal"].get("status") == FAILED),
            "positive_ev": len(positive),
            "negative_ev": sum(1 for item in available if item["predeal"]["ev"] < 0),
            "exceeds_margin": len(margin_hits),
            "zero_window": len(positive) == 0,
            "signal_coverage": (len(available) / len(rounds) if rounds else 0.0),
            "sample_unit": "round-within-shoe",
            "mean_available_ev": (sum(item["predeal"]["ev"] for item in available) / len(available)
                                  if available else None),
            "positive_ev_rate_among_available": (len(positive) / len(available) if available else None),
            "exceeds_margin_rate_among_available": (len(margin_hits) / len(available) if available else None),
            "realized_mean": (sum(realized) / len(realized) if realized else None),
            "realized_distribution": _pay_distribution(realized),
            "realized_path_is_not_counterfactual_truth": True,
            "positive_streak_max": max(streaks, default=0),
            "positive_streaks": streaks,
        },
    }


def _positive_streaks(rounds):
    streaks, current = [], 0
    for item in rounds:
        ev = item.get("predeal", {}).get("ev")
        if item.get("predeal", {}).get("status") == AVAILABLE and ev is not None and ev > 0:
            current += 1
            continue
        if current:
            streaks.append(current)
        current = 0
    if current:
        streaks.append(current)
    return streaks


def run_window_study(*, kind, n_decks=6, remaining=None, pack=None, seed=1, margin=0.01,
                     budget_seconds=5.0, max_rounds=80, play_budget_seconds=2.0,
                     play_policy=None):
    rng = Random(seed)
    play_policy = play_policy or CONSUMPTION_PI
    if pack is not None:
        initial = list(pack)
    elif kind in (KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE):
        initial = sample_pack(n_decks, remaining or 8, rng)
    else:
        initial = full_pack(n_decks)
    if kind == KIND_FULL_RESHUFFLE:
        rounds = []
        for index in range(min(max_rounds, 8)):
            predeal = evaluate_predeal(initial, budget_seconds=budget_seconds)
            rounds.append({"round_index": index, "predeal": predeal, "realized_net": None,
                           "consumption_policy": None})
        return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                          consumption="none-reshuffle-full-pack")
    if kind == KIND_LATE_RESHUFFLE:
        rounds = []
        baseline = evaluate_predeal(initial, budget_seconds=budget_seconds)
        for index in range(min(max_rounds, 8)):
            shuffled = list(initial)
            rng.shuffle(shuffled)
            try:
                played = play_round(shuffled, policy=play_policy, budget_seconds=play_budget_seconds)
                realized = played["net"]
            except (InsufficientCards, CalculationStopped, ValueError) as error:
                realized = None
                played = {"error": str(error)}
            rounds.append({"round_index": index, "predeal": dict(baseline), "realized_net": realized,
                           "consumption_policy": play_policy, "play_error": played.get("error")})
        return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                          consumption=play_policy)
    current = list(initial)
    rng.shuffle(current)
    rounds = []
    for index in range(max_rounds):
        if len(current) < 4:
            break
        predeal = evaluate_predeal(current, budget_seconds=budget_seconds)
        if kind == KIND_FULL_DEPLETE and len(current) > PREDEAL_MAX_REMAINING:
            policy = CONSUMPTION_STAND
        else:
            policy = play_policy
        try:
            played = play_round(current, policy=policy, budget_seconds=play_budget_seconds)
            realized = played["net"]
            current = list(played["remaining"])
            error = None
        except (InsufficientCards, CalculationStopped, ValueError) as err:
            realized = None
            error = str(err)
        rounds.append({"round_index": index, "predeal": predeal, "realized_net": realized,
                       "consumption_policy": policy, "play_error": error})
        if error:
            break
    consumption = (CONSUMPTION_STAND + "+" + play_policy if kind == KIND_FULL_DEPLETE
                   else play_policy)
    return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                      consumption=consumption)


def run_independent_shoes(*, n_shoes=3, base_seed=1, **kwargs):
    """Aggregate window studies at the shoe unit. Rounds inside a shoe stay dependent."""
    if n_shoes < 1:
        raise ValueError("独立牌靴数必须为正")
    shoes = []
    for index in range(n_shoes):
        seed = int(base_seed) + index * 17
        report = run_window_study(seed=seed, **kwargs)
        summary = dict(report["summary"])
        shoes.append({
            "seed": seed,
            "kind": report.get("kind"),
            "n_decks": report.get("n_decks"),
            "not_a_reliable_window_claim": True,
            "summary": summary,
        })
    coverages = [item["summary"]["signal_coverage"] for item in shoes]
    zero = sum(1 for item in shoes if item["summary"]["zero_window"])
    positive_shoes = sum(1 for item in shoes if item["summary"]["positive_ev"] > 0)
    exceeds_shoes = sum(1 for item in shoes if item["summary"]["exceeds_margin"] > 0)
    negative_shoes = sum(1 for item in shoes if item["summary"]["negative_ev"] > 0)
    streaks = [item["summary"].get("positive_streak_max") or 0 for item in shoes]
    pooled = {}
    for item in shoes:
        for key, count in (item["summary"].get("realized_distribution") or {}).items():
            pooled[key] = pooled.get(key, 0) + count
    return {
        "schema": "hakimi-independent-shoe-ensemble-v1",
        "window": WINDOW_PRE_DEAL,
        "sample_unit": "shoe",
        "not_independent_round_samples": True,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "realized_path_is_not_counterfactual_truth": True,
        "n_shoes": n_shoes,
        "base_seed": base_seed,
        "kind": kwargs.get("kind"),
        "n_decks": kwargs.get("n_decks", 6),
        "shoes": shoes,
        "summary": {
            "zero_window_shoes": zero,
            "zero_window_rate": zero / n_shoes,
            "positive_ev_shoes": positive_shoes,
            "positive_ev_shoe_rate": positive_shoes / n_shoes,
            "negative_ev_shoes": negative_shoes,
            "exceeds_margin_shoes": exceeds_shoes,
            "exceeds_margin_shoe_rate": exceeds_shoes / n_shoes,
            "mean_signal_coverage": sum(coverages) / len(coverages),
            "max_positive_streak_across_shoes": max(streaks, default=0),
            "mean_positive_streak_max": sum(streaks) / len(streaks),
            "realized_distribution_pooled_not_independent": pooled,
            "note": "按独立牌靴汇总；同靴内各轮相关，不能当独立样本。收益分布按轮合并只供描述，不是独立抽样。不是未使用真实录像。",
        },
    }


def run_policy_contrast(*, pack, seed=1, policies=None, kind=KIND_LATE_DEPLETE, **kwargs):
    """Same shuffle origin, separate remaining paths. Not a shared counterfactual."""
    policies = tuple(policies or (CONSUMPTION_STAND, CONSUMPTION_BASIC))
    if len(policies) < 2:
        raise ValueError("耗牌对照至少需要两种策略")
    kwargs.pop("play_policy", None)
    arms = []
    remaining_paths = []
    for policy in policies:
        report = run_window_study(kind=kind, pack=list(pack), seed=seed, play_policy=policy, **kwargs)
        remaining = [item["predeal"]["physical_remaining"] for item in report["rounds"]]
        remaining_paths.append(remaining)
        arms.append({
            "policy": policy,
            "summary": report["summary"],
            "physical_remaining_by_round": remaining,
            "realized_nets": [item.get("realized_net") for item in report["rounds"]],
        })
    return {
        "schema": "hakimi-policy-consumption-contrast-v1",
        "window": WINDOW_PRE_DEAL,
        "shared_realized_path": False,
        "realized_path_is_not_counterfactual_truth": True,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "seed": seed,
        "kind": kind,
        "policies": list(policies),
        "arms": arms,
        "remaining_paths_equal": remaining_paths and all(path == remaining_paths[0] for path in remaining_paths),
        "note": "同一洗牌起点、各自耗牌；不能把一条实现路径当所有反事实策略的共同真值",
    }


def physical_mean(pack, n_plays, seed, budget_seconds=2.0):
    rng = Random(seed)
    pays = []
    base = list(pack)
    for _ in range(n_plays):
        shuffled = list(base)
        rng.shuffle(shuffled)
        pays.append(play_round(shuffled, policy=CONSUMPTION_PI, budget_seconds=budget_seconds)["net"])
    return sum(pays) / len(pays), pays
