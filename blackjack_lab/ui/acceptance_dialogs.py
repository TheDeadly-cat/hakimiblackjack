"""Fullscreen evidence form and manual operator-study export. Empty is not a pass."""
from __future__ import annotations

import json
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..capture.fullscreen_acceptance import ITEMS, empty_evidence, probe_environment, report
from ..observation.operator_study import trial, write_export


class FullscreenAcceptanceDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("全屏验收清单（缺证据即未通过）")
        self.geometry("640x520")
        self.evidence = empty_evidence()
        self.vars = {}
        ttk.Label(self, wraplength=600,
                  text="不能用浮层截图换签进入模型的源帧。环境探测不是通过。").pack(anchor="w", padx=8, pady=6)
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True, padx=8)
        for item in ITEMS:
            row = ttk.LabelFrame(body, text=item)
            row.pack(fill=tk.X, pady=3)
            passed = tk.BooleanVar(value=False)
            path = tk.StringVar(value="")
            notes = tk.StringVar(value="")
            self.vars[item] = {"passed": passed, "path": path, "notes": notes}
            ttk.Checkbutton(row, text="本项已在用户浏览器下通过（须有源帧证据路径）",
                            variable=passed).pack(anchor="w")
            line = ttk.Frame(row)
            line.pack(fill=tk.X)
            ttk.Entry(line, textvariable=path).pack(side=tk.LEFT, fill=tk.X, expand=True)
            ttk.Button(line, text="证据文件",
                       command=lambda name=item: self._browse(name)).pack(side=tk.LEFT, padx=4)
            ttk.Entry(row, textvariable=notes).pack(fill=tk.X, pady=2)
        ttk.Button(self, text="保存清单（未齐备则 accepted=false）", command=self.save).pack(pady=8)

    def _browse(self, item):
        chosen = filedialog.askopenfilename(parent=self, title="源帧或热键证据")
        if chosen:
            self.vars[item]["path"].set(chosen)

    def collect(self):
        evidence = {}
        for item, fields in self.vars.items():
            path = fields["path"].get().strip() or None
            evidence[item] = {
                "passed": bool(fields["passed"].get()),
                "evidence_path": path,
                "notes": fields["notes"].get().strip(),
            }
        return evidence

    def save_to(self, path):
        body = report(self.collect(), probe_environment(self.app))
        Path(path).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        return body

    def save(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json", initialfile="fullscreen-acceptance.json",
            filetypes=[("全屏验收", "*.json")])
        if not path:
            return
        body = self.save_to(path)
        self.app.set_status("已保存全屏验收清单；accepted=" + str(body["accepted"]))
        messagebox.showinfo("全屏验收", body["note"] + "\naccepted=" + str(body["accepted"]), parent=self)


def export_operator_trials(app):
    human = messagebox.askyesno(
        "操作者对照", "这是配对真人试验记录吗？选否则保持结论：不能改自动提示默认。", parent=app)
    if not messagebox.askyesno(
        "实时声明", "确认：暂停后的对账成功不算实时跟上。必须选是才能导出。", parent=app):
        app.set_status("未导出：必须声明暂停对账不算实时跟上")
        return None
    condition = simpledialog.askstring(
        "对照条件", "manual 或 assisted：", initialvalue="manual", parent=app)
    if condition not in ("manual", "assisted"):
        messagebox.showerror("操作者对照", "条件只能是 manual 或 assisted", parent=app)
        return None
    elapsed = simpledialog.askfloat("用时秒", "本段用时（秒）：", parent=app, minvalue=0)
    keys = simpledialog.askinteger("按键", "按键次数：", parent=app, minvalue=0)
    clicks = simpledialog.askinteger("点击", "点击次数：", parent=app, minvalue=0)
    backlog = simpledialog.askinteger("积压峰值", "待核对积压峰值：", parent=app, minvalue=0)
    missed = simpledialog.askinteger("漏牌次数", "本段漏牌次数：", parent=app, minvalue=0)
    duplicates = simpledialog.askinteger("重复次数", "本段重复记录次数：", parent=app, minvalue=0)
    repair = simpledialog.askinteger("修复耗时秒", "回溯修复耗时（秒）：", parent=app, minvalue=0)
    if None in (elapsed, keys, clicks, backlog, missed, duplicates, repair):
        return None
    path = filedialog.asksaveasfilename(
        parent=app, defaultextension=".json", initialfile="operator-study.json",
        filetypes=[("操作者对照", "*.json")])
    if not path:
        return None
    recorded = trial(
        condition, human_run=bool(human), elapsed_seconds=elapsed, keystrokes=keys,
        clicks=clicks, backlog_peak=backlog, missed_cards=missed, duplicates=duplicates,
        repair_seconds=repair, pause_reconcile_not_realtime=True)
    body = write_export(path, [recorded])
    app.set_status("已导出操作者对照；auto_prompt_default=" + str(body["auto_prompt_default"]))
    return body
