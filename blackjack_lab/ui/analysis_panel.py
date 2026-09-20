"""Local analysis UI: real numbers, request state, and immutable historical results."""
from datetime import datetime
import json
import tkinter as tk
from tkinter import ttk, messagebox

from ..analysis.contracts import RESULT_SCHEMA, STATUS_ZH, ACTION_ZH, AVAILABLE, STALE, InputUnavailable
from ..analysis.predeal_contracts import PREDEAL_RESULT_SCHEMA, PreDealInput
from ..analysis.offline_mc_contracts import OFFLINE_MC_RESULT_SCHEMA, OfflineMcInput
from ..analysis.offline_mc import format_offline_mc_result
from ..analysis.fixed_policy_mc import POLICY_ALWAYS_STAND
from ..analysis.research_windows import (
    CURRENT_HAND_SCOPE, KIND_LIVE_CURRENT, KIND_MANUAL_ASOF, KIND_REPLAY, KIND_STALE,
    KIND_SYNTHETIC, WINDOW_PRE_DEAL, assess_timely_live_claim, build_offline_mc_input,
    build_predeal_input, counts_from_values, format_outcome_line, format_predeal_result,
    knowledge_revision_token, parse_remaining_tokens, parse_surrender_token, research_rules,
    result_heading,
)
from ..analysis.service import AnalysisService
from ..observation.currency import REASON_INPUT, REASON_SWITCHED, REASON_ZH
from ..storage.analysis_snapshots import is_minimal_result
from ..analysis.split_contracts import SPLIT_RESULT_SCHEMA

DISPLAY_RESULT_SCHEMAS = {RESULT_SCHEMA, SPLIT_RESULT_SCHEMA, PREDEAL_RESULT_SCHEMA, OFFLINE_MC_RESULT_SCHEMA}


def _current_hand_rule_line(info):
    raw = info.get("rules_json")
    rules = {}
    if isinstance(raw, str) and raw:
        try:
            rules = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            rules = {}
    if not isinstance(rules, dict) or "surrender" not in rules:
        return "S17 · 3:2 · 美式底牌 · 投降规则未写入结果，不能按研究模板补全。"
    if rules.get("surrender") is None:
        return "S17 · 3:2 · 美式底牌 · 无投降 · 初始零烧牌"
    if rules.get("surrender") == "late":
        return "S17 · 3:2 · 美式底牌 · 晚投降 · 初始零烧牌"
    return "S17 · 3:2 · 美式底牌 · 投降规则未验收，不能按研究模板补全。"


