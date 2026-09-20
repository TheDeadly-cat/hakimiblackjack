"""Interval research over candidate remaining packs. A mean shoe is not exact EV.

Unknown ranks or unknown removal counts stay a gap unless the feasible remaining
set is enumerated from origin knowledge, or the caller lists concrete packs.
A caller list never proves coverage. The published signal is the min/max of
available evaluations, never their average.

Separately optimized compositions are a diagnostic envelope, not a robust EV
under one information-feasible policy. A frozen π over a fully enumerated
feasible set is still not exact-optimal opening EV.
"""
from __future__ import annotations

from itertools import permutations

from .actions import (
    HIT_CONTINUATION_COMPOSITION, HIT_CONTINUATION_STAND, HIT_CONTINUATION_TOY,
)
from .contracts import AVAILABLE
from .predeal_contracts import SURRENDER_UNSET, legal_predeal_actions, require_declared_surrender
from .probability import CalculationStopped, InsufficientCards
from .research_windows import (
    WINDOW_CURRENT_HAND, WINDOW_PRE_DEAL, counts_from_values, require_count_vector,
    require_point_values,
)
from .shoe_windows import (
    CONSUMPTION_BASIC, CONSUMPTION_PI, CONSUMPTION_STAND, POLICY_DISPLAY,
    choose_action, evaluate_predeal, play_round,
)

SCHEMA = "hakimi-composition-interval-v1"
SCOPE_DIAGNOSTIC = "diagnostic_candidate_envelope"
SCOPE_COMMON_POLICY = "common_frozen_policy_envelope"
SCOPE_LISTED_MAXMIN = "listed_frozen_policy_maxmin"
SCOPE_INFOSET_MAXMIN = "infoset_first_action_maxmin"
HIT_CONTINUATIONS = (
    HIT_CONTINUATION_COMPOSITION,
    HIT_CONTINUATION_STAND,
    HIT_CONTINUATION_TOY,
)
COMMON_POLICY_MAX_REMAINING = 8
MAX_FEASIBLE_CANDIDATES = 256
COVERAGE_CALLER_LISTED = "caller_listed"
COVERAGE_FEASIBLE_ENUMERATION = "feasible_enumeration"
COMMON_POLICIES = {
    CONSUMPTION_STAND: POLICY_DISPLAY[CONSUMPTION_STAND],
    CONSUMPTION_BASIC: POLICY_DISPLAY[CONSUMPTION_BASIC],
}


class IntervalError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def refuse_mean_shoe():
    return {
        "schema": SCHEMA,
        "status": "unsupported",
        "reason_code": "MEAN_SHOE_FORBIDDEN",
        "scope": SCOPE_DIAGNOSTIC,
        "coverage_complete": False,
        "robust_signal_allowed": False,
        "forbids_mean_shoe": True,
        "ev": None,
        "published_point_ev": None,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "note": "未知组成不能用平均牌靴或单点期望冒充精确发牌前EV；须给出候选剩余组成并报告区间",
    }


def refuse_unknown_removal():
    return {
        "schema": SCHEMA,
        "status": "unsupported",
        "reason_code": "UNKNOWN_REMOVAL_GAP",
        "scope": SCOPE_DIAGNOSTIC,
        "coverage_complete": False,
        "robust_signal_allowed": False,
        "forbids_mean_shoe": True,
        "ev": None,
        "published_point_ev": None,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "note": "未知数量或未知牌面移除仍是缺口；须列出候选剩余组成，不能默认平均牌靴",
    }


def _require_interval_surrender(surrender, what):
    try:
        return require_declared_surrender(surrender, what=what)
    except ValueError as error:
        code = "SURRENDER_REQUIRED" if surrender is SURRENDER_UNSET else "SURRENDER_INVALID"
        raise IntervalError(code, str(error)) from error


