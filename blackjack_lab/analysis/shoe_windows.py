"""Full-shoe / late-shoe pre-deal window study. Not a second EV engine.

The simulator may know the shuffle. Strategy π sees only public remaining
composition, the visible initial cards, and the US peek result. Dealer
blackjack is a real branch. Remaining >16 is recorded as unsupported, not
retitled current-hand EV. Timeouts stay timeouts. Zero-window shoes are valid.
"""
from __future__ import annotations

from random import Random
from time import time

from .actions import HIT_CONTINUATION_COMPOSITION, solve_counts
from .contracts import UNSUPPORTED, TIMEOUT, AVAILABLE, FAILED, digest
from .predeal import solve_predeal_counts
from .predeal_contracts import (
    PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION, SURRENDER_UNSET,
    legal_predeal_actions, require_declared_surrender,
)
from .probability import CalculationStopped, InsufficientCards
from .research_windows import (
    EV_POSITIVE, WINDOW_PRE_DEAL, classify_opening_window, counts_from_values,
    evaluation_scope, window_state,
)
from ..core.cards import hand_total

SCHEMA = "hakimi-shoe-window-study-v1"
KIND_LATE_DEPLETE = "late-deplete"
KIND_LATE_RESHUFFLE = "late-reshuffle-control"
KIND_FULL_RESHUFFLE = "full-reshuffle-control"
KIND_FULL_DEPLETE = "full-deplete-stand-then-exact"
CONSUMPTION_STAND = "always-stand-v1"
CONSUMPTION_PI = PREDEAL_STRATEGY_VERSION
CONSUMPTION_BASIC = "basic-s17-unsplit-no-double-v1"
CONSUMPTION_TOY_HARD = CONSUMPTION_BASIC
CONSUMPTION_LEGAL_UNSPLIT = "legal-unsplit-s17-no-split-no-insurance-v1"
ACTION_ORDER = ("stand", "hit", "double", "surrender")
RESEARCH_DEFAULT_CUT_REMAINING = 52
EVALUATION_EXACT_SMALL = "exact_small"
EVALUATION_FIXED_POLICY_MC = "fixed_policy_monte_carlo"
POLICY_DISPLAY = {
    CONSUMPTION_STAND: "冻结停牌消耗策略",
    CONSUMPTION_BASIC: "玩具硬点数消耗策略（不是已核验基本策略表）",
    CONSUMPTION_LEGAL_UNSPLIT: "冻结合法未分牌S17（硬/软/加倍/允许时晚投降；不分牌、不买保险）",
    CONSUMPTION_PI: "小牌靴精确未分牌可见信息最优（≤16）",
}


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


def legal_unsplit_s17_action(player, up, *, surrender, can_double=True, can_surrender=True):
    """Frozen S17 unsplit chart: hard/soft/double/late surrender.

    Not a verified casino basic-strategy table. No split, no insurance,
    not composition-dependent. Double and surrender only when the caller
    says those actions are still legal (initial two cards).
    """
    total, soft = hand_total([_rank(v) for v in player])
    if total is None:
        raise ValueError("点数不可知")
    up = int(up)
    two = len(player) == 2
    if can_surrender and two and surrender == "late" and not soft:
        if total == 16 and up in (1, 9, 10):
            return "surrender"
        if total == 15 and up == 10:
            return "surrender"
    if two and can_double:
        if soft:
            if total in (13, 14) and up in (5, 6):
                return "double"
            if total in (15, 16) and up in (4, 5, 6):
                return "double"
            if total == 17 and up in (3, 4, 5, 6):
                return "double"
            if total == 18 and up in (3, 4, 5, 6):
                return "double"
        else:
            if total == 9 and up in (3, 4, 5, 6):
                return "double"
            if total == 10 and up in (2, 3, 4, 5, 6, 7, 8, 9):
                return "double"
            if total == 11 and up != 1:
                return "double"
    if soft:
        if total <= 17:
            return "hit"
        if total == 18:
            return "stand" if up in (2, 7, 8) else "hit"
        return "stand"
    if total <= 11:
        return "hit"
    if total == 12:
        return "stand" if up in (4, 5, 6) else "hit"
    if total <= 16:
        return "stand" if up in (2, 3, 4, 5, 6) else "hit"
    return "stand"


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