def format_result(result, historical=False, live_applicable=True, applicability_reason=None,
                  applicability_kind=None):
    if result.get("schema") == OFFLINE_MC_RESULT_SCHEMA:
        return format_offline_mc_result(result, historical, live_applicable=live_applicable,
                                        applicability_reason=applicability_reason,
                                        applicability_kind=applicability_kind)
    if result.get("schema") == PREDEAL_RESULT_SCHEMA:
        return format_predeal_result(result, historical, live_applicable=live_applicable,
                                     applicability_reason=applicability_reason,
                                     applicability_kind=applicability_kind)
    if result['schema'] == SPLIT_RESULT_SCHEMA:
        from .split_display import format_split_result
        return format_split_result(result, historical, live_applicable=live_applicable,
                                   applicability_reason=applicability_reason,
                                   applicability_kind=applicability_kind)
    info = result["input"]
    lines = [result_heading(historical, live_applicable, applicability_kind) +
             f"{info['seat']} · {' '.join(info['player_ranks'])} · 庄家 {info['dealer_up']} · {info['n_decks']}副"]
    if result["status"] != AVAILABLE:
        lines.append(f"{STATUS_ZH.get(result['status'], result['status'])}：{result['reason']}")
        return "\n".join(lines)
    if historical:
        lines.append("原时点结果，不代表当前输入")
    elif not live_applicable:
        detail = REASON_ZH.get(applicability_reason, applicability_reason or "观察状态已变化")
        lines.append("账本未变；数字对应已确认前缀，不适用于眼前牌桌：" + detail)
    if result["partial_comparison"]:
        lines.append("部分动作比较（分牌缺失或合法性待核对）")
    lines.append("EV单位：原始1单位初始注的最终净收益")
    for action, item in result["actions"].items():
        value = f"  EV {item['ev']:+.6f}" if item["status"] == AVAILABLE else ""
        extra = ""
        if item["status"] == AVAILABLE and item.get("net_distribution"):
            extra = " · " + format_outcome_line(item["net_distribution"])
        lines.append(f"{ACTION_ZH[action]}：{STATUS_ZH[item['status']]}{value}{extra}")
    if result["partial_comparison"]:
        lines.append("部分动作比较；缺少分牌EV或合法性待核对，不给唯一推荐。")
    elif result.get("highest_ev_action"):
        lines.append(f"在声明模型与当时信息下EV最高：{ACTION_ZH[result['highest_ev_action']]}")
    if result.get("all_computed_ev_negative"):
        lines.append("已计算动作EV均为负；较高只意味着可能少亏。")
    lines.append(CURRENT_HAND_SCOPE)
    lines.append("当前手牌EV不等于下一轮开局优势，不提供注额建议。")
    probabilities = result["probabilities"]
    if result.get("probability_status", {}).get("next_target_draw", AVAILABLE) == AVAILABLE:
        lines.extend(["", f"若目标再抽一张，爆牌概率 {probabilities['hit_bust']:.4%}", "下一次目标抽牌分布（已计入底牌条件）："])
        bins = [f"{k}:{v:.3%}" for k, v in probabilities["next_target_draw"].items()]
        for i in range(0, len(bins), 3):
            lines.append("  ".join(bins[i:i + 3]))
    else:
        lines.append("目标当前无合法抽牌动作，抽牌/爆牌指标不适用。")
    lines.append("庄家终局分布（此刻停牌，不再移除玩家牌）：")
    dealer_names = {"blackjack": "BJ", "bust": "爆牌"}
    bins = [f"{dealer_names.get(k, k)}:{v:.3%}" for k, v in probabilities["dealer_terminal_if_stand_now"].items()]
    for i in range(0, len(bins), 3):
        lines.append("  ".join(bins[i:i + 3]))
    lines.extend(["", _current_hand_rule_line(info),
                  "底牌未揭示；非BJ检查：" + ("已记录" if info["peek_negative"] else "此明牌无需检查"),
                  "补牌后按可见新牌在补/停之间继续决策。",
                  "有限不放回枚举，双精度舍入；无采样/概率截断。",
                  f"耗时 {result['elapsed_seconds']:.3f}s · 事件前缀 #{info['through_seq']}",
                  f"引擎：{result['engine_version']}", f"策略：{result['strategy_version']}",
                  f"输入摘要：{result['input_digest'][:16]}", f"规则摘要：{result['rules_digest'][:16]}"])
    return "\n".join(lines)