def evaluate_interval(candidates, *, budget_seconds=5.0, coverage_complete=False,
                      surrender=SURRENDER_UNSET):
    if not candidates:
        raise IntervalError("CANDIDATES_MISSING", "区间研究必须给出候选剩余组成，不能默认平均牌靴")
    if coverage_complete not in (True, False):
        raise IntervalError("COVERAGE_FLAG", "coverage_complete 必须是布尔值，不能把未知覆盖写成已穷尽")
    try:
        surrender = _require_interval_surrender(surrender, "区间评估")
        legal = legal_predeal_actions(surrender)
    except IntervalError:
        raise
    except ValueError as error:
        raise IntervalError("SURRENDER_INVALID", str(error)) from error
    evaluated = []
    keys = []
    for pack in candidates:
        try:
            values = require_point_values(pack, what="区间候选剩余")
        except ValueError as error:
            raise IntervalError("CANDIDATE_INVALID", str(error)) from error
        keys.append(values)
        evaluated.append(evaluate_predeal(list(values), budget_seconds=budget_seconds,
                                          surrender=surrender))
    duplicate_candidates = len(keys) != len(set(keys))
    available = [item for item in evaluated
                 if item.get("status") == AVAILABLE and item.get("ev") is not None]
    missing = [
        {"index": index, "status": item.get("status"), "reason_code": item.get("reason_code"),
         "reason": item.get("reason")}
        for index, item in enumerate(evaluated)
        if item.get("status") != AVAILABLE or item.get("ev") is None
    ]
    evs = [item["ev"] for item in available]
    all_evaluated = bool(evaluated) and not missing
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "scope": SCOPE_DIAGNOSTIC,
        "surrender": surrender,
        "legal_actions": list(legal),
        "coverage_complete": False,
        "coverage_claimed_by_caller": bool(coverage_complete),
        "coverage_source": COVERAGE_CALLER_LISTED,
        "robust_signal_allowed": False,
        "not_a_reliable_window_claim": True,
        "forbids_mean_shoe": True,
        "independent_video": False,
        "duplicate_candidates": duplicate_candidates,
        "missing_or_failed": missing,
        "candidates": evaluated,
        "summary": {
            "n_candidates": len(evaluated),
            "n_available": len(available),
            "n_missing": len(missing),
            "ev_min": min(evs) if evs else None,
            "ev_max": max(evs) if evs else None,
            "interval_width": (max(evs) - min(evs)) if evs else None,
            "published_point_ev": None,
            "mean_ev_not_published": (sum(evs) / len(evs)) if evs else None,
            "diagnostic_all_available_nonpositive": all_evaluated and all(ev <= 0 for ev in evs),
            "diagnostic_all_available_positive": all_evaluated and all(ev > 0 for ev in evs),
            "zero_window_all": False,
            "verified_robust_positive_lower_bound": False,
            "verified_common_policy_positive_lower_bound": False,
            "note": "只公布可用EV的最小/最大，口径为诊断候选包络；平均值不是窗口信号；"
                    "调用方列表即使全部算成也不能证明已穷尽可行组成；分别优化的组成最优不是同一信息下的稳健π",
        },
    }


def _require_infoset(player, up, peek, surrender):
    try:
        surrender = require_declared_surrender(surrender, what="可见信息评估")
        player = require_point_values(player, what="可见玩家牌")
        if type(up) is not int or not 1 <= up <= 10:
            raise ValueError("明牌必须是点值 1–10 的整数")
        if type(peek) is not bool:
            raise ValueError("检查状态必须明确")
        legal = legal_predeal_actions(surrender)
    except ValueError as error:
        code = "SURRENDER_REQUIRED" if surrender is SURRENDER_UNSET else "INFOSET_INVALID"
        raise IntervalError(code, str(error)) from error
    if len(player) < 2:
        raise IntervalError("INFOSET_INVALID", "可见手牌至少两张")
    return player, up, peek, legal


