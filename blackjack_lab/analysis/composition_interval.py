"""Interval research over candidate remaining packs. A mean shoe is not exact EV.

Unknown ranks or unknown removal counts stay a gap unless the caller lists
concrete remaining compositions. The published signal is the min/max of
available exact evaluations, never their average.
"""
from __future__ import annotations

from .contracts import AVAILABLE
from .research_windows import WINDOW_PRE_DEAL
from .shoe_windows import evaluate_predeal

SCHEMA = "hakimi-composition-interval-v1"


class IntervalError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def refuse_mean_shoe():
    return {
        "schema": SCHEMA,
        "status": "unsupported",
        "reason_code": "MEAN_SHOE_FORBIDDEN",
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
        "forbids_mean_shoe": True,
        "ev": None,
        "published_point_ev": None,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "note": "未知数量或未知牌面移除仍是缺口；须列出候选剩余组成，不能默认平均牌靴",
    }


def evaluate_interval(candidates, *, budget_seconds=5.0):
    if not candidates:
        raise IntervalError("CANDIDATES_MISSING", "区间研究必须给出候选剩余组成，不能默认平均牌靴")
    evaluated = []
    for pack in candidates:
        if not isinstance(pack, (list, tuple)) or not pack:
            raise IntervalError("CANDIDATE_INVALID", "每个候选必须是非空剩余点值列表")
        evaluated.append(evaluate_predeal(list(pack), budget_seconds=budget_seconds))
    available = [item for item in evaluated
                 if item.get("status") == AVAILABLE and item.get("ev") is not None]
    evs = [item["ev"] for item in available]
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "forbids_mean_shoe": True,
        "independent_video": False,
        "candidates": evaluated,
        "summary": {
            "n_candidates": len(evaluated),
            "n_available": len(available),
            "ev_min": min(evs) if evs else None,
            "ev_max": max(evs) if evs else None,
            "interval_width": (max(evs) - min(evs)) if evs else None,
            "published_point_ev": None,
            "mean_ev_not_published": (sum(evs) / len(evs)) if evs else None,
            "zero_window_all": bool(evs) and all(ev <= 0 for ev in evs),
            "note": "只公布可用EV的最小/最大；平均值仅作对照字段，不是窗口信号",
        },
    }
