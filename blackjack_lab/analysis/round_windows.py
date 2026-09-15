"""3/6-round shoe consumption. Not multi-player EV and not a second solver.

Seat order is first-card then second-card around the table, then dealer.
Other seats follow an explicit consumption policy. They are not independent
samples. Remaining >16 still refuses pre-deal exact EV. Timeouts stay timeouts.
"""
from __future__ import annotations

from random import Random

from .predeal_contracts import (
    PREDEAL_STRATEGY_VERSION, SURRENDER_UNSET, legal_predeal_actions, require_declared_surrender,
)
from .probability import CalculationStopped, InsufficientCards
from .research_windows import WINDOW_PRE_DEAL, counts_from_values, evaluation_scope
from .shoe_windows import (
    CONSUMPTION_BASIC, CONSUMPTION_LEGAL_UNSPLIT, CONSUMPTION_PI, CONSUMPTION_STAND, POLICY_DISPLAY,
    EVALUATION_EXACT_SMALL,
    basic_unsplit_action, choose_action, evaluate_checkpoint, full_pack,
    legal_unsplit_s17_action, observation_remainings, play_round, resolve_cut_remaining,
    _dealer_stands, _score, _settle,
)

SCHEMA = "hakimi-round-window-study-v1"
OTHER_STAND = CONSUMPTION_STAND
AFTER_ROUNDS = (3, 6)


def play_seated_round(pack, *, n_players=1, target_index=0, target_policy=CONSUMPTION_BASIC,
                      other_policy=OTHER_STAND, budget_seconds=2.0, surrender=SURRENDER_UNSET):
    """Play one unsplit round with 1–7 seats. Target net is the research unit."""
    surrender = require_declared_surrender(surrender, what="多座位对局")
    if n_players not in range(1, 8):
        raise ValueError("对照只接受 1 到 7 个玩家座位")
    if not 0 <= target_index < n_players:
        raise ValueError("目标座位必须在参与座位内")
    pack = list(pack)
    if n_players == 1:
        played = play_round(pack, policy=target_policy, budget_seconds=budget_seconds,
                            surrender=surrender)
        played = dict(played)
        played.update(n_players=1, target_index=0, other_nets=[],
                      target_net=played["net"], not_multiplayer_ev=True)
        return played
    needed = 2 * (n_players + 1)
    if len(pack) < needed:
        raise InsufficientCards("剩余牌不足本轮各座位初始两张")
    seats = [[] for _ in range(n_players)]
    dealer = []
    for _pass in range(2):
        for i in range(n_players):
            seats[i].append(pack.pop(0))
        dealer.append(pack.pop(0))
    up, hole = dealer[0], dealer[1]
    undealt = pack
    dealer_bj = sorted(dealer) == [1, 10]
    peek = up in (1, 10) and not dealer_bj
    public = {"up": up, "peek": peek, "n_players": n_players, "target_index": target_index}
    if dealer_bj:
        nets = [0.0 if sorted(hand) == [1, 10] else -1.0 for hand in seats]
        return _seated_result(nets, target_index, undealt, public, dealer_bj=True,
                              hole=hole, hole_revealed=True)

    pending = []
    nets = [None] * n_players
    for index, player in enumerate(seats):
        policy = target_policy if index == target_index else other_policy
        if sorted(player) == [1, 10]:
            nets[index] = 1.5
            continue
        player, action, stake, undealt = _play_unsplit(player, up, hole, undealt, policy,
                                                       peek, budget_seconds, surrender)
        if action == "surrender":
            nets[index] = -0.5
        elif _score(player) > 21:
            nets[index] = -float(stake)
        else:
            pending.append((index, player, stake))

    if pending:
        dealer_cards = [up, hole]
        while not _dealer_stands(dealer_cards):
            if not undealt:
                raise InsufficientCards("庄家尚需补牌但合成牌靴已耗尽")
            dealer_cards.append(undealt.pop(0))
        for index, player, stake in pending:
            nets[index] = float(_settle(player, dealer_cards, stake))
    return _seated_result(nets, target_index, undealt, public, dealer_bj=False,
                          hole=hole, hole_revealed=bool(pending))


