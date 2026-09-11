"""Local analysis UI: real numbers, request state, and immutable historical results."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

from ..analysis.contracts import RESULT_SCHEMA, STATUS_ZH, ACTION_ZH, AVAILABLE, STALE, InputUnavailable
from ..analysis.service import AnalysisService
from ..storage.analysis_snapshots import is_minimal_result

DISPLAY_RESULT_SCHEMAS = {RESULT_SCHEMA}


def format_result(result, historical=False):
    info = result["input"]
    lines = [("历史分析 · " if historical else "当前 · ") +
             f"{info['seat']} · {' '.join(info['player_ranks'])} · 庄家 {info['dealer_up']} · {info['n_decks']}副"]
    if result["status"] != AVAILABLE:
        lines.append(f"{STATUS_ZH.get(result['status'], result['status'])}：{result['reason']}")
        return "\n".join(lines)
    if historical:
        lines.append("原时点结果，不代表当前输入")
    if result["partial_comparison"]:
        lines.append("部分动作比较（分牌缺失或合法性待核对）")
    lines.append("EV单位：原始1单位初始注的最终净收益")
    for action, item in result["actions"].items():
        value = f"  EV {item['ev']:+.6f}" if item["status"] == AVAILABLE else ""
        lines.append(f"{ACTION_ZH[action]}：{STATUS_ZH[item['status']]}{value}")
    if result["partial_comparison"]:
        lines.append("部分动作比较；缺少分牌EV或合法性待核对，不给唯一推荐。")
    elif result.get("highest_ev_action"):
        lines.append(f"在声明模型与当时信息下EV最高：{ACTION_ZH[result['highest_ev_action']]}")
    if result.get("all_computed_ev_negative"):
        lines.append("已计算动作EV均为负；较高只意味着可能少亏。")
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
    lines.extend(["", "S17 · 3:2 · 美式底牌 · 初始零烧牌",
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
        self.status = tk.StringVar(value="先选择研究模板并录入当前手牌")
        self.persistence = tk.StringVar(value="结果会独立保存，原始事件不变")
        self.auto = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=2)
        self.compute_button = ttk.Button(toolbar, text="计算当前手牌", command=self.calculate_current)
        self.compute_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(toolbar, text="取消", command=self.cancel)
        self.cancel_button.pack(side=tk.LEFT, padx=3)
        self.auto_button = ttk.Checkbutton(toolbar, text="自动", variable=self.auto)
        self.auto_button.pack(side=tk.LEFT)
        ttk.Label(self, textvariable=self.status, wraplength=300).grid(row=1, column=0, sticky="w", padx=3)
        ttk.Label(self, textvariable=self.persistence, wraplength=300, foreground="#555").grid(row=2, column=0, sticky="w", padx=3)
        body = ttk.Frame(self)
        body.grid(row=3, column=0, sticky="nsew")
        self.text = tk.Text(body, wrap=tk.WORD, state=tk.DISABLED, width=38, height=15, font=("Microsoft YaHei UI", 9))
        scroll = ttk.Scrollbar(body, command=self.text.yview)
        self.text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(fill=tk.BOTH, expand=True)
        footer = ttk.Frame(self)
        footer.grid(row=4, column=0, sticky="ew", pady=2)
        ttk.Button(footer, text="历史分析 / 复算", command=self.show_history).pack(side=tk.LEFT)
        ttk.Button(footer, text="重试保存", command=self.retry_save).pack(side=tk.LEFT, padx=3)
        self.app.ctrl.add_context_listener(self.context_changed)
        self._target_traces = [(var, var.trace_add("write", self.context_changed))
                               for var in (self.app.var_target, self.app.var_hand)]
        self._auto_trace = self.auto.trace_add("write", self._auto_changed)
        self._poll_id = self.after(50, self._poll)

    def _set_text(self, text):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", text)
        self.text.configure(state=tk.DISABLED)

    def _live_key(self):
        return (self.app.ctrl.context_token, self.app.var_target.get(), self.app.var_hand.get())

    def _cancel_auto(self):
        if self._auto_id:
            self.after_cancel(self._auto_id)
            self._auto_id = None

    def _invalidate_current(self):
        self.request_key = self.request_digest = self.request_id = None
        self.last_result = self.saved = None
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
        if (self.auto.get() and not self._closed and not self.recomputed_from
                and self._live_key() != self._auto_suppressed_key):
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

    def start(self, snapshot, recomputed_from=None, budget_seconds=5.0):
        self._cancel_auto()
        if not recomputed_from:
            segment = self.app._current_seg()
            hand_id = self.app._selected_hand_id(segment) if segment else None
            current = self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
            if current.input_digest != snapshot.input_digest:
                raise InputUnavailable("TARGET_CHANGED", "输入已改变，请按当前手牌重新计算")
        self.context_key = self._live_key()
        self.request_key = self.context_key
        self.request_digest = snapshot.input_digest
        self.recomputed_from = recomputed_from
        self.saved = None
        self.last_result = None
        self._set_text("正在按当时可见信息计算；可以继续录入或取消。")
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
        result = self.service.poll()
        current_matches = self.request_key is not None and self.request_key == self._live_key()
        if (result and (self.recomputed_from or current_matches)
                and result.get("request_id") == self.request_id
                and result.get("input_digest") == self.request_digest):
            self.last_result = result
            summary = result["reason"]
            if result["status"] == AVAILABLE:
                summary = ("部分动作比较，不给唯一推荐" if result["partial_comparison"] else
                           "已计算动作EV均为负" if result.get("all_computed_ev_negative") else "计算完成（所声明模型）")
            self.status.set(("历史复算 · " if self.recomputed_from else "") + STATUS_ZH[result["status"]] + "：" + summary)
            self._set_text(format_result(result, bool(self.recomputed_from)))
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
            self.saved = self.app.ctrl.analysis_store.save(self.last_result, self.recomputed_from)
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
        self.app.ctrl.remove_context_listener(self.context_changed)
        for var, trace_id in self._target_traces:
            var.trace_remove("write", trace_id)
        self.auto.trace_remove("write", self._auto_trace)
        self.after_cancel(self._poll_id)
        self._cancel_auto()
        self.service.close()