def resolve_cut_remaining(n_decks, *, pack=None, cut_remaining=None):
    """Declared cut depth. Undeclared full-shoe deplete uses research default 52, not a lucky tail."""
    if cut_remaining is not None:
        if type(cut_remaining) is not int or cut_remaining < 0:
            raise ValueError("切牌剩余必须是非负整数")
        return cut_remaining, True, "caller"
    if pack is None and n_decks in (6, 7, 8):
        return RESEARCH_DEFAULT_CUT_REMAINING, True, "research-default-52"
    return 0, False, "undeclared-play-to-exhaustion"


def choose_action(counts, player, up, peek, actions=None, budget_seconds=2.0,
                  hit_continuation=HIT_CONTINUATION_COMPOSITION):
    if not actions:
        raise ValueError("动作集合必须显式给出，不能默认含投降")
    actions = tuple(actions)
    solved = solve_counts(
        counts, tuple(player), up, peek, actions=actions,
        budget_seconds=budget_seconds, hit_continuation=hit_continuation)
    best_name, best_ev = None, None
    for name in actions:
        item = solved["actions"].get(name)
        if not item:
            continue
        if best_name is None or item["ev"] > best_ev + 1e-10:
            best_name, best_ev = name, item["ev"]
    if best_name is None:
        raise ValueError("可见信息下没有可执行动作")
    if best_name == "surrender" and "surrender" not in actions:
        raise ValueError("无投降规则下不能选择投降")
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


def observation_remainings(*, hole, undealt, hole_revealed):
    """Private remaining is the next-round shoe. Public remaining is unseen ranks.

    An unrevealed hole has already left the shoe, but its rank is still unseen.
    Public remaining therefore keeps that hole; it is not the next-round truth.
    """
    private = list(undealt)
    public = list(undealt) if hole_revealed else [hole, *undealt]
    return {
        "remaining": private,
        "private_remaining": private,
        "public_remaining": public,
        "hole_revealed": bool(hole_revealed),
        "public_remaining_is_not_private_truth": sorted(public) != sorted(private),
    }


def public_dealt_cards(pack, consumed, hole_revealed):
    """Face-up events only. An unrevealed hole is not a public deal."""
    if type(consumed) is not int or consumed < 4:
        raise ValueError("本轮公开事件必须发完初始四张")
    pack = list(pack)
    if consumed > len(pack):
        raise ValueError("消耗张数超过本轮牌序")
    cards = list(pack[:3])
    if hole_revealed:
        cards.append(pack[3])
    cards.extend(pack[4:consumed])
    return cards


def remaining_after_events(pack, dealt):
    """Subtract believed deals from the pre-round pack. Missing ranks are inconsistent."""
    left = list(pack)
    for card in dealt:
        try:
            left.remove(card)
        except ValueError:
            return None
    return left