def _play_unsplit(player, up, hole, undealt, policy, peek, budget_seconds, surrender=SURRENDER_UNSET):
    surrender = require_declared_surrender(surrender, what="多座位续玩")
    player = list(player)
    undealt = list(undealt)
    action = "stand"
    stake = 1
    if policy == CONSUMPTION_PI:
        counts = counts_from_values([hole, *undealt])
        action, _solved = choose_action(
            counts, player, up, peek, actions=legal_predeal_actions(surrender),
            budget_seconds=budget_seconds)
        if action == "surrender":
            return player, action, stake, undealt
        if action == "double":
            if not undealt:
                raise InsufficientCards("加倍时没有可补的牌")
            player.append(undealt.pop(0))
            return player, "double", 2, undealt
        if action == "hit":
            while True:
                if not undealt:
                    raise InsufficientCards("补牌时牌靴耗尽")
                player.append(undealt.pop(0))
                if _score(player) > 21:
                    break
                counts = counts_from_values([hole, *undealt])
                nxt, _solved = choose_action(counts, player, up, peek, actions=("stand", "hit"),
                                             budget_seconds=budget_seconds)
                action = nxt
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
                return player, action, stake, undealt
            if action == "double":
                if not undealt:
                    raise InsufficientCards("加倍时没有可补的牌")
                player.append(undealt.pop(0))
                return player, "double", 2, undealt
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
    return player, action, stake, undealt


def _seated_result(nets, target_index, remaining, public, *, dealer_bj, hole, hole_revealed):
    payload = {
        "net": nets[target_index],
        "target_net": nets[target_index],
        "other_nets": [value for index, value in enumerate(nets) if index != target_index],
        "n_players": public["n_players"],
        "target_index": target_index,
        "public": public,
        "dealer_bj": dealer_bj,
        "not_multiplayer_ev": True,
    }
    payload.update(observation_remainings(hole=hole, undealt=remaining, hole_revealed=hole_revealed))
    return payload