def _compatible_remaining(pack, player, up):
    remaining = list(pack)
    try:
        for card in (*player, up):
            remaining.remove(card)
    except ValueError:
        return None
    return remaining


def composition_optimal_actions_at_infoset(candidates, player, up, peek, *,
                                           surrender=SURRENDER_UNSET, budget_seconds=2.0):
    """Separately optimized first action at one visible infoset.

    Remaining counts are the pre-deal pack minus the visible player cards and
    upcard; the hole stays unknown. Opposite actions mean the diagnostic
    envelope is not one information-feasible π.
    """
    player, up, peek, legal = _require_infoset(player, up, peek, surrender)
    rows = []
    available = []
    for index, pack in enumerate(candidates):
        try:
            values = list(require_point_values(pack, what="区间候选剩余"))
        except ValueError as error:
            rows.append({"index": index, "status": "invalid", "reason": str(error), "action": None})
            continue
        remaining = _compatible_remaining(values, player, up)
        if remaining is None:
            rows.append({"index": index, "status": "not_compatible",
                         "reason": "该组成不含当前可见牌", "action": None})
            continue
        try:
            action, solved = choose_action(
                counts_from_values(remaining), player, up, peek,
                actions=legal, budget_seconds=budget_seconds)
        except (InsufficientCards, CalculationStopped, ValueError) as error:
            rows.append({"index": index, "status": "unavailable",
                         "reason": str(error), "action": None})
            continue
        ev = solved["actions"][action]["ev"]
        rows.append({"index": index, "status": AVAILABLE, "action": action, "ev": ev})
        available.append(action)
    unique = tuple(dict.fromkeys(available))
    disagree = len(unique) > 1
    return {
        "infoset": {"player": player, "up": up, "peek": peek},
        "surrender": surrender,
        "legal_actions": list(legal),
        "rows": rows,
        "available_actions": list(unique),
        "disagree": disagree,
        "composition_optimal_is_not_information_feasible": True,
        "robust_signal_allowed": False,
        "verified_robust_positive_lower_bound": False,
        "window": WINDOW_CURRENT_HAND,
        "note": "分别优化的组成最优动作可以相反；不知道真实组成时不能执行这个包络",
    }


