"""Frozen-policy Monte Carlo for 6/7/8-deck remaining compositions.

This evaluates an executable policy π, not composition-optimal unsplit EV.
The interactive exact_small entry stays capped at 16 cards and 5 seconds.
Offline callers pass their own sample count and optional wall-clock budget.
"""
from __future__ import annotations

import math
import os
import sys
from math import sqrt
from random import Random
from statistics import mean, stdev
from time import perf_counter, time

from .bounded_mean import bounded_mean_interval
from .predeal_contracts import (
    PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION, SURRENDER_UNSET,
    require_declared_surrender,
)
from .contracts import digest
from .probability import CalculationStopped, InsufficientCards
from .research_windows import WINDOW_PRE_DEAL, counts_from_values, require_point_values, window_state
from .shoe_windows import (
    CONSUMPTION_BASIC, CONSUMPTION_LEGAL_UNSPLIT, CONSUMPTION_PI, CONSUMPTION_STAND,
    full_pack, play_round,
)

SCHEMA = "hakimi-fixed-policy-mc-v1"
METHOD = "fixed_policy_monte_carlo"
POLICY_ALWAYS_STAND = CONSUMPTION_STAND
POLICY_TOY_HARD = CONSUMPTION_BASIC
POLICY_LEGAL_UNSPLIT = CONSUMPTION_LEGAL_UNSPLIT
INPUT_SCOPE_FIXED_COMPOSITION = "fixed_composition"
INPUT_SCOPE_REMAINING_COUNT_PRIOR = "remaining_count_prior"
# Conservative legal net-pay support for the current unsplit, no-insurance,
# at-most-one-double, 3:2 S17 model. Other games must pass their own bounds.
UNSPLIT_NO_INSURANCE_DOUBLE_PAY_LOW = -2.0
UNSPLIT_NO_INSURANCE_DOUBLE_PAY_HIGH = 2.0
FORMAL_CI_METHOD = "hoeffding_fixed_n_finite_family_v1"
WALD_CI_METHOD = "wald_normal_mean_sample_sd"
SUPPORTED_POLICIES = {
    POLICY_ALWAYS_STAND: "冻结停牌消耗；不是最优策略",
    POLICY_TOY_HARD: "玩具硬点数消耗；不是已核验基本策略表",
    POLICY_LEGAL_UNSPLIT: (
        "冻结合法未分牌S17：硬点、软点、允许时加倍、明确允许的晚投降；"
        "不分牌、不买保险；不是组成最优，也不是已核验赌场基本策略表"
    ),
}


class FixedPolicyError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _peak_rss_bytes():
    try:
        import resource
        rss = int(getattr(resource.getrusage(resource.RUSAGE_SELF), "ru_maxrss", 0) or 0)
        if rss > 0:
            return rss if sys.platform == "darwin" else rss * 1024
    except (ImportError, OSError, ValueError, TypeError):
        pass
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        getter = getattr(kernel32, "K32GetProcessMemoryInfo", None)
        if getter is None:
            getter = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        getter.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        getter.restype = wintypes.BOOL
        get_process = kernel32.GetCurrentProcess
        get_process.restype = wintypes.HANDLE
        if not getter(get_process(), ctypes.byref(counters), counters.cb):
            return None
        peak = int(counters.PeakWorkingSetSize)
        return peak if peak > 0 else None
    except (AttributeError, OSError, ValueError, TypeError):
        return None


def _require_positive_int(value, name):
    if type(value) is not int or value < 1:
        raise FixedPolicyError("ILLEGAL_INT", f"{name}必须是正整数；不能把 {value!r} 截成整数")
    return value


def _require_finite_number(value, name, *, positive=False):
    if type(value) is bool:
        raise FixedPolicyError("ILLEGAL_NUMBER", f"{name}不能是布尔值")
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise FixedPolicyError("ILLEGAL_NUMBER", f"{name}必须是有限数") from error
    if not math.isfinite(number):
        raise FixedPolicyError("ILLEGAL_NUMBER", f"{name}必须是有限数")
    if positive and number <= 0:
        raise FixedPolicyError("ILLEGAL_NUMBER", f"{name}必须为正数")
    return number