def play_round(pack, *, policy=CONSUMPTION_PI, budget_seconds=2.0, decisions=None,
               surrender=SURRENDER_UNSET):
    """Play one unsplit round. `pack[0:4]` is P, up, P, hole. Returns remaining undealt."""
    surrender = require_declared_surrender(surrender, what="整轮对局")
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

    def _done(payload, *, hole_revealed):
        payload.update(observation_remainings(hole=hole, undealt=undealt, hole_revealed=hole_revealed))
        consumed = len(pack) - len(undealt)
        payload["consumed"] = consumed
        payload["public_dealt"] = public_dealt_cards(pack, consumed, hole_revealed)
        payload["public"] = public
        payload["natural"] = natural
        payload["dealer_bj"] = dealer_bj
        return payload

    if dealer_bj:
        return _done({"net": 0.0 if natural else -1.0, "action": None, "stake": 1},
                     hole_revealed=True)
    if natural:
        return _done({"net": 1.5, "action": "stand", "stake": 1}, hole_revealed=True)

    action = "stand"
    stake = 1
    if policy == CONSUMPTION_PI:
        counts = counts_from_values([hole, *undealt])
        action, _solved = choose_action(
            counts, player, up, peek, actions=legal_predeal_actions(surrender),
            budget_seconds=budget_seconds)
        if decisions is not None:
            decisions.append({"player": tuple(player), "up": up, "peek": peek,
                              "action": action, "counts": counts})
        if action == "surrender":
            return _done({"net": -0.5, "action": action, "stake": 1}, hole_revealed=False)
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
    elif policy == CONSUMPTION_LEGAL_UNSPLIT:
        first = True
        while True:
            action = legal_unsplit_s17_action(
                player, up, surrender=surrender,
                can_double=first and len(player) == 2,
                can_surrender=first and len(player) == 2)
            if action == "surrender":
                return _done({"net": -0.5, "action": action, "stake": 1}, hole_revealed=False)
            if action == "double":
                if not undealt:
                    raise InsufficientCards("加倍时没有可补的牌")
                player.append(undealt.pop(0))
                stake = 2
                break
            if action != "hit":
                break
            if not undealt:
                raise InsufficientCards("补牌时牌靴耗尽")
            player.append(undealt.pop(0))
            first = False
            if _score(player) > 21:
                break
    elif policy != CONSUMPTION_STAND:
        raise ValueError("未知消耗策略")

    if _score(player) > 21:
        return _done({"net": -float(stake), "action": action, "stake": stake}, hole_revealed=False)
    dealer = [up, hole]
    while not _dealer_stands(dealer):
        if not undealt:
            raise InsufficientCards("庄家尚需补牌但合成小牌靴已耗尽")
        dealer.append(undealt.pop(0))
    return _done({"net": float(_settle(player, dealer, stake)), "action": action, "stake": stake},
                 hole_revealed=True)


def evaluate_predeal(pack, budget_seconds=5.0, surrender=SURRENDER_UNSET):
    surrender = require_declared_surrender(surrender, what="发牌前评估")
    remaining = len(pack)
    counts = counts_from_values(pack)
    record = {
        "physical_remaining": remaining,
        "counts": list(counts),
        "window": WINDOW_PRE_DEAL,
        "window_kind": WINDOW_PRE_DEAL,
        "source_mode": "synthetic-composition",
        "strategy_id": PREDEAL_STRATEGY_VERSION,
        "rules_digest": digest({"surrender": surrender, "window": WINDOW_PRE_DEAL}),
        "method": None,
        "knowledge_revision": None,
        "ledger_prefix_digest": None,
        "information_cutoff": None,
        "result_ready_at": None,
        "decision_deadline": None,
        "timely": False,
        "not_a_reliable_window_claim": True,
        "surrender": surrender,
        "legal_actions": list(legal_predeal_actions(surrender)),
    }
    if remaining > PREDEAL_MAX_REMAINING:
        record.update(status=UNSUPPORTED, reason_code="PREDEAL_SHOE_TOO_LARGE",
                      reason=f"剩余{remaining}张超过精确穷举上限{PREDEAL_MAX_REMAINING}", ev=None)
    elif remaining < 4:
        record.update(status="inapplicable", reason_code="PREDEAL_TOO_FEW_CARDS",
                      reason="剩余牌不足下一轮初始四张", ev=None)
    else:
        try:
            numbers = solve_predeal_counts(
                counts, budget_seconds=budget_seconds, surrender=surrender)
            record.update(status=AVAILABLE, reason_code="CALCULATED", ev=numbers["ev"],
                          variance=numbers["variance"], outcomes=numbers["outcomes"],
                          elapsed_seconds=numbers["elapsed_seconds"],
                          legal_actions=list(numbers["legal_actions"]),
                          method=numbers.get("method"),
                          result_ready_at=time())
        except CalculationStopped as error:
            record.update(status=TIMEOUT, reason_code=str(error),
                          reason="预算到期，发牌前请求未完成；未使用当前手牌结果", ev=None)
        except InsufficientCards as error:
            record.update(status=UNSUPPORTED, reason_code="INSUFFICIENT_CARDS", reason=str(error), ev=None)
        except Exception as error:
            record.update(status=FAILED, reason_code="CALCULATION_FAILED", reason=str(error), ev=None)
    record["window_state"] = window_state(record)
    return record