def evaluate_infoset_first_action_maxmin(
        candidates, player, up, peek, *,
        surrender=SURRENDER_UNSET, budget_seconds=2.0,
        hit_continuation=HIT_CONTINUATION_COMPOSITION):
    """max_a min_C of first-action EV at one visible infoset.

    The first action is the same for every remaining pack. Composition-optimal
    hit continuation still depends on remaining counts. always-stand / toy-hard
    continuations are information-feasible given that frozen chart, but are not
    a search over all information-feasible π. Current-hand EV is not next-round
    opening EV.
    """
    if not candidates:
        raise IntervalError("CANDIDATES_MISSING", "可见信息 maxmin 必须给出候选剩余组成，不能默认平均牌靴")
    if hit_continuation not in HIT_CONTINUATIONS:
        raise IntervalError("HIT_CONTINUATION_INVALID", "补牌后续只接受组成最优、冻结停牌或玩具硬规则")
    player, up, peek, legal = _require_infoset(player, up, peek, surrender)
    rows = []
    available = []
    failed = []
    for index, pack in enumerate(candidates):
        try:
            values = list(require_point_values(pack, what="区间候选剩余"))
        except ValueError as error:
            row = {"index": index, "status": "invalid", "reason": str(error), "action": None,
                   "actions": {}}
            rows.append(row)
            failed.append(row)
            continue
        remaining = _compatible_remaining(values, player, up)
        if remaining is None:
            rows.append({"index": index, "status": "not_compatible",
                         "reason": "该组成不含当前可见牌", "action": None, "actions": {}})
            continue
        try:
            action, solved = choose_action(
                counts_from_values(remaining), player, up, peek,
                actions=legal, budget_seconds=budget_seconds,
                hit_continuation=hit_continuation)
        except (InsufficientCards, CalculationStopped, ValueError) as error:
            row = {"index": index, "status": "unavailable", "reason": str(error),
                   "action": None, "actions": {}}
            rows.append(row)
            failed.append(row)
            continue
        action_evs = {name: item["ev"] for name, item in solved["actions"].items()}
        row = {"index": index, "status": AVAILABLE, "action": action, "ev": action_evs[action],
               "actions": action_evs}
        rows.append(row)
        available.append(row)
    unique = tuple(dict.fromkeys(row["action"] for row in available))
    disagree = len(unique) > 1
    action_mins = {}
    complete = bool(available) and not failed
    if complete:
        for action in legal:
            if any(action not in row["actions"] for row in available):
                continue
            action_mins[action] = min(row["actions"][action] for row in available)
    maxmin_ev = max(action_mins.values()) if action_mins else None
    chosen = None
    if maxmin_ev is not None:
        chosen = next(action for action in legal
                      if action in action_mins and action_mins[action] == maxmin_ev)
    composition_continuation = hit_continuation == HIT_CONTINUATION_COMPOSITION
    if composition_continuation:
        continuation_note = "补牌后的续玩仍按各组成最优，因此不是全部信息可行π的稳健下界"
    else:
        continuation_note = (
            f"补牌后续使用冻结规则 {hit_continuation}，同一可见信息下动作相同，"
            "但仍只是所列第一动作与该续玩的 maxmin，不是全部信息可行π的稳健下界"
        )
    return {
        "schema": SCHEMA,
        "window": WINDOW_CURRENT_HAND,
        "scope": SCOPE_INFOSET_MAXMIN,
        "method": "infoset_first_action_maxmin",
        "infoset": {"player": player, "up": up, "peek": peek},
        "surrender": surrender,
        "legal_actions": list(legal),
        "hit_continuation": hit_continuation,
        "not_exact_optimal": True,
        "hit_continuation_is_composition_optimal": composition_continuation,
        "hit_continuation_is_information_feasible": not composition_continuation,
        "composition_optimal_is_not_information_feasible": True,
        "coverage_complete": False,
        "coverage_source": COVERAGE_CALLER_LISTED,
        "robust_signal_allowed": False,
        "not_a_reliable_window_claim": True,
        "forbids_mean_shoe": True,
        "independent_video": False,
        "rows": rows,
        "available_actions": list(unique),
        "disagree": disagree,
        "action_mins": action_mins,
        "infoset_maxmin_ev": maxmin_ev,
        "chosen_action": chosen,
        "summary": {
            "n_candidates": len(rows),
            "n_available": len(available),
            "n_failed": len(failed),
            "n_not_compatible": sum(1 for row in rows if row["status"] == "not_compatible"),
            "infoset_maxmin_ev": maxmin_ev,
            "chosen_action": chosen,
            "hit_continuation": hit_continuation,
            "published_point_ev": None,
            "zero_window_all": False,
            "verified_robust_positive_lower_bound": False,
            "verified_infoset_maxmin_positive_lower_bound": False,
            "note": "第一动作只依赖可见牌；" + continuation_note +
                    "；这是当前手条件优势，不是发牌前开局优势",
        },
    }