def _require_nonneg_int(value, name):
    if type(value) is not int or value < 0:
        raise FixedPolicyError("ILLEGAL_INT", f"{name}必须是非负整数；不能把 {value!r} 截成整数")
    return value


def _policy_id(policy):
    if policy == CONSUMPTION_PI or policy == PREDEAL_STRATEGY_VERSION:
        raise FixedPolicyError(
            "OPTIMAL_POLICY_NOT_MC",
            "精确未分牌最优不是本离线MC方法；请用冻结停牌、玩具硬规则或合法未分牌S17，小牌靴仍走 exact_small",
        )
    if policy not in SUPPORTED_POLICIES:
        raise FixedPolicyError("UNKNOWN_POLICY", f"未声明的冻结策略: {policy!r}")
    return policy


def _input_scope(*, pack, remaining, n_decks):
    if pack is not None:
        return INPUT_SCOPE_FIXED_COMPOSITION
    full = len(full_pack(n_decks)) if n_decks in (6, 7, 8) else None
    if remaining is None or remaining == full:
        return INPUT_SCOPE_FIXED_COMPOSITION
    return INPUT_SCOPE_REMAINING_COUNT_PRIOR


def _net_summary(pays):
    n = len(pays)
    buckets = {}
    win = push = lose = 0
    for pay in pays:
        key = f"{float(pay):.12g}"
        buckets[key] = buckets.get(key, 0) + 1
        if pay > 0:
            win += 1
        elif pay < 0:
            lose += 1
        else:
            push += 1
    probabilities = {key: count / n for key, count in buckets.items()} if n else {}
    return {
        "net_pay_counts": buckets,
        "net_distribution": probabilities,
        "p_win": (win / n) if n else None,
        "p_push": (push / n) if n else None,
        "p_lose": (lose / n) if n else None,
        "n": n,
    }


def _rules_snapshot(n_decks, surrender, input_scope, pay_low, pay_high):
    return {
        "dealer_soft17": "S17",
        "blackjack_payout": [3, 2],
        "american_hole_card": True,
        "surrender": surrender,
        "split": False,
        "insurance": False,
        "double_after_split": False,
        "n_decks": n_decks,
        "input_scope": input_scope,
        "payoff_support": [pay_low, pay_high],
        "payoff_support_source": "unsplit_no_insurance_single_double_3to2_conservative",
    }


def _sample_pack(*, n_decks, remaining, pack, rng):
    if pack is not None:
        values = list(require_point_values(pack, what="冻结策略MC剩余"))
        rng.shuffle(values)
        return values
    if n_decks not in (6, 7, 8):
        raise FixedPolicyError("ILLEGAL_DECKS", "整靴固定策略评估只接受 6/7/8 副")
    shoe = full_pack(n_decks)
    size = len(shoe) if remaining is None else remaining
    if type(size) is not int or size < 4:
        raise FixedPolicyError("ILLEGAL_REMAINING", "采样剩余必须是≥4的整数")
    if size > len(shoe):
        raise FixedPolicyError("ILLEGAL_REMAINING", "采样张数超过整靴")
    values = rng.sample(shoe, size)
    rng.shuffle(values)
    return values


def _parent_composition(*, n_decks, remaining, pack):
    if pack is not None:
        values = list(require_point_values(pack, what="冻结策略MC剩余"))
        return values, list(counts_from_values(values)), len(values), None
    if n_decks not in (6, 7, 8):
        raise FixedPolicyError("ILLEGAL_DECKS", "整靴固定策略评估只接受 6/7/8 副")
    values = full_pack(n_decks)
    size = len(values) if remaining is None else remaining
    if type(size) is not int or size < 4:
        raise FixedPolicyError("ILLEGAL_REMAINING", "采样剩余必须是≥4的整数")
    if size > len(values):
        raise FixedPolicyError("ILLEGAL_REMAINING", "采样张数超过整靴")
    return values, list(counts_from_values(values)), size, n_decks


