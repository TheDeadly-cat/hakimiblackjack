"""Minimal Chinese contrast page for synthetic 6/7/8-deck experiments and history replay."""
from __future__ import annotations

import queue
import threading
import uuid
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from ..experiments.contracts import KIND_HISTORY, KIND_SYNTHETIC
from ..experiments.runner import ExperimentRunner
from ..experiments.scenarios import config_from_mapping

ACTION_ZH = {"stand": "停牌", "hit": "补牌", "double": "加倍", "split": "分牌",
             "surrender": "投降", "deal": "发牌/补牌等待", "complete": "两手完成"}
STATUS_ZH = {"available": "可用", "failed": "失败", "timeout": "超时",
             "cancelled": "已取消", "unsupported": "未支持", "inapplicable": "不适用",
             "pending": "待核对"}


class ExperimentWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("对照实验（合成场景 / 历史前缀）")
        self.geometry("920x640")
        self.runner = None
        self.thread = None
        self.saved = None
        self._queue = queue.Queue()
        self.var_mode = tk.StringVar(value="合成场景")
        self.var_template = tk.StringVar(value="single")
        self.var_player = tk.StringVar(value="10,6")
        self.var_up = tk.StringVar(value="10")
        self.var_removed = tk.StringVar(value="")
        self.var_decks = tk.StringVar(value="6,7,8")
        self.var_session = tk.StringVar(value="")
        self.var_seq = tk.StringVar(value="")
        self.var_status = tk.StringVar(value="固定移除已知牌面不等于实际经过若干完整轮次。")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)
        self._build()

    def _build(self):
        form = ttk.LabelFrame(self, text="实验配置")
        form.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        ttk.Label(form, text="入口").grid(row=0, column=0, sticky="w", padx=4, pady=3)
        ttk.Combobox(form, textvariable=self.var_mode, values=["合成场景", "历史前缀回放"],
                     state="readonly", width=18).grid(row=0, column=1, sticky="w")
        ttk.Label(form, text="模板").grid(row=0, column=2, sticky="w")
        ttk.Combobox(form, textvariable=self.var_template,
                     values=["single", "split", "das"], state="readonly", width=10).grid(row=0, column=3, sticky="w")
        ttk.Label(form, text="副数").grid(row=0, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.var_decks, width=12).grid(row=0, column=5, sticky="w")
        ttk.Label(form, text="玩家牌").grid(row=1, column=0, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.var_player, width=18).grid(row=1, column=1, sticky="w")
        ttk.Label(form, text="庄家明牌").grid(row=1, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.var_up, width=8).grid(row=1, column=3, sticky="w")
        ttk.Label(form, text="额外已知移除").grid(row=1, column=4, sticky="w")
        ttk.Entry(form, textvariable=self.var_removed, width=18).grid(row=1, column=5, sticky="w")
        ttk.Label(form, text="会话ID").grid(row=2, column=0, sticky="w", padx=4)
        ttk.Entry(form, textvariable=self.var_session, width=34).grid(row=2, column=1, columnspan=2, sticky="w")
        ttk.Label(form, text="事件序号").grid(row=2, column=3, sticky="w")
        ttk.Entry(form, textvariable=self.var_seq, width=8).grid(row=2, column=4, sticky="w")
        ttk.Label(form, text="历史回放只用当时已记录信息，不吸收后续揭牌或纠错。",
                  wraplength=880).grid(row=3, column=0, columnspan=6, sticky="w", padx=4, pady=4)

        bar = ttk.Frame(self)
        bar.grid(row=1, column=0, sticky="ew", padx=8)
        ttk.Button(bar, text="开始", command=self.start).pack(side=tk.LEFT)
        ttk.Button(bar, text="取消", command=self.cancel).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="导出目录", command=self.export_folder).pack(side=tk.LEFT)
        ttk.Label(bar, textvariable=self.var_status, wraplength=640).pack(side=tk.LEFT, padx=8)

        body = ttk.Frame(self)
        body.grid(row=2, column=0, sticky="nsew", padx=8, pady=6)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        columns = ("decks", "changed", "action", "status", "ev")
        self.table = ttk.Treeview(body, columns=columns, show="headings", height=16)
        self.table.heading("decks", text="副数")
        self.table.heading("changed", text="相对对照变化")
        self.table.heading("action", text="动作")
        self.table.heading("status", text="状态")
        self.table.heading("ev", text="EV")
        self.table.column("decks", width=60)
        self.table.column("changed", width=280)
        self.table.column("action", width=120)
        self.table.column("status", width=90)
        self.table.column("ev", width=120)
        scroll = ttk.Scrollbar(body, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

    def _config(self):
        mode = KIND_SYNTHETIC if self.var_mode.get() == "合成场景" else KIND_HISTORY
        data = {
            "kind": mode,
            "n_decks": self.var_decks.get(),
            "template": self.var_template.get(),
            "player_ranks": self.var_player.get(),
            "dealer_up": self.var_up.get(),
            "extra_removed": self.var_removed.get(),
            "seat": "玩家1",
            "session_id": self.var_session.get().strip() or None,
            "through_seq": int(self.var_seq.get()) if self.var_seq.get().strip() else None,
            "db_path": str(self.app.ctrl.store.db_path) if mode == KIND_HISTORY else None,
        }
        return config_from_mapping(data, uuid.uuid4().hex)

    def start(self):
        if self.thread and self.thread.is_alive():
            messagebox.showinfo("对照实验", "已有实验在运行", parent=self)
            return
        try:
            config = self._config()
        except Exception as error:
            messagebox.showerror("对照实验", str(error), parent=self)
            return
        self.table.delete(*self.table.get_children())
        self._expected = 1 if config.kind == KIND_HISTORY else max(1, len(config.n_decks))
        self.var_status.set(f"进度 0/{self._expected}：顺序执行，不并行启动大量原生进程。")
        output = Path(self.app.ctrl.store.db_path).resolve().parent / "experiments" / config.experiment_id
        self.runner = ExperimentRunner()
        def work():
            try:
                self._queue.put(("ok", self.runner.run(config, output)))
            except Exception as error:
                self._queue.put(("err", error))
        self.thread = threading.Thread(target=work, daemon=True)
        self.thread.start()
        self.after(50, self._poll)

    def _poll(self):
        done = len(self.runner.progress) if self.runner else 0
        expected = getattr(self, "_expected", 1)
        try:
            kind, payload = self._queue.get_nowait()
        except queue.Empty:
            if self.thread and self.thread.is_alive():
                self.var_status.set(f"进度 {done}/{expected}：顺序执行，不并行启动大量原生进程。")
                self.after(50, self._poll)
            return
        if kind == "ok":
            self._done(payload)
        else:
            self._fail(payload)

    def cancel(self):
        if self.runner:
            self.runner.cancel()
            self.var_status.set("已请求取消；已完成项会保留。")

    def export_folder(self):
        if not self.saved:
            messagebox.showinfo("对照实验", "还没有可导出的实验结果", parent=self)
            return
        messagebox.showinfo("对照实验", f"JSON：{self.saved['json']}\nCSV：{self.saved['csv']}", parent=self)

    def _fail(self, error):
        self.var_status.set(str(error))
        messagebox.showerror("对照实验", str(error), parent=self)

    def _done(self, saved):
        self.saved = saved
        self.table.delete(*self.table.get_children())
        items = saved["record"]["items"]
        baseline = next((item for item in items if item.get("status") == "available"), None)
        for item in items:
            changed = self._changes(baseline, item)
            actions = item.get("actions") or {"": {"status": item.get("status"), "ev": None}}
            for action, payload in actions.items():
                ev = payload.get("ev")
                self.table.insert("", tk.END, values=(
                    item.get("n_decks"),
                    changed,
                    ACTION_ZH.get(action, action or "（无动作）"),
                    STATUS_ZH.get(payload.get("status") or item.get("status"), item.get("status")),
                    "" if ev is None else f"{ev:+.6f}",
                ))
        self.var_status.set(f"完成。JSON {saved['json']}；CSV {saved['csv']}。未支持动作会保留原状态。")

    def _changes(self, baseline, item):
        if baseline is None or item is baseline:
            return "对照基准；规则模板与玩家/庄家牌面相同" if item.get("status") == "available" else (item.get("reason") or "")
        parts = []
        if item.get("n_decks") != baseline.get("n_decks"):
            parts.append(f"副数 {baseline.get('n_decks')}→{item.get('n_decks')}")
        else:
            parts.append("副数未变")
        parts.append("玩家牌/庄家明牌未变")
        if item.get("status") != "available":
            parts.append(item.get("reason") or item.get("status"))
        return "；".join(parts)