def exact_common_policy_ev(pack, *, policy=CONSUMPTION_STAND, surrender=SURRENDER_UNSET):
    """Exact mean net of one next round under a frozen π. Not composition-optimal EV."""
    surrender = _require_interval_surrender(surrender, "共同π评估")
    if policy not in COMMON_POLICIES:
        raise IntervalError("UNKNOWN_POLICY", f"共同π只接受冻结停牌或玩具硬规则，不能用 {policy!r}")
    values = tuple(require_point_values(pack, what="共同π候选剩余"))
    if len(values) > COMMON_POLICY_MAX_REMAINING:
        raise IntervalError(
            "COMMON_POLICY_TOO_LARGE",
            f"共同π精确枚举仅用于剩余≤{COMMON_POLICY_MAX_REMAINING}张；更大组成请用固定策略MC，不能改精确16张门槛",
        )
    if len(values) < 4:
        raise IntervalError("COMMON_POLICY_TOO_SMALL", "共同π评估至少需要4张剩余")
    pays = []
    failures = []
    for order in permutations(values):
        try:
            played = play_round(list(order), policy=policy, surrender=surrender)
            pays.append(float(played["net"]))
        except (InsufficientCards, CalculationStopped, ValueError) as error:
            failures.append(str(error))
    n_ok = len(pays)
    n_failed = len(failures)
    complete = n_failed == 0 and n_ok > 0
    return {
        "policy_id": policy,
        "policy_note": COMMON_POLICIES[policy],
        "physical_remaining": len(values),
        "n_permutations": n_ok + n_failed,
        "n_ok": n_ok,
        "n_failed": n_failed,
        "status": "available" if complete else "indeterminate",
        "ev": (sum(pays) / n_ok) if n_ok else None,
        "not_exact_optimal": True,
    }


def evaluate_common_policy_interval(candidates, *, policy=CONSUMPTION_STAND,
                                    coverage_complete=False, surrender=SURRENDER_UNSET):
    """Same frozen π on every listed composition. Not a robust bound on optimal EV."""
    if policy == CONSUMPTION_PI:
        raise IntervalError("OPTIMAL_NOT_COMMON_POLICY",
                            "组成最优不是共同可执行π；请用冻结停牌或玩具硬规则")
    if not candidates:
        raise IntervalError("CANDIDATES_MISSING", "共同π区间必须给出候选剩余组成，不能默认平均牌靴")
    if coverage_complete not in (True, False):
        raise IntervalError("COVERAGE_FLAG", "coverage_complete 必须是布尔值，不能把未知覆盖写成已穷尽")
    surrender = _require_interval_surrender(surrender, "共同π区间")
    evaluated = []
    keys = []
    for pack in candidates:
        try:
            values = tuple(require_point_values(pack, what="共同π候选剩余"))
        except ValueError as error:
            raise IntervalError("CANDIDATE_INVALID", str(error)) from error
        keys.append(values)
        evaluated.append(exact_common_policy_ev(values, policy=policy, surrender=surrender))
    duplicate_candidates = len(keys) != len(set(keys))
    available = [item for item in evaluated
                 if item.get("status") == "available" and item.get("ev") is not None]
    missing = [
        {"index": index, "status": item.get("status"), "n_failed": item.get("n_failed")}
        for index, item in enumerate(evaluated)
        if item.get("status") != "available" or item.get("ev") is None
    ]
    evs = [item["ev"] for item in available]
    all_evaluated = bool(evaluated) and not missing
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "scope": SCOPE_COMMON_POLICY,
        "method": "exact_common_frozen_policy",
        "policy_id": policy,
        "policy_note": COMMON_POLICIES[policy],
        "path_policy_id": policy,
        "evaluation_policy_id": policy,
        "surrender": surrender,
        "not_exact_optimal": True,
        "coverage_complete": False,
        "coverage_claimed_by_caller": bool(coverage_complete),
        "coverage_source": COVERAGE_CALLER_LISTED,
        "robust_signal_allowed": False,
        "not_a_reliable_window_claim": True,
        "forbids_mean_shoe": True,
        "independent_video": False,
        "duplicate_candidates": duplicate_candidates,
        "missing_or_failed": missing,
        "candidates": evaluated,
        "summary": {
            "n_candidates": len(evaluated),
            "n_available": len(available),
            "n_missing": len(missing),
            "ev_min": min(evs) if evs else None,
            "ev_max": max(evs) if evs else None,
            "interval_width": (max(evs) - min(evs)) if evs else None,
            "published_point_ev": None,
            "mean_ev_not_published": (sum(evs) / len(evs)) if evs else None,
            "zero_window_all": False,
            "verified_robust_positive_lower_bound": False,
            "verified_common_policy_positive_lower_bound": False,
            "note": "同一冻结π在所列候选上的精确均值包络；不是组成最优；"
                    "调用方列表不能证明已穷尽当时知识下的全部可行组成",
        },
    }