def evaluate_checkpoint(pack, *, surrender, evaluation_method=EVALUATION_EXACT_SMALL,
                        evaluation_policy_id=None, budget_seconds=5.0, mc_n_samples=64,
                        mc_seed=1, mc_z=1.96, mc_play_budget_seconds=2.0, mc_family_size=1,
                        mc_alpha=0.05):
    """Evaluate one remaining pack. Exact and frozen-policy MC stay separate methods."""
    surrender = require_declared_surrender(surrender, what="检查点评估")
    method = evaluation_method or EVALUATION_EXACT_SMALL
    if method == EVALUATION_EXACT_SMALL:
        if evaluation_policy_id not in (None, PREDEAL_STRATEGY_VERSION, CONSUMPTION_PI):
            raise ValueError("精确发牌前检查点不能改用冻结策略冒充最优")
        record = dict(evaluate_predeal(pack, budget_seconds=budget_seconds, surrender=surrender))
        record["evaluation_method"] = EVALUATION_EXACT_SMALL
        record["evaluation_policy_id"] = PREDEAL_STRATEGY_VERSION
        record["input_scope"] = "fixed_composition"
        record["not_merged_with_monte_carlo"] = True
        return record
    if method != EVALUATION_FIXED_POLICY_MC:
        raise ValueError(f"未知检查点评估方法: {method!r}")
    from .fixed_policy_mc import SUPPORTED_POLICIES, evaluate_fixed_policy
    policy = evaluation_policy_id or CONSUMPTION_STAND
    if policy not in SUPPORTED_POLICIES:
        raise ValueError("MC检查点必须使用已声明冻结策略；不能把精确最优并进MC")
    report = dict(evaluate_fixed_policy(
        pack=list(pack), policy=policy, n_samples=mc_n_samples, seed=mc_seed,
        surrender=surrender, z=mc_z, play_budget_seconds=mc_play_budget_seconds,
        family_size=mc_family_size, alpha=mc_alpha,
    ))
    report["evaluation_method"] = EVALUATION_FIXED_POLICY_MC
    report["evaluation_policy_id"] = policy
    report["not_merged_with_exact_optimal"] = True
    return report


def _pay_distribution(pays):
    buckets = {}
    for pay in pays:
        key = f"{round(float(pay), 4):+g}"
        buckets[key] = buckets.get(key, 0) + 1
    return buckets


