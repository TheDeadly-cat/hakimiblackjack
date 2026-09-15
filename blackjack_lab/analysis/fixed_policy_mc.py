"""Frozen-policy Monte Carlo for 6/7/8-deck remaining compositions.

This evaluates an executable policy π, not composition-optimal unsplit EV.
The interactive exact_small entry stays capped at 16 cards and 5 seconds.
Offline callers pass their own sample count and optional wall-clock budget.
"""
from __future__ import annotations

import os
import sys
from math import sqrt
from random import Random
from statistics import mean, stdev
from time import perf_counter, time

from .predeal_contracts import (
    PREDEAL_MAX_REMAINING, PREDEAL_STRATEGY_VERSION, SURRENDER_UNSET,
    require_declared_surrender,
)
from .contracts import digest
from .probability import CalculationStopped, InsufficientCards
from .research_windows import WINDOW_PRE_DEAL, require_point_values, window_state
from .shoe_windows import (
    CONSUMPTION_BASIC, CONSUMPTION_PI, CONSUMPTION_STAND, full_pack, play_round,
)

SCHEMA = "hakimi-fixed-policy-mc-v1"
METHOD = "fixed_policy_monte_carlo"
POLICY_ALWAYS_STAND = CONSUMPTION_STAND
POLICY_TOY_HARD = CONSUMPTION_BASIC
SUPPORTED_POLICIES = {
    POLICY_ALWAYS_STAND: "冻结停牌消耗；不是最优策略",
    POLICY_TOY_HARD: "玩具硬点数消耗；不是已核验基本策略表",
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


def _require_nonneg_int(value, name):
    if type(value) is not int or value < 0:
        raise FixedPolicyError("ILLEGAL_INT", f"{name}必须是非负整数；不能把 {value!r} 截成整数")
    return value


def _policy_id(policy):
    if policy == CONSUMPTION_PI or policy == PREDEAL_STRATEGY_VERSION:
        raise FixedPolicyError(
            "OPTIMAL_POLICY_NOT_MC",
            "精确未分牌最优不是本离线MC方法；请用冻结停牌或玩具硬规则，小牌靴仍走 exact_small",
        )
    if policy not in SUPPORTED_POLICIES:
        raise FixedPolicyError("UNKNOWN_POLICY", f"未声明的冻结策略: {policy!r}")
    return policy


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


def evaluate_fixed_policy(*, n_decks=6, policy=POLICY_ALWAYS_STAND, n_samples=1024, seed=1,
                          remaining=None, pack=None, surrender=SURRENDER_UNSET, cancelled=None,
                          budget_seconds=None, play_budget_seconds=2.0, z=1.96):
    """Independent Monte Carlo of one next round under a frozen unsplit policy.

    `budget_seconds` is an offline wall-clock bound, not the interactive 5s exact cap.
    Failed or unrun samples stay in the denominator. A positive point estimate is not
    a proven opening window and is never exact-optimal EV.
    """
    policy = _policy_id(policy)
    n_samples = _require_positive_int(n_samples, "样本数")
    seed = _require_nonneg_int(seed, "种子")
    try:
        surrender = require_declared_surrender(surrender, what="固定策略MC")
    except ValueError as error:
        raise FixedPolicyError("SURRENDER_REQUIRED", str(error)) from error
    if budget_seconds is not None:
        if type(budget_seconds) is bool or type(budget_seconds) not in (int, float) or budget_seconds <= 0:
            raise FixedPolicyError("ILLEGAL_BUDGET", "离线墙钟预算必须为正数；不能改交互精确 5 秒门槛")
    rng = Random(seed)
    pays = []
    failures = []
    stopped = None
    start = perf_counter()
    for index in range(n_samples):
        if cancelled is not None and cancelled():
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
    sample_mean = mean(pays) if pays else None
    sample_std = stdev(pays) if n_ok >= 2 else None
    se = (sample_std / sqrt(n_ok)) if sample_std is not None else None
    z_value = float(z)
    lo = (sample_mean - z_value * se) if se is not None else None
    hi = (sample_mean + z_value * se) if se is not None else None
    if not complete or se is None:
        sign_status = "indeterminate"
        sign_reason = "未完成预注册样本或精度不足；不能宣称确定正优势"
    elif lo > 0:
        sign_status = "point_ci_excludes_zero_positive"
        sign_reason = "Wald 区间不含零且点估计为正；仍不是精确最优，也不是已证实窗口"
    elif hi < 0:
        sign_status = "point_ci_excludes_zero_negative"
        sign_reason = "Wald 区间不含零且点估计为负；仍不是精确最优"
    else:
        sign_status = "indeterminate"
        sign_reason = "区间含零或贴零；不能宣称确定正优势"
    physical = len(pack) if pack is not None else remaining
    if physical is None and n_decks in (6, 7, 8):
        physical = len(full_pack(n_decks))
    report = {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "window_kind": WINDOW_PRE_DEAL,
        "source_mode": "synthetic-composition" if pack is not None else "sampled-remaining",
        "strategy_id": policy,
        "rules_digest": digest({
            "window": WINDOW_PRE_DEAL,
            "surrender": surrender,
            "policy": policy,
            "method": METHOD,
        }),
        "method": METHOD,
        "knowledge_revision": None,
        "ledger_prefix_digest": None,
        "information_cutoff": None,
        "result_ready_at": time() if complete else None,
        "decision_deadline": None,
        "timely": False,
        "not_exact_optimal": True,
        "window_claim_allowed": False,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "policy_id": policy,
        "policy_note": SUPPORTED_POLICIES[policy],
        "path_policy_id": policy,
        "evaluation_policy_id": policy,
        "exact_small_max_remaining": PREDEAL_MAX_REMAINING,
        "interactive_exact_budget_seconds": 5.0,
        "n_decks": None if pack is not None else n_decks,
        "physical_remaining": physical,
        "surrender": surrender,
        "n_samples_planned": n_samples,
        "n_ok": n_ok,
        "n_failed": n_failed,
        "n_not_run": n_not_run,
        "failures": failures,
        "complete_pre_registered_sample": complete,
        "status": "available" if complete else "indeterminate",
        "reason_code": "CALCULATED" if complete else (stopped or "INCOMPLETE_SAMPLE"),
        "ev": sample_mean,
        "variance": (sample_std * sample_std) if sample_std is not None else None,
        "std": sample_std,
        "standard_error": se,
        "ci_z": z_value,
        "ci_low": lo,
        "ci_high": hi,
        "ci_method": "wald_normal_mean_sample_sd",
        "sign_status": sign_status,
        "sign_reason": sign_reason,
        "seed": seed,
        "elapsed_seconds": elapsed,
        "samples_attempted": n_ok + n_failed,
        "samples_per_second": ((n_ok + n_failed) / elapsed) if elapsed > 0 else None,
        "peak_rss_bytes": _peak_rss_bytes(),
        "platform": sys.platform,
        "cancel_response": stopped == "cancelled",
        "cancel_latency_seconds": elapsed if stopped == "cancelled" else None,
        "hardware_note": "吞吐按本机墙钟与已尝试样本；峰值RSS是进程工作集，不是精度证明；取消延迟是本机墙钟，不是声明硬件基准",
        "cancelled": stopped == "cancelled",
        "note": "固定策略蒙特卡洛；失败样本未丢弃；正的点估计不是精确最优开局优势",
    }
    report["window_state"] = window_state(report)
    return report
