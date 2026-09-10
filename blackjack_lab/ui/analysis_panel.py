"""Local analysis UI: real numbers, request state, and immutable historical results."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

from ..analysis.contracts import STATUS_ZH, ACTION_ZH, AVAILABLE, STALE, InputUnavailable
from ..analysis.service import AnalysisService


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
        self.last_result = None
        self.saved = None
        self.recomputed_from = None
        self._auto_id = None
        self.status = tk.StringVar(value="先选择研究模板并录入当前手牌")
        self.persistence = tk.StringVar(value="结果会独立保存，原始事件不变")
        self.auto = tk.BooleanVar(value=False)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)
        toolbar = ttk.Frame(self)
        toolbar.grid(row=0, column=0, sticky="ew", pady=2)
        self.compute_button = ttk.Button(toolbar, text="计算当前手牌", command=self.calculate_current)
        self.compute_button.pack(side=tk.LEFT)
        ttk.Button(toolbar, text="取消", command=self.cancel).pack(side=tk.LEFT, padx=3)
        ttk.Checkbutton(toolbar, text="自动", variable=self.auto).pack(side=tk.LEFT)
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
        self._poll_id = self.after(50, self._poll)

    def _set_text(self, text):
        self.text.configure(state=tk.NORMAL)
        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", text)
        self.text.configure(state=tk.DISABLED)

    def on_context(self, segment):
        ledger = self.app.ctrl.ledger
        hand_id = self.app._selected_hand_id(segment) if segment else None
        key = (self.app.ctrl.session_id, self.app.ctrl.commit_revision,
               ledger.events[-1].event_id if ledger.events else None, self.app.var_target.get(), hand_id)
        if key == self.context_key:
            return
        self.context_key = key
        if self._auto_id:
            self.after_cancel(self._auto_id)
            self._auto_id = None
        if self.service.active or self.last_result:
            self.service.cancel(STALE)
            self.last_result = None
            self.saved = None
            self.status.set("过期：牌面、目标或会话已变化")
            self._set_text("旧请求与当前输入不匹配，已移除当前数值。已保存的原结果仍可在历史分析中查看。")
        try:
            self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
        except InputUnavailable as error:
            self.status.set(f"{STATUS_ZH[error.status]}：{error.reason}")
            self.compute_button.state(["disabled"])
        else:
            self.compute_button.state(["!disabled"])
            if not self.last_result:
                self.status.set("可计算：当前单手，正常底牌仍未揭示")
            if self.auto.get():
                self._auto_id = self.after(250, self.calculate_current)

    def calculate_current(self):
        self._auto_id = None
        try:
            segment = self.app._current_seg()
            hand_id = self.app._selected_hand_id(segment) if segment else None
            snapshot = self.app.ctrl.analysis_input(self.app.var_target.get(), hand_id)
            self.start(snapshot)
        except Exception as error:
            self.service.cancel(STALE)
            self.last_result = None
            self._set_text("")
            self.status.set(f"待核对：{error}")

    def start(self, snapshot, recomputed_from=None, budget_seconds=5.0):
        self.request_key = self.context_key
        self.recomputed_from = recomputed_from
        self.saved = None
        self.last_result = None
        self._set_text("正在按当时可见信息计算；可以继续录入或取消。")
        self.persistence.set("等待当前计算完成")
        self.service.start(snapshot, budget_seconds)
        self.status.set(("历史复算中" if recomputed_from else "计算中") + f" · 前缀 #{snapshot.through_seq}")

    def _poll(self):
        result = self.service.poll()
        if result and self.request_key == self.context_key:
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
        self.service.cancel()
        self.last_result = None
        self.status.set("已取消：可继续录入或重新计算")
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
            listing.insert(tk.END, f"{stamp} · {r['input']['seat']} · #{r['input']['through_seq']} · {r['engine_version']} · {item['snapshot_id'][:8]}")
        def select(_event=None):
            indices = listing.curselection()
            if indices:
                display.configure(state=tk.NORMAL)
                display.delete("1.0", tk.END)
                display.insert("1.0", format_result(entries[indices[0]]["result"], historical=True))
                display.configure(state=tk.DISABLED)
        listing.bind("<<ListboxSelect>>", select)
        def recompute():
            indices = listing.curselection()
            if not indices:
                return
            try:
                saved = entries[indices[0]]
                snapshot = self.app.ctrl.recompute_input(saved)
                self.start(snapshot, saved["snapshot_id"])
                win.destroy()
            except Exception as error:
                messagebox.showerror("不能复算", str(error), parent=win)
        ttk.Button(win, text="按选中结果的原事件前缀重新计算", command=recompute).pack(pady=5)
        note = "无已保存分析" if not entries else "重算采用当前引擎，并保留原结果。"
        if damaged:
            note += f" 发现 {len(damaged)} 个损坏快照，已拒绝读取，请保留文件核对。"
        ttk.Label(win, text=note).pack(pady=3)
        if entries:
            listing.selection_set(len(entries) - 1)
            select()

    def close(self):
        self.after_cancel(self._poll_id)
        if self._auto_id:
            self.after_cancel(self._auto_id)
        self.service.close()