def _summarize(kind, rounds, *, n_decks, seed, margin, consumption,
               cut_remaining=0, cut_declared=False, cut_source="undeclared-play-to-exhaustion",
               stop_reason=None, remaining_at_end=None, surrender=SURRENDER_UNSET,
               evaluation_method=EVALUATION_EXACT_SMALL, evaluation_policy_id=None,
               mc_family_size=None, mc_alpha=None):
    surrender = require_declared_surrender(surrender, what="整靴窗口摘要")
    available = [item for item in rounds
                 if item["predeal"].get("status") == AVAILABLE and item["predeal"].get("ev") is not None]
    margin_hits = [item for item in available if item["predeal"]["ev"] > margin]
    realized = [item["realized_net"] for item in rounds if item.get("realized_net") is not None]
    streaks = _positive_streaks(rounds)
    scope = evaluation_scope([item["predeal"] for item in rounds])
    eval_policy = evaluation_policy_id or PREDEAL_STRATEGY_VERSION
    exact = evaluation_method == EVALUATION_EXACT_SMALL
    return {
        "schema": SCHEMA,
        "kind": kind,
        "n_decks": n_decks,
        "seed": seed,
        "margin": margin,
        "strategy_version": PREDEAL_STRATEGY_VERSION,
        "consumption_policy": consumption,
        "path_policy_id": consumption,
        "evaluation_method": evaluation_method,
        "evaluation_policy_id": eval_policy,
        "evaluation_policy_note": (
            "快照发牌前EV仍按精确未分牌最优；不是当前耗牌策略自己的开局EV"
            if exact else
            "检查点EV按冻结策略MC；不是精确最优，也不是耗牌路径与评估方法的合并曲线"
        ),
        "methods_not_merged": True,
        "mc_family_size": mc_family_size,
        "mc_alpha": mc_alpha,
        "path_policy_display": POLICY_DISPLAY.get(consumption, consumption),
        "cut_remaining": cut_remaining,
        "cut_declared": cut_declared,
        "cut_source": cut_source,
        "cut_policy": {
            "cut_remaining": cut_remaining,
            "cut_declared": cut_declared,
            "cut_source": cut_source,
            "not_moved_to_last_cards": True,
            "not_relocated_to_exact_cap": cut_remaining != PREDEAL_MAX_REMAINING,
        },
        "stop_reason": stop_reason,
        "remaining_at_end": remaining_at_end,
        "information_scope": "ideal_composition",
        "ideal_full_observation": True,
        "next_remaining_source": "environment-private",
        "public_observer_is_not_private_remaining": True,
        "current_hand_not_used_as_opening": True,
        "window": WINDOW_PRE_DEAL,
        "surrender": surrender,
        "not_a_reliable_window_claim": True,
        "rounds": rounds,
        "summary": {
            "round_count": len(rounds),
            "predeal_available": len(available),
            "predeal_unsupported": sum(1 for item in rounds if item["predeal"].get("status") == UNSUPPORTED),
            "predeal_timeout": sum(1 for item in rounds if item["predeal"].get("status") == TIMEOUT),
            "predeal_failed": sum(1 for item in rounds if item["predeal"].get("status") == FAILED),
            "positive_ev": scope["positive"],
            "nonpositive_ev": scope["nonpositive"],
            "negative_ev": sum(1 for item in available if item["predeal"]["ev"] < 0),
            "indeterminate_ev": scope["indeterminate"],
            "unavailable_ev": scope["unavailable"],
            "exceeds_margin": len(margin_hits),
            "zero_window": scope["zero_window"],
            "no_positive_signal_detected": scope["no_positive_signal_detected"],
            "no_positive_window_in_complete_evaluation": scope["no_positive_window_in_complete_evaluation"],
            "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
            "complete_evaluation": scope["complete_evaluation"],
            "evaluated_count": scope["evaluated_count"],
            "unassessable_count": scope["unassessable_count"],
            "verified_no_positive_over_declared_domain": scope["verified_no_positive_over_declared_domain"],
            "signal_coverage": (len(available) / len(rounds) if rounds else 0.0),
            "sample_unit": "round-within-shoe",
            "mean_available_ev": (sum(item["predeal"]["ev"] for item in available) / len(available)
                                  if available else None),
            "positive_ev_rate_among_available": (scope["positive"] / len(available) if available else None),
            "exceeds_margin_rate_among_available": (len(margin_hits) / len(available) if available else None),
            "realized_mean": (sum(realized) / len(realized) if realized else None),
            "realized_count": len(realized),
            "realized_distribution": _pay_distribution(realized),
            "realized_path_is_not_counterfactual_truth": True,
            "positive_streak_max": max(streaks, default=0),
            "positive_streaks": streaks,
            "stop_reason": stop_reason,
            "cut_remaining": cut_remaining,
            "remaining_at_end": remaining_at_end,
        },
    }


def _positive_streaks(rounds):
    streaks, current = [], 0
    for item in rounds:
        if classify_opening_window(item.get("predeal")) == EV_POSITIVE:
            current += 1
            continue
        if current:
            streaks.append(current)
        current = 0
    if current:
        streaks.append(current)
    return streaks


def _attach_checkpoint(predeal, *, path_policy_id, cut, cut_declared, cut_source):
    record = dict(predeal)
    record["path_policy_id"] = path_policy_id
    record["cut_remaining"] = cut
    record["cut_declared"] = cut_declared
    record["cut_source"] = cut_source
    record["cut_policy"] = {
        "cut_remaining": cut,
        "cut_declared": cut_declared,
        "cut_source": cut_source,
        "not_moved_to_last_cards": True,
    }
    return record


