"""Synthetic recording-error contrast for pre-deal windows.

The shuffle is known to the simulator. The observer signal uses only a lagged
or misread remaining pack. Independent unused video is still required before
any live-table window claim. Zero-window and disagreement are valid results.
"""
from __future__ import annotations

from random import Random

from .contracts import AVAILABLE
from .probability import CalculationStopped, InsufficientCards
from .research_windows import WINDOW_PRE_DEAL
from .shoe_windows import CONSUMPTION_PI, evaluate_predeal, play_round

SCHEMA = "hakimi-observation-error-study-v1"


def _positive(record):
    return record.get("status") == AVAILABLE and record.get("ev") is not None and record["ev"] > 0


def misread_one_rank(pack, rng):
    """Swap one remaining 10<->9. Persistent only for this snapshot, not a live model."""
    pack = list(pack)
    if not pack:
        return pack, {"kind": "none"}
    index = rng.randrange(len(pack))
    previous = pack[index]
    pack[index] = 9 if previous == 10 else 10
    return pack, {"kind": "rank_swap", "index": index, "from": previous, "to": pack[index]}


def run_observation_error_study(*, pack, seed=1, lag_rounds=1, budget_seconds=5.0,
                                max_rounds=40, play_budget_seconds=2.0,
                                play_policy=CONSUMPTION_PI):
    rng = Random(seed)
    current = list(pack)
    rng.shuffle(current)
    history = []
    rounds = []
    for index in range(max_rounds):
        if len(current) < 4:
            break
        truth = evaluate_predeal(current, budget_seconds=budget_seconds)
        if lag_rounds and len(history) >= lag_rounds:
            delay = evaluate_predeal(history[-lag_rounds], budget_seconds=budget_seconds)
        else:
            delay = {"status": "inapplicable", "reason_code": "OBSERVER_LAG", "ev": None,
                     "reason": "观察延迟：尚无足够已确认前缀，不能把当前手牌EV改称已抓住窗口"}
        noisy_pack, noise = misread_one_rank(current, Random(seed + index + 17))
        noisy = evaluate_predeal(noisy_pack, budget_seconds=budget_seconds)
        noisy["noise"] = noise
        history.append(list(current))
        realized, error = None, None
        try:
            played = play_round(current, policy=play_policy, budget_seconds=play_budget_seconds)
            realized = played["net"]
            current = list(played["remaining"])
        except (InsufficientCards, CalculationStopped, ValueError) as err:
            error = str(err)
        rounds.append({"round_index": index, "truth": truth, "delay": delay,
                       "rank_error": noisy, "realized_net": realized, "play_error": error})
        if error:
            break

    def confusion(observer_key):
        false_positive = false_negative = agree_positive = agree_negative = 0
        for item in rounds:
            truth_hit = _positive(item["truth"])
            obs_hit = _positive(item[observer_key])
            if truth_hit and obs_hit:
                agree_positive += 1
            elif (not truth_hit) and (not obs_hit):
                agree_negative += 1
            elif obs_hit and not truth_hit:
                false_positive += 1
            else:
                false_negative += 1
        return {"agree_positive": agree_positive, "agree_negative": agree_negative,
                "false_positive": false_positive, "false_negative": false_negative}

    truth_positive = sum(1 for item in rounds if _positive(item["truth"]))
    delay = confusion("delay")
    rank_error = confusion("rank_error")
    delay_missed = delay["false_negative"]
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "realized_path_is_not_counterfactual_truth": True,
        "seed": seed,
        "lag_rounds": lag_rounds,
        "consumption_policy": play_policy,
        "rounds": rounds,
        "summary": {
            "round_count": len(rounds),
            "truth_positive": truth_positive,
            "zero_window_truth": truth_positive == 0,
            "delay": delay,
            "rank_error": rank_error,
            "delay_missed_positive": delay_missed,
            "delay_missed_positive_rate": (delay_missed / truth_positive) if truth_positive else None,
            "rank_error_false_positive": rank_error["false_positive"],
            "rank_error_false_negative": rank_error["false_negative"],
            "note": "合成录牌误差对照，不是未使用真实录像；误报/漏报不能用来打开自动确认",
        },
    }