def run_round_window_study(*, n_decks=6, n_players=1, after_rounds=AFTER_ROUNDS, seed=1,
                           target_policy=CONSUMPTION_BASIC, other_policy=OTHER_STAND,
                           budget_seconds=5.0, play_budget_seconds=2.0, pack=None,
                           target_index=0, cut_remaining=None, surrender=SURRENDER_UNSET,
                           evaluation_method=EVALUATION_EXACT_SMALL, evaluation_policy_id=None,
                           mc_n_samples=64, mc_seed=None, mc_z=1.96):
    surrender = require_declared_surrender(surrender, what="前三/六轮消耗对照")
    if n_decks not in (6, 7, 8):
        raise ValueError("整靴研究只接受 6/7/8 副")
    checkpoints = tuple(after_rounds or AFTER_ROUNDS)
    if any(type(item) is not int or item < 1 for item in checkpoints):
        raise ValueError("对照轮数必须为正整数")
    eval_method = evaluation_method or EVALUATION_EXACT_SMALL
    if eval_method == EVALUATION_EXACT_SMALL:
        eval_policy = PREDEAL_STRATEGY_VERSION
    else:
        eval_policy = evaluation_policy_id or CONSUMPTION_STAND
    cut, cut_declared, cut_source = resolve_cut_remaining(
        n_decks, pack=pack, cut_remaining=cut_remaining)
    rng = Random(seed)
    current = list(pack) if pack is not None else full_pack(n_decks)
    rng.shuffle(current)
    history = []
    snapshots = []
    wanted = set(checkpoints)
    stop_reason = "complete"
    mc_base_seed = seed if mc_seed is None else mc_seed
    for index in range(max(wanted)):
        if cut > 0 and len(current) <= cut:
            stop_reason = "cut"
            break
        try:
            played = play_seated_round(
                current, n_players=n_players, target_index=target_index,
                target_policy=target_policy, other_policy=other_policy,
                budget_seconds=play_budget_seconds, surrender=surrender)
        except (InsufficientCards, CalculationStopped, ValueError) as error:
            history.append({"round_index": index, "play_error": str(error),
                            "physical_remaining": len(current)})
            stop_reason = "play_error"
            break
        current = list(played["remaining"])
        history.append({
            "round_index": index,
            "target_net": played["target_net"],
            "other_nets": list(played["other_nets"]),
            "physical_remaining": len(current),
        })
        if index + 1 in wanted:
            predeal = evaluate_checkpoint(
                current, surrender=surrender, evaluation_method=eval_method,
                evaluation_policy_id=evaluation_policy_id, budget_seconds=budget_seconds,
                mc_n_samples=mc_n_samples, mc_seed=mc_base_seed + index + 1, mc_z=mc_z,
                mc_play_budget_seconds=play_budget_seconds)
            predeal = dict(predeal)
            predeal["path_policy_id"] = target_policy
            predeal["cut_policy"] = {
                "cut_remaining": cut,
                "cut_declared": cut_declared,
                "cut_source": cut_source,
                "not_moved_to_last_cards": True,
            }
            snapshots.append({
                "after_rounds": index + 1,
                "n_players": n_players,
                "physical_remaining": len(current),
                "remaining_count": len(current),
                "predeal": predeal,
                "evaluation_method": eval_method,
                "evaluation_policy_id": eval_policy,
                "path_policy_id": target_policy,
                "target_net_sum": sum(item["target_net"] for item in history if "target_net" in item),
                "other_net_sum": sum(sum(item.get("other_nets") or ()) for item in history),
                "other_seat_count": n_players - 1,
                "not_fixed_card_subtraction": True,
            })
    scope = evaluation_scope([item["predeal"] for item in snapshots])
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "n_decks": n_decks,
        "n_players": n_players,
        "seed": seed,
        "sample_unit": "shoe",
        "not_independent_player_samples": True,
        "not_multiplayer_ev": True,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "current_hand_not_used_as_opening": True,
        "target_policy": target_policy,
        "other_policy": other_policy,
        "path_policy_id": target_policy,
        "path_policy_display": POLICY_DISPLAY.get(target_policy, target_policy),
        "evaluation_method": eval_method,
        "evaluation_policy_id": eval_policy,
        "methods_not_merged": True,
        "surrender": surrender,
        "cut_remaining": cut,
        "cut_declared": cut_declared,
        "cut_source": cut_source,
        "cut_policy": {
            "cut_remaining": cut,
            "cut_declared": cut_declared,
            "cut_source": cut_source,
            "not_moved_to_last_cards": True,
        },
        "stop_reason": stop_reason,
        "remaining_at_end": len(current),
        "rounds": history,
        "snapshots": snapshots,
        "summary": {
            "snapshot_count": len(snapshots),
            "complete_evaluation": scope["complete_evaluation"],
            "zero_window": scope["zero_window"],
            "no_positive_signal_detected": scope["no_positive_signal_detected"],
            "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
            "unavailable": scope["unavailable"],
            "positive": scope["positive"],
            "nonpositive": scope["nonpositive"],
            "evaluated_count": scope["evaluated_count"],
            "unassessable_count": scope["unassessable_count"],
            "verified_no_positive_over_declared_domain": scope["verified_no_positive_over_declared_domain"],
            "note": "3/6轮快照全部 unsupported/timeout 时不能写成已证明零窗口",
        },
        "note": "三/六轮是实际耗牌快照，不是固定减65/130张；其他座位只消耗牌，不是独立样本，也不是多玩家EV；"
                "玩具硬规则不是已核验基本策略；发牌前精确评估仍≤16；"
                "zero_window 只在快照全部可评且无正 EV 时为真",
    }