def _planned_mc_family_size(initial, *, cut, max_rounds, kind):
    """Prespecified checkpoint budget. Do not use the realized round count after play."""
    if kind == KIND_FULL_RESHUFFLE:
        return max(1, min(max_rounds, 8))
    playable = max(0, len(initial) - max(int(cut or 0), 0))
    return max(1, min(max_rounds, max(playable, 4) // 4))


def run_window_study(*, kind, n_decks=6, remaining=None, pack=None, seed=1, margin=0.01,
                     budget_seconds=5.0, max_rounds=80, play_budget_seconds=2.0,
                     play_policy=None, surrender=SURRENDER_UNSET, cut_remaining=None,
                     evaluation_method=EVALUATION_EXACT_SMALL, evaluation_policy_id=None,
                     mc_n_samples=64, mc_seed=None, mc_z=1.96, mc_alpha=0.05):
    surrender = require_declared_surrender(surrender, what="整靴窗口研究")
    rng = Random(seed)
    eval_method = evaluation_method or EVALUATION_EXACT_SMALL
    if eval_method == EVALUATION_EXACT_SMALL:
        eval_policy = PREDEAL_STRATEGY_VERSION
    else:
        eval_policy = evaluation_policy_id or CONSUMPTION_STAND
    if play_policy is None:
        if eval_method == EVALUATION_FIXED_POLICY_MC:
            play_policy = eval_policy
        elif kind == KIND_FULL_RESHUFFLE:
            play_policy = CONSUMPTION_STAND
        else:
            play_policy = CONSUMPTION_PI
    cut, cut_declared, cut_source = resolve_cut_remaining(
        n_decks, pack=pack, cut_remaining=cut_remaining)
    if pack is not None:
        initial = list(pack)
    elif kind in (KIND_LATE_DEPLETE, KIND_LATE_RESHUFFLE):
        initial = sample_pack(n_decks, remaining or 8, rng)
    else:
        initial = full_pack(n_decks)
    mc_base_seed = seed if mc_seed is None else mc_seed
    mc_family_size = _planned_mc_family_size(
        initial, cut=cut, max_rounds=max_rounds, kind=kind)

    def _predeal_at(current, *, round_index, path_policy_id, checkpoint_cut, checkpoint_declared,
                    checkpoint_source):
        record = evaluate_checkpoint(
            current, surrender=surrender, evaluation_method=eval_method,
            evaluation_policy_id=eval_policy if eval_method == EVALUATION_FIXED_POLICY_MC
            else evaluation_policy_id, budget_seconds=budget_seconds,
            mc_n_samples=mc_n_samples, mc_seed=mc_base_seed + round_index, mc_z=mc_z,
            mc_play_budget_seconds=play_budget_seconds, mc_family_size=mc_family_size,
            mc_alpha=mc_alpha)
        return _attach_checkpoint(
            record, path_policy_id=path_policy_id, cut=checkpoint_cut,
            cut_declared=checkpoint_declared, cut_source=checkpoint_source)

    summary_kw = dict(
        evaluation_method=eval_method, evaluation_policy_id=eval_policy,
        mc_family_size=mc_family_size, mc_alpha=mc_alpha)
    if kind == KIND_FULL_RESHUFFLE:
        path = CONSUMPTION_STAND if play_policy == CONSUMPTION_PI else play_policy
        rounds = []
        for index in range(min(max_rounds, 8)):
            predeal = _predeal_at(
                initial, round_index=index, path_policy_id=path,
                checkpoint_cut=len(initial), checkpoint_declared=True,
                checkpoint_source="full-reshuffle-no-penetration")
            shuffled = list(initial)
            rng.shuffle(shuffled)
            realized, error = None, None
            try:
                played = play_round(shuffled, policy=path, budget_seconds=play_budget_seconds,
                                    surrender=surrender)
                realized = played["net"]
            except (InsufficientCards, CalculationStopped, ValueError) as err:
                error = str(err)
            rounds.append({
                "round_index": index, "predeal": predeal, "realized_net": realized,
                "consumption_policy": path, "path_policy_id": path, "reshuffled": True,
                "history_removed": False, "play_error": error,
                "checkpoint_remaining": len(initial),
                "evaluation_method": eval_method,
                "evaluation_policy_id": eval_policy,
            })
        return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                          consumption=path, cut_remaining=len(initial),
                          cut_declared=True, cut_source="full-reshuffle-no-penetration",
                          stop_reason="independent-reset", remaining_at_end=len(initial),
                          surrender=surrender, **summary_kw)
    if kind == KIND_LATE_RESHUFFLE:
        rounds = []
        for index in range(min(max_rounds, 8)):
            predeal = _predeal_at(
                initial, round_index=index, path_policy_id=play_policy,
                checkpoint_cut=len(initial), checkpoint_declared=True,
                checkpoint_source="late-reshuffle-fixed-pack")
            shuffled = list(initial)
            rng.shuffle(shuffled)
            realized, error = None, None
            try:
                played = play_round(shuffled, policy=play_policy, budget_seconds=play_budget_seconds,
                                    surrender=surrender)
                realized = played["net"]
            except (InsufficientCards, CalculationStopped, ValueError) as err:
                error = str(err)
            rounds.append({"round_index": index, "predeal": predeal, "realized_net": realized,
                           "consumption_policy": play_policy, "path_policy_id": play_policy,
                           "reshuffled": True, "history_removed": False, "play_error": error,
                           "evaluation_method": eval_method,
                           "evaluation_policy_id": eval_policy})
        return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                          consumption=play_policy, cut_remaining=len(initial),
                          cut_declared=True, cut_source="late-reshuffle-fixed-pack",
                          stop_reason="independent-reset", remaining_at_end=len(initial),
                          surrender=surrender, **summary_kw)
    current = list(initial)
    rng.shuffle(current)
    rounds = []
    stop_reason = "max_rounds"
    for index in range(max_rounds):
        if len(current) < 4:
            stop_reason = "exhausted"
            break
        if cut > 0 and len(current) <= cut:
            stop_reason = "cut"
            break
        if (kind == KIND_FULL_DEPLETE and eval_method == EVALUATION_EXACT_SMALL
                and len(current) > PREDEAL_MAX_REMAINING):
            policy = CONSUMPTION_STAND
        else:
            policy = play_policy
        predeal = _predeal_at(
            current, round_index=index, path_policy_id=policy,
            checkpoint_cut=cut, checkpoint_declared=cut_declared, checkpoint_source=cut_source)
        try:
            played = play_round(current, policy=policy, budget_seconds=play_budget_seconds,
                                surrender=surrender)
            realized = played["net"]
            current = list(played["remaining"])
            error = None
        except (InsufficientCards, CalculationStopped, ValueError) as err:
            realized = None
            error = str(err)
        rounds.append({"round_index": index, "predeal": predeal, "realized_net": realized,
                       "consumption_policy": policy, "path_policy_id": policy,
                       "checkpoint_remaining": len(current), "play_error": error,
                       "evaluation_method": eval_method,
                       "evaluation_policy_id": eval_policy})
        if error:
            stop_reason = "play_error"
            break
    if kind == KIND_FULL_DEPLETE and eval_method == EVALUATION_EXACT_SMALL:
        consumption = CONSUMPTION_STAND + "+" + play_policy
    else:
        consumption = play_policy
    return _summarize(kind, rounds, n_decks=n_decks, seed=seed, margin=margin,
                      consumption=consumption, cut_remaining=cut, cut_declared=cut_declared,
                      cut_source=cut_source, stop_reason=stop_reason,
                      remaining_at_end=len(current), surrender=surrender, **summary_kw)