def evaluate_fixed_policy(*, n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=1024, seed=1,
                          remaining=None, pack=None, surrender=SURRENDER_UNSET, cancelled=None,
                          budget_seconds=None, play_budget_seconds=2.0, z=1.96, alpha=0.05,
                          family_size=1, pay_low=UNSPLIT_NO_INSURANCE_DOUBLE_PAY_LOW,
                          pay_high=UNSPLIT_NO_INSURANCE_DOUBLE_PAY_HIGH,
                          numerical_tolerance=1e-10):
    """Independent Monte Carlo of one next round under a frozen unsplit policy.

    `budget_seconds` is an offline wall-clock bound, not the interactive 5s exact cap.
    Failed or unrun samples stay in the denominator. The successful-subsample mean
    is not the planned-population EV. A statistical sign claim is not exact-optimal,
    not timely, and not desktop-attested.
    """
    policy = _policy_id(policy)
    n_samples = _require_positive_int(n_samples, "样本数")
    seed = _require_nonneg_int(seed, "种子")
    z_value = _require_finite_number(z, "区间z", positive=True)
    alpha_value = _require_finite_number(alpha, "family_alpha", positive=True)
    if not 0 < alpha_value < 1:
        raise FixedPolicyError("ILLEGAL_NUMBER", "family_alpha 必须在 (0,1)")
    family_size = _require_positive_int(family_size, "family_size")
    pay_low = _require_finite_number(pay_low, "收益下界")
    pay_high = _require_finite_number(pay_high, "收益上界")
    if pay_low >= pay_high:
        raise FixedPolicyError("ILLEGAL_NUMBER", "收益界必须严格有序，且来自规则而不是样本极值")
    band = _require_finite_number(numerical_tolerance, "numerical_tolerance")
    if band < 0:
        raise FixedPolicyError("ILLEGAL_NUMBER", "numerical_tolerance 必须非负")
    try:
        play_budget_seconds = _require_finite_number(play_budget_seconds, "单局预算", positive=True)
        if budget_seconds is not None:
            budget_seconds = _require_finite_number(budget_seconds, "离线墙钟预算", positive=True)
    except FixedPolicyError as error:
        if error.code == "ILLEGAL_NUMBER":
            raise FixedPolicyError("ILLEGAL_BUDGET", str(error)) from error
        raise
    try:
        surrender = require_declared_surrender(surrender, what="固定策略MC")
    except ValueError as error:
        raise FixedPolicyError("SURRENDER_REQUIRED", str(error)) from error
    parent, composition_counts, physical, decks_out = _parent_composition(
        n_decks=n_decks, remaining=remaining, pack=pack)
    input_scope = _input_scope(pack=pack, remaining=remaining, n_decks=n_decks)
    rules = _rules_snapshot(decks_out, surrender, input_scope, pay_low, pay_high)
    sample_plan = {
        "n_samples_planned": n_samples,
        "seed": seed,
        "ci_z": z_value,
        "alpha_family": alpha_value,
        "family_size": family_size,
        "payoff_support": [pay_low, pay_high],
        "input_scope": input_scope,
        "draw_size": physical,
        "play_budget_seconds": play_budget_seconds,
        "offline_budget_seconds": budget_seconds,
    }
    rules_digest = digest(rules)
    policy_digest = digest({
        "policy_id": policy,
        "policy_note": SUPPORTED_POLICIES[policy],
        "split": False,
        "insurance": False,
    })
    algorithm_digest = digest({
        "method": METHOD,
        "ci_method": FORMAL_CI_METHOD,
        "diagnostic_ci_method": WALD_CI_METHOD,
        "sample_plan": sample_plan,
    })
    rng = Random(seed)
    pays = []
    failures = []
    stopped = None
    cancel_noticed_at = None
    start = perf_counter()
    for index in range(n_samples):
        if cancelled is not None and cancelled():
            cancel_noticed_at = perf_counter()
            stopped = "cancelled"
            break
        if budget_seconds is not None and perf_counter() - start >= budget_seconds:
            stopped = "timeout"
            break
        try:
            shoe = _sample_pack(n_decks=n_decks, remaining=remaining, pack=pack, rng=rng)
            played = play_round(shoe, policy=policy, budget_seconds=play_budget_seconds,
                                surrender=surrender)
            pays.append(float(played["net"]))
        except (InsufficientCards, CalculationStopped, ValueError) as error:
            failures.append({
                "sample_index": index,
                "exception": type(error).__name__,
                "message": str(error),
            })
    elapsed = perf_counter() - start
    n_ok = len(pays)
    n_failed = len(failures)
    n_not_run = n_samples - n_ok - n_failed
    complete = stopped is None and n_failed == 0 and n_not_run == 0 and n_ok == n_samples
    subsample_mean = mean(pays) if pays else None
    subsample_std = stdev(pays) if n_ok >= 2 else None
    subsample_se = (subsample_std / sqrt(n_ok)) if subsample_std is not None else None
    population_ev = subsample_mean if complete else None
    sample_std = subsample_std if complete else None
    se = subsample_se if complete else None
    lo = (population_ev - z_value * se) if se is not None else None
    hi = (population_ev + z_value * se) if se is not None else None
    wald_degenerate = bool(complete and (se is None or se == 0))
    nets = _net_summary(pays) if complete else {
        "net_pay_counts": {},
        "net_distribution": {},
        "p_win": None,
        "p_push": None,
        "p_lose": None,
        "n": n_ok,
    }
    try:
        formal = bounded_mean_interval(
            pays, planned_n=n_samples, n_failed=n_failed, n_not_run=n_not_run,
            lower_payoff=pay_low, upper_payoff=pay_high, alpha_family=alpha_value,
            family_size=family_size, positive_threshold=band,
            fixed_composition=input_scope == INPUT_SCOPE_FIXED_COMPOSITION,
            fixed_policy=True, iid_design_declared=True)
    except ValueError as error:
        raise FixedPolicyError("ILLEGAL_PAYOFF_SUPPORT", str(error)) from error
    statistical_positive = bool(formal["statistical_positive"])
    statistical_nonpositive = bool(formal["statistical_nonpositive"])
    window_claim_allowed = bool(formal["window_claim_allowed"])
    formal_lo = formal.get("ci_low")
    formal_hi = formal.get("ci_high")
    if complete and window_claim_allowed and statistical_positive:
        sign_status = "bounded_ci_excludes_zero_positive"
        sign_reason = (
            "固定组成、预注册样本完成且有界Hoeffding下界高于近零带；"
            "统计正，不是精确最优、不是 timely、不是桌面已证。Wald 只作诊断"
        )
    elif complete and window_claim_allowed and statistical_nonpositive:
        sign_status = "bounded_ci_excludes_zero_nonpositive"
        sign_reason = "固定组成、预注册样本完成且有界Hoeffding上界不高于近零带的负侧；统计非正，不是精确最优"
    elif not complete:
        sign_status = "indeterminate"
        sign_reason = "未完成预注册样本或存在失败/未跑样本；成功子样本均值不能代表计划总体"
    elif input_scope != INPUT_SCOPE_FIXED_COMPOSITION:
        sign_status = "indeterminate"
        sign_reason = "剩余张数先验每次重抽组成，不能作为正式窗口声称"
    else:
        sign_status = "indeterminate"
        sign_reason = "有界区间含零或过近零；Wald 退化或过窄不能当作正式符号"
    source_mode = ("synthetic-composition" if input_scope == INPUT_SCOPE_FIXED_COMPOSITION
                   else "sampled-remaining")
    cancel_latency = None
    if cancel_noticed_at is not None:
        cancel_latency = perf_counter() - cancel_noticed_at
    report = {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "window_kind": WINDOW_PRE_DEAL,
        "source_mode": source_mode,
        "input_scope": input_scope,
        "strategy_id": policy,
        "rules": rules,
        "rules_digest": rules_digest,
        "policy_digest": policy_digest,
        "algorithm_digest": algorithm_digest,
        "method": METHOD,
        "evaluation_method": METHOD,
        "knowledge_revision": None,
        "ledger_prefix_digest": None,
        "information_cutoff": None,
        "result_ready_at": time() if complete else None,
        "decision_deadline": None,
        "timely": False,
        "exact_positive": False,
        "statistical_positive": statistical_positive,
        "statistical_nonpositive": statistical_nonpositive,
        "desktop_attested": False,
        "not_exact_optimal": True,
        "window_claim_allowed": window_claim_allowed,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "policy_id": policy,
        "policy_note": SUPPORTED_POLICIES[policy],
        "path_policy_id": policy,
        "evaluation_policy_id": policy,
        "exact_small_max_remaining": PREDEAL_MAX_REMAINING,
        "interactive_exact_budget_seconds": 5.0,
        "n_decks": decks_out,
        "physical_remaining": physical,
        "counts": composition_counts,
        "composition_counts": composition_counts,
        "parent_card_count": len(parent),
        "surrender": surrender,
        "sample_plan": sample_plan,
        "n_samples_planned": n_samples,
        "n_ok": n_ok,
        "n_failed": n_failed,
        "n_not_run": n_not_run,
        "failures": failures,
        "complete_pre_registered_sample": complete,
        "status": "available" if complete else "indeterminate",
        "reason_code": "CALCULATED" if complete else (stopped or "INCOMPLETE_SAMPLE"),
        "ev": population_ev,
        "successful_subsample_ev": subsample_mean,
        "variance": (sample_std * sample_std) if sample_std is not None else None,
        "std": sample_std,
        "standard_error": se,
        "ci_z": z_value,
        "wald_ci_low": lo,
        "wald_ci_high": hi,
        "wald_degenerate": wald_degenerate,
        "ci_low": formal_lo,
        "ci_high": formal_hi,
        "ci_method": FORMAL_CI_METHOD,
        "diagnostic_ci_method": WALD_CI_METHOD,
        "alpha_family": alpha_value,
        "family_size": family_size,
        "alpha_per_claim": formal.get("alpha_per_claim"),
        "payoff_support": [pay_low, pay_high],
        "payoff_support_note": "当前未分牌、不买保险、最多一次加倍、3:2 模型的保守净收益界；其他玩法必须另核",
        "hoeffding_radius": formal.get("radius"),
        "numerical_tolerance": band,
        "net_pay_counts": nets["net_pay_counts"],
        "net_distribution": nets["net_distribution"],
        "p_win": nets["p_win"],
        "p_push": nets["p_push"],
        "p_lose": nets["p_lose"],
        "sign_status": sign_status,
        "sign_reason": sign_reason,
        "seed": seed,
        "elapsed_seconds": elapsed,
        "samples_attempted": n_ok + n_failed,
        "samples_per_second": ((n_ok + n_failed) / elapsed) if elapsed > 0 else None,
        "peak_rss_bytes": _peak_rss_bytes(),
        "platform": sys.platform,
        "cancel_response": stopped == "cancelled",
        "cancel_latency_seconds": cancel_latency,
        "hardware_note": "吞吐按本机墙钟与已尝试样本；峰值RSS是进程工作集，不是精度证明；取消延迟是察觉 cancelled() 之后到回执写出的墙钟，不是整段任务时长",
        "cancelled": stopped == "cancelled",
        "note": "固定策略蒙特卡洛；失败/未跑样本使总体EV为空；正式符号用有界Hoeffding，Wald只作诊断；不是 timely、不是桌面已证",
    }
    report["window_state"] = window_state(report)
    return report
