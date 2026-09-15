"""Fixed-N Hoeffding interval for a bounded IID mean.

Wald intervals stay diagnostic elsewhere. This module is the formal sign gate:
bounds come from the legal payoff support, not from the observed sample range.
With M prespecified claims, alpha/M is the union bound. Do not use after
optional stopping. A valid interval is not timely, desktop-attested, or accepted.
"""
from __future__ import annotations

import math
from numbers import Real


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name}必须是有限实数，不能是布尔或文本")
    out = float(value)
    if not math.isfinite(out):
        raise ValueError(f"{name}必须有限")
    return out


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name}必须是≥{minimum}的整数")
    return value


def bounded_mean_interval(
    payoffs, *, planned_n, n_failed, n_not_run, lower_payoff, upper_payoff,
    alpha_family=0.05, family_size=1, positive_threshold=0.0,
    fixed_composition=True, fixed_policy=True, iid_design_declared=True,
):
    """Two-sided Hoeffding interval for a complete fixed-N bounded sample."""
    planned_n = _integer(planned_n, "planned_n", 1)
    n_failed = _integer(n_failed, "n_failed")
    n_not_run = _integer(n_not_run, "n_not_run")
    family_size = _integer(family_size, "family_size", 1)
    low_bound = _number(lower_payoff, "lower_payoff")
    high_bound = _number(upper_payoff, "upper_payoff")
    if low_bound >= high_bound:
        raise ValueError("收益界必须严格有序，且来自规则模型而不是样本最小/最大")
    alpha = _number(alpha_family, "alpha_family")
    if not 0 < alpha < 1:
        raise ValueError("alpha_family 必须在 (0,1)")
    threshold = _number(positive_threshold, "positive_threshold")
    if threshold < 0:
        raise ValueError("positive_threshold 必须非负")
    for name, flag in (("fixed_composition", fixed_composition), ("fixed_policy", fixed_policy),
                       ("iid_design_declared", iid_design_declared)):
        if type(flag) is not bool:
            raise ValueError(f"{name}必须是布尔值")
    values = [_number(item, "payoff") for item in payoffs]
    if any(item < low_bound or item > high_bound for item in values):
        raise ValueError("收益超出已声明模型支撑")
    n_ok = len(values)
    if n_ok + n_failed + n_not_run != planned_n:
        raise ValueError("成功+失败+未跑必须等于预注册样本数")
    diagnostic = math.fsum(values) / n_ok if n_ok else None
    complete = n_ok == planned_n and n_failed == n_not_run == 0
    eligible = complete and fixed_composition and fixed_policy and iid_design_declared
    report = {
        "method": "hoeffding_fixed_n_finite_family_v1",
        "planned_n": planned_n,
        "n_ok": n_ok,
        "n_failed": n_failed,
        "n_not_run": n_not_run,
        "complete": complete,
        "alpha_family": alpha,
        "family_size": family_size,
        "alpha_per_claim": alpha / family_size,
        "model_support": [low_bound, high_bound],
        "successful_subsample_mean": diagnostic,
        "ev": diagnostic if complete else None,
        "ci_low": None,
        "ci_high": None,
        "radius": None,
        "window_claim_allowed": False,
        "statistical_positive": False,
        "statistical_nonpositive": False,
        "timely": False,
        "desktop_attested": False,
        "accepted": False,
    }
    if not eligible:
        report["reason"] = "incomplete_sample_or_unverified_sampling_design"
        return report
    radius = (high_bound - low_bound) * math.sqrt(
        (math.log(2.0) + math.log(family_size) - math.log(alpha)) / (2 * n_ok))
    lo = max(low_bound, diagnostic - radius)
    hi = min(high_bound, diagnostic + radius)
    report.update(ci_low=lo, ci_high=hi, radius=radius, reason="fixed_n_bounded_interval")
    if lo > threshold:
        report["statistical_positive"] = True
        report["window_claim_allowed"] = True
    elif hi <= -threshold if threshold > 0 else hi <= 0:
        report["statistical_nonpositive"] = True
        report["window_claim_allowed"] = True
    return report