class AnalysisPanel(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.service = AnalysisService()
        self.context_key = None
        self.request_key = None
        self.request_digest = None
        self.request_id = None
        self.last_result = None
        self.saved = None
        self.recomputed_from = None
        self._auto_id = None
        self._auto_suppressed_key = None
        self._closed = False
        self.live_applicable = True
        self._applicability_reason = None
        self._applicability_kind = KIND_MANUAL_ASOF
        self.request_observation = None
        self.result_source_generation = ""
        self._observation_moved_during_request = False
        self._input_moved_during_request = False
        self._request_window = None
        self._request_offline = False
        self.status = tk.StringVar(value="先选择研究模板并录入当前手牌")
        self.persistence = tk.StringVar(value="结果会独立保存，原始事件不变")
        self.auto = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=2)
        self.compute_button = ttk.Button(toolbar, text="计算当前手牌", command=self.calculate_current)
        self.compute_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(toolbar, text="取消", command=self.cancel)
        self.cancel_button.pack(side=tk.LEFT, padx=3)
        self.auto_button = ttk.Checkbutton(toolbar, text="自动", variable=self.auto)
        self.auto_button.pack(side=tk.LEFT)
        predeal_bar = ttk.Frame(self)
        predeal_bar.grid(row=1, column=0, sticky="ew", pady=2)
        self.predeal_button = ttk.Button(predeal_bar, text="计算发牌前优势", command=self.calculate_predeal)
        self.predeal_button.pack(side=tk.LEFT)
        self.predeal_button.state(["disabled"])
        self.offline_button = ttk.Button(
            predeal_bar, text="按已确认账本离线评估", command=self.calculate_offline_mc)
        self.offline_button.pack(side=tk.LEFT, padx=(6, 0))
        self.offline_button.state(["disabled"])
        self.var_predeal_remaining = tk.StringVar(value="")
        ttk.Label(predeal_bar, text="合成剩余").pack(side=tk.LEFT, padx=(8, 2))
        self.predeal_entry = ttk.Entry(predeal_bar, textvariable=self.var_predeal_remaining, width=18)
        self.predeal_entry.pack(side=tk.LEFT)
        ttk.Label(self, textvariable=self.status, wraplength=300).grid(row=2, column=0, sticky="w", padx=3)
        ttk.Label(self, textvariable=self.persistence, wraplength=300, foreground="#555").grid(row=3, column=0, sticky="w", padx=3)
        body = ttk.Frame(self)
        body.grid(row=4, column=0, sticky="nsew")
        self.text = tk.Text(body, wrap=tk.WORD, state=tk.DISABLED, width=38, height=15, font=("Microsoft YaHei UI", 9))
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(self)
        footer.grid(row=5, column=0, sticky="ew", pady=2)
        ttk.Button(footer, text="历史分析 / 复算", command=self.show_history).pack(side=tk.LEFT)
        ttk.Button(footer, text="重试保存", command=self.retry_save).pack(side=tk.LEFT, padx=3)
        self.app.ctrl.add_context_listener(self.context_changed)
        self._target_traces = [(var, var.trace_add("write", self.context_changed))
                               for var in (self.app.var_target, self.app.var_hand)]
        self._auto_trace = self.auto.trace_add("write", self._auto_changed)
        self._predeal_trace = self.var_predeal_remaining.trace_add("write", self._predeal_field_changed)
        self._rule_traces = [
            (self.app.var_surrender, self.app.var_surrender.trace_add("write", self._predeal_field_changed)),
            (self.app.var_decks, self.app.var_decks.trace_add("write", self._predeal_field_changed)),
        ]
        obs = getattr(self.app, "observation", None)
        if obs is not None:
            obs.add_listener(self._on_observation)
        self._poll_id = self.after(50, self._poll)

    def _on_observation(self):
        if not self._closed:
            self._sync_observation_currency()

    def _set_text(self, text):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", text)
        self.text.configure(state=tk.DISABLED)

    def _live_key(self):
        return (self.app.ctrl.context_token, self.app.var_target.get(), self.app.var_hand.get())

    def _observation_revision(self):
        obs = getattr(self.app, "observation", None)
        return None if obs is None else obs.revision()

    def _live_table_ok(self):
        obs = getattr(self.app, "observation", None)
        if obs is None:
            return False, "manual_asof"
        return obs.live_table_applicable()

    def _observation_kind(self):
        revision = self._observation_revision()
        if revision is None:
            return KIND_MANUAL_ASOF
        return revision.applicability_kind()

    def _display_kind(self):
        if self.recomputed_from:
            return None
        if self.last_result and self.last_result.get("schema") == PREDEAL_RESULT_SCHEMA:
            raw = (self.last_result.get("input") or {}).get("information_json")
            if isinstance(raw, str) and "explicit-composition" in raw:
                return KIND_SYNTHETIC
        if self.last_result and self.last_result.get("schema") == OFFLINE_MC_RESULT_SCHEMA:
            return KIND_MANUAL_ASOF
        if self.live_applicable:
            return KIND_LIVE_CURRENT
        kind = self._applicability_kind or KIND_STALE
        if kind == KIND_LIVE_CURRENT:
            return KIND_STALE
        return kind

    def _refresh_result_text(self):
        if not self.last_result:
            return
        historical = bool(self.recomputed_from)
        live = (not historical) and self.live_applicable
        self._set_text(format_result(
            self.last_result, historical=historical, live_applicable=live,
            applicability_reason=None if live or historical else self._applicability_reason,
            applicability_kind=None if historical else self._display_kind()))

    def _status_for_result(self, result):
        summary = result["reason"]
        if result["status"] == AVAILABLE:
            if result.get("schema") == PREDEAL_RESULT_SCHEMA:
                summary = f"发牌前净 EV {result['ev']:+.6f}"
            elif result.get("schema") == OFFLINE_MC_RESULT_SCHEMA:
                ev = result.get("ev")
                summary = (f"离线MC EV {ev:+.6f}" if ev is not None else result.get("reason") or "离线MC")
            else:
                summary = ("部分动作比较，不给唯一推荐" if result["partial_comparison"] else
                           "已计算动作EV均为负" if result.get("all_computed_ev_negative") else "计算完成（所声明模型）")
        prefix = "历史复算 · " if self.recomputed_from else ""
        if not self.recomputed_from and not self.live_applicable:
            kind = self._display_kind()
            if kind == KIND_SYNTHETIC:
                prefix = "合成研究 · "
            elif kind == KIND_REPLAY:
                prefix = "录像回放 · "
            elif kind == KIND_MANUAL_ASOF:
                prefix = "截至人工确认记录 · "
            else:
                prefix = "截至已确认记录 · "
        return prefix + STATUS_ZH[result["status"]] + "：" + summary

    def _sync_observation_currency(self):
        if self.recomputed_from:
            return
        ok, reason = self._live_table_ok()
        revision = self._observation_revision()
        generation = revision.generation if revision is not None else ""
        kind = revision.applicability_kind() if revision is not None else KIND_MANUAL_ASOF
        ledger_ok = self.request_key is None or self.request_key == self._live_key()
        if not self.last_result or not ledger_ok:
            self.live_applicable = ok and not self._observation_moved_during_request and not self._input_moved_during_request
            self._applicability_reason = None if self.live_applicable else reason
            self._applicability_kind = KIND_LIVE_CURRENT if self.live_applicable else kind
            return
        if self._observation_moved_during_request or self._input_moved_during_request:
            if self._input_moved_during_request:
                shown_reason = REASON_INPUT
                shown_kind = KIND_STALE
            elif kind in (KIND_REPLAY, KIND_MANUAL_ASOF):
                shown_reason = reason
                shown_kind = kind
            else:
                shown_reason = reason if not ok else REASON_SWITCHED
                shown_kind = KIND_STALE
            if self.live_applicable or self._applicability_reason != shown_reason or self._applicability_kind != shown_kind:
                self.live_applicable = False
                self._applicability_reason = shown_reason
                self._applicability_kind = shown_kind
                self.status.set(self._status_for_result(self.last_result))
                self.persistence.set("观察或输入已变化；本结果只保留为截至已确认记录，需重新计算才能作为当前牌桌信号")
                self._refresh_result_text()
            return
        same_source = generation == self.result_source_generation
        if ok and same_source:
            if not self.live_applicable or self._applicability_kind != KIND_LIVE_CURRENT:
                self.live_applicable = True
                self._applicability_reason = None
                self._applicability_kind = KIND_LIVE_CURRENT
                self.status.set(self._status_for_result(self.last_result))
                self._refresh_result_text()
            return
        shown_reason = reason if not ok else REASON_SWITCHED
        shown_kind = kind if not ok else KIND_STALE
        if self.live_applicable or self._applicability_reason != shown_reason or self._applicability_kind != shown_kind:
            self.live_applicable = False
            self._applicability_reason = shown_reason
            self._applicability_kind = shown_kind
            self.status.set(self._status_for_result(self.last_result))
            self.persistence.set("账本未变；数字仍对应已确认前缀，不能当作眼前牌桌信号")
            self._refresh_result_text()

    def _cancel_auto(self):
        if self._auto_id:
            self.after_cancel(self._auto_id)
            self._auto_id = None

    def _invalidate_current(self):
        self.request_key = self.request_digest = self.request_id = None
        self.last_result = self.saved = None
        self.request_observation = None
        self.result_source_generation = ""
        self.live_applicable = True
        self._applicability_reason = None
        self._applicability_kind = KIND_MANUAL_ASOF
        self._observation_moved_during_request = False
        self._input_moved_during_request = False
        self._request_window = None
        self.service.cancel(STALE)
        self.status.set("过期：牌面、目标或会话已变化")
        self.persistence.set("旧判断仅保留在历史分析中")
        self._set_text("旧请求与当前输入不匹配，已移除当前数值。已保存的原结果仍可在历史分析中查看。")

    def context_changed(self, *_args):
        """Synchronous post-commit / target notification, before any general redraw."""
        if self._closed or self._live_key() == self.context_key:
            return
        self._cancel_auto()
        if not self.recomputed_from and (self.request_key is not None or self.last_result):
            self._invalidate_current()
        self.context_key = None  # Gate/button refresh may run later; invalidation has already happened.

    def _auto_changed(self, *_args):
        self._cancel_auto()
        self._auto_suppressed_key = None if self.auto.get() else self._live_key()
        if self.auto.get() and not self.recomputed_from:
            self._auto_id = self.after(250, self._run_auto)

    def _run_auto(self):
        self._auto_id = None
        revision = self._observation_revision()
        knowledge_ok = revision is None or revision.knowledge_clear()
        if (self.auto.get() and not self._closed and not self.recomputed_from
                and self._live_key() != self._auto_suppressed_key
                and knowledge_ok):
            self.calculate_current()

    def on_context(self, segment):
        key = self._live_key()
        if key == self.context_key:
            return
        self.context_changed()
        self.context_key = key
        hand_id = self.app._selected_hand_id(segment) if segment else None
        try:
            self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
        except InputUnavailable as error:
            if not self.recomputed_from:
                self.status.set(f"{STATUS_ZH[error.status]}：{error.reason}")
            self.compute_button.state(["disabled"])
        else:
            self.compute_button.state(["!disabled"])
            if not self.last_result and not self.recomputed_from:
                self.status.set("可计算：当前单手，正常底牌仍未揭示")
            if self.auto.get() and not self.recomputed_from and key != self._auto_suppressed_key:
                self._auto_id = self.after(250, self._run_auto)
        self._refresh_predeal_gate()

    def _predeal_field_changed(self, *_args):
        if self._closed:
            return
        self._refresh_predeal_gate()
        if self.recomputed_from or self._request_window != WINDOW_PRE_DEAL or self._request_offline:
            return
        if self.request_id is None and not self.last_result:
            return
        current = self._current_predeal_digest()
        if current == self.request_digest:
            return
        self._input_moved_during_request = True
        self.live_applicable = False
        self._applicability_reason = REASON_INPUT
        self._applicability_kind = KIND_STALE
        if self.last_result:
            self.status.set(self._status_for_result(self.last_result))
            self.persistence.set("发牌前输入已改变；本结果不再作为当前请求，可保留为截至旧输入的记录")
            self._refresh_result_text()

    def _current_predeal_digest(self):
        try:
            return self._predeal_snapshot().input_digest
        except Exception:
            return None

    def _synthetic_predeal_rules(self):
        locked = self.app.ctrl.current_rules()
        if locked is not None:
            return locked
        surrender = parse_surrender_token(self.app.var_surrender.get())
        return research_rules(self.app.var_decks.get(), surrender=surrender)

    def _predeal_snapshot(self):
        text = self.var_predeal_remaining.get().strip()
        if text:
            return build_predeal_input(
                counts=counts_from_values(parse_remaining_tokens(text)),
                rules=self._synthetic_predeal_rules())
        return self.app.ctrl.predeal_input()

    def _refresh_predeal_gate(self):
        try:
            self._predeal_snapshot()
        except (InputUnavailable, ValueError):
            self.predeal_button.state(["disabled"])
        else:
            self.predeal_button.state(["!disabled"])
        try:
            self._offline_snapshot()
        except (InputUnavailable, ValueError):
            self.offline_button.state(["disabled"])
        else:
            self.offline_button.state(["!disabled"])
        if (self._request_offline and self.request_digest and not self.recomputed_from
                and (self.request_id is not None or self.last_result)):
            current = self._current_offline_digest()
            if current != self.request_digest:
                self._input_moved_during_request = True
                self.live_applicable = False
                self._applicability_reason = REASON_INPUT
                self._applicability_kind = KIND_STALE
                if self.last_result:
                    self.status.set(self._status_for_result(self.last_result))
                    self.persistence.set("账本前缀已改变；离线结果保留为截至旧时点的记录")
                    self._refresh_result_text()

    def _offline_snapshot(self, template=None):
        policy = template.policy_id if template is not None else POLICY_ALWAYS_STAND
        n_samples = template.n_samples if template is not None else 256
        seed = template.seed if template is not None else 1
        family_size = template.family_size if template is not None else 1
        alpha = template.alpha if template is not None else 0.05
        return build_offline_mc_input(
            self.app.ctrl.ledger, policy=policy, n_samples=n_samples, seed=seed,
            family_size=family_size, alpha=alpha)

    def _current_offline_digest(self):
        try:
            template = None
            if self.last_result and self.last_result.get("schema") == OFFLINE_MC_RESULT_SCHEMA:
                template = OfflineMcInput.from_dict(self.last_result["input"])
            return self._offline_snapshot(template).input_digest
        except Exception:
            return None

    def calculate_current(self):
        self._cancel_auto()
        try:
            segment = self.app._current_seg()
            hand_id = self.app._selected_hand_id(segment) if segment else None
            snapshot = self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
            self.start(snapshot)
        except Exception as error:
            self.recomputed_from = None
            self._invalidate_current()
            self._set_text("")
            self.status.set(f"待核对：{error}")

    def calculate_predeal(self):
        self._cancel_auto()
        try:
            snapshot = self._predeal_snapshot()
            self.start(snapshot)
        except Exception as error:
            self.recomputed_from = None
            self._invalidate_current()
            self._set_text("")
            self.status.set(f"待核对：{error}")

    def calculate_offline_mc(self):
        self._cancel_auto()
        try:
            snapshot = self._offline_snapshot()
            self.start(snapshot, budget_seconds=30.0)
        except Exception as error:
            self.recomputed_from = None
            self._invalidate_current()
            self._set_text("")
            self.status.set(f"待核对：{error}")

    def start(self, snapshot, recomputed_from=None, budget_seconds=5.0):
        self._cancel_auto()
        if not recomputed_from:
            if isinstance(snapshot, PreDealInput):
                current = self._predeal_snapshot()
                if current.input_digest != snapshot.input_digest:
                    raise InputUnavailable("TARGET_CHANGED", "发牌前输入已改变，请按当前组成重新计算")
            elif isinstance(snapshot, OfflineMcInput):
                current = self._offline_snapshot(snapshot)
                if current.input_digest != snapshot.input_digest:
                    raise InputUnavailable("TARGET_CHANGED", "账本前缀已改变，请按当前已确认记录重新计算")
            else:
                segment = self.app._current_seg()
                hand_id = self.app._selected_hand_id(segment) if segment else None
                current = self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
                if current.input_digest != snapshot.input_digest:
                    raise InputUnavailable("TARGET_CHANGED", "输入已改变，请按当前手牌重新计算")
        self.context_key = self._live_key()
        self.request_key = self.context_key
        self.request_digest = snapshot.input_digest
        self._request_offline = isinstance(snapshot, OfflineMcInput)
        self._request_window = (
            WINDOW_PRE_DEAL if isinstance(snapshot, (PreDealInput, OfflineMcInput)) else "current_hand"
        )
        self.request_observation = self._observation_revision()
        self.result_source_generation = (
            self.request_observation.generation if self.request_observation is not None else "")
        ok, reason = self._live_table_ok()
        synthetic = isinstance(snapshot, PreDealInput) and "explicit-composition" in (snapshot.information_json or "")
        self._input_moved_during_request = False
        self._observation_moved_during_request = False
        if recomputed_from:
            self.live_applicable = False
            self._applicability_reason = None
            self._applicability_kind = KIND_SYNTHETIC if synthetic else KIND_STALE
        elif synthetic:
            self.live_applicable = False
            self._applicability_reason = None
            self._applicability_kind = KIND_SYNTHETIC
        elif isinstance(snapshot, OfflineMcInput):
            self.live_applicable = False
            self._applicability_reason = None
            self._applicability_kind = KIND_MANUAL_ASOF
        else:
            self.live_applicable = bool(ok)
            self._applicability_reason = None if self.live_applicable else reason
            self._applicability_kind = (
                KIND_LIVE_CURRENT if self.live_applicable else self._observation_kind())
        self.recomputed_from = recomputed_from
        self.saved = None
        self.last_result = None
        waiting = "正在按当时可见信息计算；可以继续录入或取消。"
        if synthetic:
            waiting = "正在按合成剩余组成计算；结果不是当前真实牌桌。"
        elif isinstance(snapshot, OfflineMcInput):
            waiting = "正在按已确认账本冻结组成做离线MC；结果不是及时发现，也不能当作桌面已证。"
        elif not recomputed_from and not ok:
            waiting = "正在按已确认记录计算；结果不能自动当作当前牌桌信号。"
        self._set_text(waiting)
        self.persistence.set("等待当前计算完成")
        self.request_id = self.service.start(snapshot, budget_seconds)
        self.status.set(("历史复算中" if recomputed_from else "计算中") + f" · 前缀 #{snapshot.through_seq}")

    def _poll(self):
        if self._closed:
            return
        if self._poll_id:
            self.after_cancel(self._poll_id)
        # Defence in depth: compare to the live controller and selection, even if a
        # commit notification or every general refresh method was skipped/failed.
        if self.context_key != self._live_key():
            self.on_context(self.app._current_seg())
        self._sync_observation_currency()
        result = self.service.poll()
        current_matches = self.request_key is not None and self.request_key == self._live_key()
        if (result and (self.recomputed_from or current_matches)
                and result.get("request_id") == self.request_id
                and result.get("input_digest") == self.request_digest):
            revision = self._observation_revision()
            ok, reason = self._live_table_ok()
            knowledge_now = None if revision is None else revision.knowledge_identity()
            knowledge_then = (
                None if self.request_observation is None else self.request_observation.knowledge_identity())
            observation_unchanged = self.recomputed_from or knowledge_then == knowledge_now
            if not observation_unchanged:
                self._observation_moved_during_request = True
            input_matches_now = True
            if self._request_window == WINDOW_PRE_DEAL and not self.recomputed_from:
                if self._request_offline:
                    input_matches_now = self._current_offline_digest() == result.get("input_digest")
                else:
                    input_matches_now = self._current_predeal_digest() == result.get("input_digest")
                if not input_matches_now:
                    self._input_moved_during_request = True
            synthetic = result.get("schema") == PREDEAL_RESULT_SCHEMA and "explicit-composition" in str(
                (result.get("input") or {}).get("information_json") or "")
            if self.recomputed_from:
                self.live_applicable = False
                self._applicability_kind = KIND_SYNTHETIC if synthetic else KIND_STALE
                self._applicability_reason = None
            elif synthetic:
                self.live_applicable = False
                self._applicability_kind = KIND_SYNTHETIC
                self._applicability_reason = None
            elif result.get("schema") == OFFLINE_MC_RESULT_SCHEMA:
                self.live_applicable = False
                self._applicability_kind = KIND_MANUAL_ASOF
                self._applicability_reason = None
            else:
                self.live_applicable = bool(
                    ok and observation_unchanged and input_matches_now
                    and not self._observation_moved_during_request
                    and not self._input_moved_during_request)
                self._applicability_reason = None if self.live_applicable else (
                    REASON_INPUT if self._input_moved_during_request else reason)
                self._applicability_kind = (
                    KIND_LIVE_CURRENT if self.live_applicable else self._observation_kind())
            published = dict(result)
            if self.recomputed_from:
                published["knowledge_revision"] = None
            else:
                published["knowledge_revision"] = knowledge_revision_token(self.request_observation)
            self.last_result = published
            self.status.set(self._status_for_result(result))
            self._refresh_result_text()
            if result["status"] == AVAILABLE:
                self.retry_save()
            else:
                self.persistence.set("没有完成可保存的判断；未使用旧结果")
        self._poll_id = self.after(50, self._poll)

    def retry_save(self):
        if not self.recomputed_from and self.request_key != self._live_key():
            self._invalidate_current()
            return
        if self.saved:
            self.persistence.set("此结果已保存，未重复创建快照")
            return
        if not self.last_result or self.last_result["status"] != AVAILABLE:
            return
        try:
            timely, _reason = assess_timely_live_claim(
                self.last_result,
                live_applicable=self.live_applicable,
                historical=bool(self.recomputed_from),
                observation_moved=self._observation_moved_during_request,
                input_moved=self._input_moved_during_request,
                recomputed_from=self.recomputed_from,
            )
            timely = bool(
                timely
                and self._display_kind() == KIND_LIVE_CURRENT
            )
            self.saved = self.app.ctrl.analysis_store.save(
                self.last_result, self.recomputed_from, timely_live_claim=timely)
            self.persistence.set("已保存分析快照 " + self.saved["snapshot_id"][:10])
        except Exception as error:
            self.persistence.set("计算已完成，但快照未保存：" + str(error) + "；可重试保存，牌面记录未受影响")

    def cancel(self):
        self._cancel_auto()
        self._auto_suppressed_key = self._live_key()
        self.service.cancel()
        self.request_key = self.request_digest = self.request_id = None
        self.last_result = self.saved = None
        self.recomputed_from = None
        self.status.set("已取消：可继续录入或重新计算")
        self.persistence.set("本次未保存新判断；历史快照仍保留")
        self._set_text("")

    def show_history(self):
        entries, damaged = self.app.ctrl.analysis_store.list()
        win = tk.Toplevel(self.app)
        win.title("历史分析（原结果只读；重算创建新快照）")
        win.geometry("800x650")
        win.transient(self.app)
        listing = tk.Listbox(win, height=7, exportselection=False)
        listing.pack(fill=tk.X, padx=6, pady=4)
        display = tk.Text(win, wrap=tk.WORD, state=tk.DISABLED)
        display.pack(fill=tk.BOTH, expand=True, padx=6)
        for item in entries:
            r = item["result"]
            stamp = datetime.fromtimestamp(item["saved_at"]).strftime("%Y-%m-%d %H:%M:%S")
            identity = ("仅存储信封，无分析数值" if is_minimal_result(r) else
                        f"离线MC剩余{r['input'].get('physical_remaining')} · #{r['input']['through_seq']} · {r['engine_version']}"
                        if r.get("schema") == OFFLINE_MC_RESULT_SCHEMA else
                        f"发牌前剩余{r['input'].get('physical_remaining', sum(r['input'].get('counts') or ()))} · #{r['input']['through_seq']} · {r['engine_version']}"
                        if r.get("schema") == PREDEAL_RESULT_SCHEMA else
                        f"{r['input']['seat']} · #{r['input']['through_seq']} · {r['engine_version']}")
            listing.insert(tk.END, f"{stamp} · {identity} · {item['snapshot_id'][:8]}")
        def select(_event=None):
            indices = listing.curselection()
            if indices:
                display.configure(state=tk.NORMAL)
                display.delete("1.0", tk.END)
                result = entries[indices[0]]["result"]
                minimal = is_minimal_result(result)
                supported = result["schema"] in DISPLAY_RESULT_SCHEMAS
                text = ("这是兼容的旧存储信封，不包含完整分析结果，不能复算。\n输入摘要：" + result["input_digest"]
                        if minimal else "当前界面尚不支持此结果格式，原文件已保留，不能在此版本复算。"
                        if not supported else format_result(result, historical=True))
                display.insert("1.0", text)
                display.configure(state=tk.DISABLED)
                recompute_button.state(["disabled"] if minimal or not supported else ["!disabled"])
        listing.bind("<<ListboxSelect>>", select)
        def recompute():
            indices = listing.curselection()
            if not indices:
                return
            try:
                saved = entries[indices[0]]
                if (is_minimal_result(saved["result"])
                        or saved["result"]["schema"] not in DISPLAY_RESULT_SCHEMAS):
                    return
                snapshot = self.app.ctrl.recompute_input(saved)
                self.start(snapshot, saved["snapshot_id"])
                win.destroy()
            except Exception as error:
                messagebox.showerror("不能复算", str(error), parent=win)
        recompute_button = ttk.Button(win, text="按选中结果的原事件前缀重新计算", command=recompute)
        recompute_button.pack(pady=5)
        note = "无已保存分析" if not entries else "重算采用当前引擎，并保留原结果。"
        if damaged:
            note += f" 发现 {len(damaged)} 个损坏快照，已拒绝读取，请保留文件核对。"
            details = tk.Text(win, height=4, wrap=tk.WORD)
            details.insert("1.0", "\n".join(f"{item['file']}：{item['error']}" for item in damaged))
            details.configure(state=tk.DISABLED)
            details.pack(fill=tk.X, padx=6, pady=3)
        ttk.Label(win, text=note).pack(pady=3)
        if entries:
            listing.selection_set(len(entries) - 1)
            select()

    def close(self):
        self._closed = True
        obs = getattr(self.app, "observation", None)
        if obs is not None:
            obs.remove_listener(self._on_observation)
        self.app.ctrl.remove_context_listener(self.context_changed)
        for var, trace_id in self._target_traces:
            var.trace_remove("write", trace_id)
        self.auto.trace_remove("write", self._auto_trace)
        self.var_predeal_remaining.trace_remove("write", self._predeal_trace)
        for var, trace_id in getattr(self, "_rule_traces", ()):
            var.trace_remove("write", trace_id)
        self.after_cancel(self._poll_id)
        self._cancel_auto()
        self.service.close()