def evaluate_listed_policy_maxmin(candidates, policies=None, *, coverage_complete=False,
                                  surrender=SURRENDER_UNSET):
    """max_π min_C over a listed frozen-policy set. Not a bound on all information-feasible π."""
    policies = tuple(policies or (CONSUMPTION_STAND, CONSUMPTION_BASIC))
    if not policies:
        raise IntervalError("POLICIES_MISSING", "所列π maxmin 必须给出冻结策略，不能默认可执行全体")
    if CONSUMPTION_PI in policies:
        raise IntervalError("OPTIMAL_NOT_COMMON_POLICY",
                            "组成最优不是共同可执行π；所列 maxmin 只接受冻结停牌或玩具硬规则")
    surrender = _require_interval_surrender(surrender, "所列π maxmin")
    arms = []
    for policy in policies:
        report = evaluate_common_policy_interval(
            candidates, policy=policy, coverage_complete=coverage_complete, surrender=surrender)
        ev_min = report["summary"]["ev_min"]
        complete_arm = (report["summary"]["n_missing"] == 0
                        and report["summary"]["n_available"] == report["summary"]["n_candidates"]
                        and ev_min is not None)
        arms.append({
            "policy_id": report["policy_id"],
            "policy_note": report["policy_note"],
            "ev_min": ev_min,
            "ev_max": report["summary"]["ev_max"],
            "n_available": report["summary"]["n_available"],
            "n_missing": report["summary"]["n_missing"],
            "complete": complete_arm,
            "report": report,
        })
    complete_mins = [arm["ev_min"] for arm in arms if arm["complete"]]
    listed_maxmin = max(complete_mins) if complete_mins and len(complete_mins) == len(arms) else None
    chosen = None
    if listed_maxmin is not None:
        chosen = next(arm["policy_id"] for arm in arms if arm["ev_min"] == listed_maxmin)
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "scope": SCOPE_LISTED_MAXMIN,
        "method": "listed_frozen_policy_maxmin",
        "surrender": surrender,
        "policies": list(policies),
        "not_exact_optimal": True,
        "coverage_complete": False,
        "coverage_claimed_by_caller": bool(coverage_complete),
        "coverage_source": COVERAGE_CALLER_LISTED,
        "robust_signal_allowed": False,
        "not_a_reliable_window_claim": True,
        "forbids_mean_shoe": True,
        "independent_video": False,
        "listed_maxmin_ev": listed_maxmin,
        "chosen_policy_id": chosen,
        "arms": [{key: value for key, value in arm.items() if key != "report"} for arm in arms],
        "arm_reports": [arm["report"] for arm in arms],
        "summary": {
            "n_policies": len(arms),
            "n_complete_policies": len(complete_mins),
            "listed_maxmin_ev": listed_maxmin,
            "published_point_ev": None,
            "zero_window_all": False,
            "verified_robust_positive_lower_bound": False,
            "verified_listed_maxmin_positive_lower_bound": False,
            "verified_common_policy_positive_lower_bound": False,
            "note": "max_π min_C 只在所列冻结π上成立；分别优化的组成最优和未列出的信息可行π都不在这个下界里",
        },
    }


