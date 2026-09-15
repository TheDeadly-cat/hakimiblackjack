"""Run a frozen-policy MC from a ledger-frozen remaining composition."""
from __future__ import annotations

import uuid
from time import perf_counter

from .contracts import AVAILABLE, CANCELLED, FAILED, TIMEOUT
from .fixed_policy_mc import FixedPolicyError, evaluate_fixed_policy
from .offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA
from .predeal_contracts import legal_predeal_actions
from .research_windows import source_mode_from_information, window_state


def calculate_offline_mc(snapshot, request_id=None, budget_seconds=30.0):
    from .service import base_result
    request_id = request_id or uuid.uuid4().hex
    result = base_result(snapshot, request_id)
    result["schema"] = OFFLINE_MC_RESULT_SCHEMA
    result["partial_comparison"] = False
    result["legal_actions"] = list(legal_predeal_actions(snapshot.surrender))
    result["surrender"] = snapshot.surrender
    result["ledger_prefix_digest"] = snapshot.prefix_digest
    result["information_cutoff"] = snapshot.through_seq
    result["source_mode"] = source_mode_from_information(snapshot.information_json)
    result["not_exact_optimal"] = True
    result["not_a_reliable_window_claim"] = True
    result["timely"] = False
    result["desktop_attested"] = False
    result["accepted"] = False
    start = perf_counter()
    try:
        snapshot.validate()
        report = evaluate_fixed_policy(
            pack=snapshot.pack, policy=snapshot.policy_id, n_samples=snapshot.n_samples,
            seed=snapshot.seed, surrender=snapshot.surrender,
            budget_seconds=budget_seconds, play_budget_seconds=snapshot.play_budget_seconds,
            family_size=snapshot.family_size, alpha=snapshot.alpha)
    except FixedPolicyError as error:
        result.update(status=FAILED, reason_code=error.code, reason=str(error), ev=None)
        result["elapsed_seconds"] = perf_counter() - start
        result["window_state"] = window_state(result)
        return result
    except Exception as error:
        result.update(status=FAILED, reason_code="CALCULATION_FAILED", reason=str(error), ev=None)
        result["elapsed_seconds"] = perf_counter() - start
        result["window_state"] = window_state(result)
        return result
    keep = (
        "ev", "variance", "std", "standard_error", "ci_low", "ci_high", "ci_z",
        "wald_ci_low", "wald_ci_high", "wald_degenerate", "ci_method",
        "diagnostic_ci_method", "alpha_family", "family_size", "alpha_per_claim",
        "payoff_support", "payoff_support_note", "hoeffding_radius",
        "numerical_tolerance", "net_pay_counts", "net_distribution",
        "p_win", "p_push", "p_lose", "sign_status", "sign_reason",
        "n_samples_planned", "n_ok", "n_failed", "n_not_run", "failures",
        "complete_pre_registered_sample", "successful_subsample_ev",
        "statistical_positive", "statistical_nonpositive", "window_claim_allowed",
        "exact_positive", "policy_id", "policy_note", "sample_plan",
        "physical_remaining", "composition_counts", "input_scope",
        "method", "evaluation_method", "evaluation_policy_id",
        "samples_per_second", "peak_rss_bytes", "platform", "note",
    )
    for key in keep:
        if key in report:
            result[key] = report[key]
    result["engine_version"] = snapshot.engine_version
    result["strategy_version"] = snapshot.strategy_version
    result["rules_digest"] = snapshot.rules_digest
    result["reason"] = report.get("sign_reason") or report.get("note") or "冻结策略离线MC"
    if report.get("complete_pre_registered_sample"):
        result["status"] = AVAILABLE
        result["reason_code"] = "CALCULATED"
    elif report.get("cancelled"):
        result["status"] = CANCELLED
        result["reason_code"] = "CANCELLED"
        result["ev"] = None
    elif report.get("reason_code") == "timeout":
        result["status"] = TIMEOUT
        result["reason_code"] = "TIMEOUT"
        result["ev"] = None
    else:
        result["status"] = FAILED
        result["reason_code"] = report.get("reason_code") or "INCOMPLETE_SAMPLE"
        result["ev"] = None
    result["elapsed_seconds"] = report.get("elapsed_seconds") or (perf_counter() - start)
    result["window"] = report.get("window") or snapshot.window
    result["window_kind"] = report.get("window_kind") or snapshot.window
    result["result_ready_at"] = report.get("result_ready_at")
    result["window_state"] = window_state(result)
    return result


def format_offline_mc_result(result, historical=False, live_applicable=True,
                             applicability_reason=None, applicability_kind=None):
    from .research_windows import result_heading
    from ..observation.currency import REASON_ZH
    info = result["input"]
    lines = [
        result_heading(historical, live_applicable, applicability_kind)
        + f"账本前缀 #{info.get('through_seq')} · 剩余 {info.get('physical_remaining')} 张 · {info.get('n_decks')}副"
    ]
    if historical:
        lines.append("原时点离线MC，不代表当前输入")
    elif not live_applicable:
        detail = REASON_ZH.get(applicability_reason, applicability_reason or "观察状态已变化")
        lines.append("账本未变；数字对应已确认前缀，不适用于眼前牌桌：" + detail)
    if result["status"] != "available":
        lines.append(f"{result.get('reason_code')}: {result.get('reason')}")
        return "\n".join(lines)
    lines.append(f"冻结策略：{result.get('policy_id')}（不是精确最优）")
    ev = result.get("ev")
    if ev is not None:
        lines.append(f"样本均值 EV {ev:+.6f}")
    lines.append(
        f"正式区间 [{result.get('ci_low')}, {result.get('ci_high')}] · {result.get('ci_method')}"
    )
    lines.append(
        f"Wald诊断 [{result.get('wald_ci_low')}, {result.get('wald_ci_high')}]"
        + (" · 退化" if result.get("wald_degenerate") else "")
    )
    lines.append(
        f"窗口状态 {result.get('window_state')} · 统计正 {result.get('statistical_positive')} · "
        f"允许声称 {result.get('window_claim_allowed')}"
    )
    lines.append(result.get("sign_reason") or "")
    lines.append(
        f"样本 {result.get('n_ok')}/{result.get('n_samples_planned')} · "
        f"family_size {result.get('family_size')} · 收益界 {result.get('payoff_support')}"
    )
    lines.append(f"组成十桶 {result.get('composition_counts')}")
    lines.append(f"来源 {result.get('source_mode')} · 前缀 {info.get('prefix_digest', '')[:16]}")
    lines.append("未知组成不会被平均成精确组成。结果不是 timely，不是桌面已证，也不是验收通过。")
    lines.append(f"耗时 {result.get('elapsed_seconds', 0):.3f}s · 引擎 {result.get('engine_version')}")
    return "\n".join(line for line in lines if line)