def run_independent_shoes(*, n_shoes=3, base_seed=1, **kwargs):
    """Aggregate window studies at the shoe unit. Rounds inside a shoe stay dependent."""
    surrender = require_declared_surrender(
        kwargs.get("surrender", SURRENDER_UNSET), what="独立牌靴汇总")
    kwargs["surrender"] = surrender
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
            "path_policy_id": report.get("path_policy_id"),
            "evaluation_policy_id": report.get("evaluation_policy_id"),
            "cut_remaining": report.get("cut_remaining"),
            "cut_declared": report.get("cut_declared"),
            "stop_reason": report.get("stop_reason"),
            "remaining_at_end": report.get("remaining_at_end"),
            "surrender": report.get("surrender"),
            "checkpoints": [
                {
                    "round_index": item.get("round_index"),
                    "predeal_remaining": (item.get("predeal") or {}).get("physical_remaining"),
                    "remaining_after_round": item.get("checkpoint_remaining"),
                    "predeal": item.get("predeal"),
                    "realized_net": item.get("realized_net"),
                    "play_error": item.get("play_error"),
                    "path_policy_id": item.get("path_policy_id"),
                    "evaluation_policy_id": report.get("evaluation_policy_id"),
                    "evaluation_method": report.get("evaluation_method"),
                    "input_scope": (item.get("predeal") or {}).get("input_scope"),
                }
                for item in report.get("rounds") or []
            ],
            "summary": summary,
        })
    coverages = [item["summary"]["signal_coverage"] for item in shoes]
    zero = sum(1 for item in shoes if item["summary"]["zero_window"])
    incomplete = sum(1 for item in shoes if item["summary"]["incomplete_cannot_claim_zero_window"])
    no_signal = sum(1 for item in shoes if item["summary"]["no_positive_signal_detected"])
    complete_no_window = sum(
        1 for item in shoes if item["summary"]["no_positive_window_in_complete_evaluation"])
    positive_shoes = sum(1 for item in shoes if item["summary"]["positive_ev"] > 0)
    exceeds_shoes = sum(1 for item in shoes if item["summary"]["exceeds_margin"] > 0)
    negative_shoes = sum(1 for item in shoes if item["summary"]["negative_ev"] > 0)
    streaks = [item["summary"].get("positive_streak_max") or 0 for item in shoes]
    pooled = {}
    for item in shoes:
        for key, count in (item["summary"].get("realized_distribution") or {}).items():
            pooled[key] = pooled.get(key, 0) + count
    complete_shoes = n_shoes - incomplete
    zero_window_rate = (zero / complete_shoes) if complete_shoes else None
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
        "surrender": surrender,
        "shoes": shoes,
        "summary": {
            "zero_window_shoes": zero,
            "complete_shoes": complete_shoes,
            "zero_window_rate": zero_window_rate,
            "incomplete_shoes": incomplete,
            "incomplete_shoe_rate": incomplete / n_shoes,
            "incomplete_cannot_claim_zero_window": complete_shoes == 0,
            "no_positive_signal_shoes": no_signal,
            "no_positive_signal_rate": no_signal / n_shoes,
            "complete_no_positive_window_shoes": complete_no_window,
            "positive_ev_shoes": positive_shoes,
            "positive_ev_shoe_rate": (positive_shoes / complete_shoes) if complete_shoes else None,
            "negative_ev_shoes": negative_shoes,
            "exceeds_margin_shoes": exceeds_shoes,
            "exceeds_margin_shoe_rate": (exceeds_shoes / complete_shoes) if complete_shoes else None,
            "mean_signal_coverage": sum(coverages) / len(coverages),
            "max_positive_streak_across_shoes": max(streaks, default=0),
            "mean_positive_streak_max": sum(streaks) / len(streaks),
            "realized_distribution_pooled_not_independent": pooled,
            "note": "按独立牌靴汇总；同靴内各轮相关，不能当独立样本。"
                    "zero_window_rate 只在有完整评估的牌靴上计算；全不完整时为 null，不能写成已证明零窗口频率。"
                    "收益分布按轮合并只供描述，不是独立抽样。不是未使用真实录像。",
        },
    }