def origin_count_vector(*, n_decks=None, origin_counts=None, origin_pack=None):
    """Ten-bucket origin. A 6/7/8-deck shoe, an explicit count vector, or a small pack."""
    specified = [item is not None for item in (n_decks, origin_counts, origin_pack)]
    if sum(specified) != 1:
        raise IntervalError("ORIGIN_CONFLICT", "起源必须是副数、十桶或点值包三者之一，不能混用或缺省成平均牌靴")
    if origin_pack is not None:
        return counts_from_values(require_point_values(origin_pack, what="区间起源剩余"))
    if origin_counts is not None:
        return require_count_vector(origin_counts, what="区间起源十桶")
    if n_decks not in (6, 7, 8):
        raise IntervalError("ILLEGAL_DECKS", "整靴起源只接受 6/7/8 副")
    return tuple([4 * n_decks] * 9 + [16 * n_decks])


def pack_from_counts(counts):
    counts = require_count_vector(counts, what="可行组成十桶")
    pack = []
    for value, n in enumerate(counts, start=1):
        pack.extend([value] * n)
    return tuple(pack)


def _require_known_remaining(known, origin):
    if known is None:
        return tuple(None for _ in range(10))
    try:
        items = tuple(known)
    except TypeError as error:
        raise IntervalError("KNOWN_REMAINING_INVALID", "已知剩余必须是十个整数或未知桶") from error
    if len(items) != 10:
        raise IntervalError("KNOWN_REMAINING_INVALID", "已知剩余必须是十个整数或未知桶")
    out = []
    for index, value in enumerate(items):
        if value is None:
            out.append(None)
            continue
        if type(value) is not int or value < 0:
            raise IntervalError("KNOWN_REMAINING_INVALID",
                                f"已知剩余必须是非负整数或未知；不能把 {value!r} 截成整数")
        if value > origin[index]:
            raise IntervalError("KNOWN_REMAINING_INVALID", "已知剩余不能超过起源容量")
        out.append(value)
    return tuple(out)


def enumerate_feasible_remaining(*, remaining_total, n_decks=None, origin_counts=None,
                                 origin_pack=None, known_remaining=None,
                                 max_candidates=MAX_FEASIBLE_CANDIDATES):
    """All remaining count vectors consistent with origin, total, and known ranks.

    Truncation is a DFS prefix, not a statistical sample of the feasible set.
    """
    if type(remaining_total) is not int or remaining_total < 0:
        raise IntervalError("ILLEGAL_TOTAL", "剩余张数必须是非负整数；不能把浮点或布尔截成整数")
    if type(max_candidates) is not int or max_candidates < 1:
        raise IntervalError("ILLEGAL_CAP", "可行组成上限必须是正整数")
    origin = origin_count_vector(n_decks=n_decks, origin_counts=origin_counts, origin_pack=origin_pack)
    if remaining_total > sum(origin):
        raise IntervalError("ILLEGAL_TOTAL", "剩余张数不能超过起源总张数")
    known = _require_known_remaining(known_remaining, origin)
    known_sum = sum(value for value in known if value is not None)
    if known_sum > remaining_total:
        return {
            "origin_counts": origin,
            "remaining_total": remaining_total,
            "known_remaining": known,
            "counts": [],
            "n_found": 0,
            "truncated": False,
            "coverage_complete": True,
            "enumeration_order": "rank_dfs_not_a_sample",
            "max_candidates": max_candidates,
            "note": "已知剩余之和已超过声明总张数，可行集为空",
        }
    max_after = [0] * 11
    min_after = [0] * 11
    for rank in range(9, -1, -1):
        if known[rank] is None:
            max_here, min_here = origin[rank], 0
        else:
            max_here = min_here = known[rank]
        max_after[rank] = max_after[rank + 1] + max_here
        min_after[rank] = min_after[rank + 1] + min_here
    results = []
    truncated = False

    def rec(rank, left, acc):
        nonlocal truncated
        if truncated:
            return
        if rank == 10:
            if left == 0:
                if len(results) == max_candidates:
                    truncated = True
                    return
                results.append(tuple(acc))
            return
        if known[rank] is not None:
            value = known[rank]
            nxt = left - value
            if nxt < 0 or nxt > max_after[rank + 1] or nxt < min_after[rank + 1]:
                return
            acc.append(value)
            rec(rank + 1, nxt, acc)
            acc.pop()
            return
        hi = min(origin[rank], left)
        for value in range(0, hi + 1):
            nxt = left - value
            if nxt > max_after[rank + 1] or nxt < min_after[rank + 1]:
                continue
            acc.append(value)
            rec(rank + 1, nxt, acc)
            acc.pop()
            if truncated:
                return

    rec(0, remaining_total, [])
    complete = not truncated
    return {
        "origin_counts": origin,
        "remaining_total": remaining_total,
        "known_remaining": known,
        "counts": results,
        "n_found": len(results),
        "truncated": truncated,
        "coverage_complete": complete,
        "enumeration_order": "rank_dfs_not_a_sample",
        "max_candidates": max_candidates,
        "note": ("已穷尽当时知识下的可行剩余十桶" if complete
                 else "搜索在上限处截断；前缀不是可行集样本，不能当覆盖完整"),
    }


def evaluate_feasible_interval(*, remaining_total, n_decks=None, origin_counts=None,
                               origin_pack=None, known_remaining=None,
                               budget_seconds=5.0, max_candidates=MAX_FEASIBLE_CANDIDATES,
                               common_policy=None, surrender=SURRENDER_UNSET):
    """Evaluate every enumerated feasible remaining pack. Caller lists cannot do this."""
    surrender = _require_interval_surrender(surrender, "可行组成区间")
    enumeration = enumerate_feasible_remaining(
        remaining_total=remaining_total, n_decks=n_decks, origin_counts=origin_counts,
        origin_pack=origin_pack, known_remaining=known_remaining,
        max_candidates=max_candidates)
    packs = [pack_from_counts(item) for item in enumeration["counts"]]
    if not packs:
        raise IntervalError("NO_FEASIBLE", "当时知识下没有可行剩余组成；不能默认平均牌靴")
    if common_policy is not None:
        report = evaluate_common_policy_interval(
            packs, policy=common_policy, coverage_complete=False, surrender=surrender)
    else:
        report = evaluate_interval(packs, budget_seconds=budget_seconds, coverage_complete=False,
                                   surrender=surrender)
    report = dict(report)
    summary = dict(report.get("summary") or {})
    enumerated_complete = bool(enumeration["coverage_complete"])
    all_evaluated = summary.get("n_missing") == 0 and summary.get("n_available") == len(packs)
    coverage_complete = enumerated_complete and all_evaluated and not report.get("duplicate_candidates")
    evs = [item["ev"] for item in report.get("candidates") or ()
           if item.get("ev") is not None and item.get("status") in ("available", AVAILABLE)]
    common_positive = (
        common_policy is not None and coverage_complete and bool(evs) and all(ev > 0 for ev in evs)
    )
    common_nonpositive = (
        common_policy is not None and coverage_complete and bool(evs) and all(ev <= 0 for ev in evs)
    )
    summary["verified_robust_positive_lower_bound"] = False
    summary["verified_common_policy_positive_lower_bound"] = common_positive
    summary["verified_common_policy_nonpositive_upper_bound"] = common_nonpositive
    summary["zero_window_all"] = False
    summary["note"] = (
        "可行组成由当时知识枚举，不是调用方口头声称覆盖；分别优化仍不是稳健最优；"
        "同一冻结π且枚举完整时，正下界只对该π成立，不是精确最优开局优势"
        if common_policy is not None else
        "可行组成由当时知识枚举；分别优化的组成最优仍不是同一信息下的稳健π"
    )
    report["summary"] = summary
    report["coverage_complete"] = coverage_complete
    report["coverage_claimed_by_caller"] = False
    report["coverage_source"] = COVERAGE_FEASIBLE_ENUMERATION
    report["feasible_enumeration"] = enumeration
    report["robust_signal_allowed"] = False
    report["not_a_reliable_window_claim"] = True
    return report