def run_policy_contrast(*, pack, seed=1, policies=None, kind=KIND_LATE_DEPLETE, **kwargs):
    """Same shuffle origin, separate remaining paths. Not a shared counterfactual."""
    surrender = require_declared_surrender(
        kwargs.get("surrender", SURRENDER_UNSET), what="策略耗牌对照")
    kwargs["surrender"] = surrender
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
            "path_policy_id": policy,
            "evaluation_policy_id": PREDEAL_STRATEGY_VERSION,
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
        "surrender": surrender,
        "policies": list(policies),
        "arms": arms,
        "remaining_paths_equal": remaining_paths and all(path == remaining_paths[0] for path in remaining_paths),
        "path_policy_id_by_arm": [arm["path_policy_id"] for arm in arms],
        "evaluation_policy_id": PREDEAL_STRATEGY_VERSION,
        "note": "同一洗牌起点、各自耗牌；path_policy 是消耗路径，evaluation_policy 是快照所用精确未分牌最优；"
                "不能把一条实现路径当所有反事实策略的共同真值，也不能把玩具硬规则叫已核验基本策略",
    }


def physical_mean(pack, n_plays, seed, budget_seconds=2.0, surrender=SURRENDER_UNSET):
    surrender = require_declared_surrender(surrender, what="物理排列均值")
    rng = Random(seed)
    pays = []
    base = list(pack)
    for _ in range(n_plays):
        shuffled = list(base)
        rng.shuffle(shuffled)
        pays.append(play_round(shuffled, policy=CONSUMPTION_PI, budget_seconds=budget_seconds,
                               surrender=surrender)["net"])
    return sum(pays) / len(pays), pays
