"""Fullscreen evidence form and manual operator-study export. Empty is not a pass."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from ..capture.fullscreen_acceptance import ITEMS, empty_evidence, probe_environment, report
from ..observation.operator_study import append_export, optional_identity_fields, trial

_REPO_ROOT = Path(__file__).resolve().parents[2]
_M4_INBOX = _REPO_ROOT / ".local-evidence" / "m4-inbox"
_M4_PACK = _REPO_ROOT / ".local-evidence" / "m4-acceptance-pending.json"


def _drop_m4_inbox(item_id, path):
    """Copy a local export into the M4 inbox. Never marks the pack accepted."""
    src = Path(path)
    dest_dir = _M4_INBOX / item_id
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    if _M4_PACK.is_file():
        from ..analysis.acceptance_pack import ingest_inbox
        from ..analysis.evidence import write_manifest
        pack = json.loads(_M4_PACK.read_text(encoding="utf-8"))
        pack = ingest_inbox(pack, _M4_INBOX)
        if pack.get("accepted") or pack.get("passed"):
            raise RuntimeError("收件箱绑定不得把验收包写成通过")
        write_manifest(_M4_PACK, pack)
    return dest


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
        _drop_m4_inbox("fullscreen_f11", path)
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
    operator_id = simpledialog.askstring(
        "操作者ID", "可选。空则只记录条件，不能声称已配对：", parent=app)
    video_id = simpledialog.askstring(
        "录像ID", "可选。空则不能声称已配对：", parent=app)
    pair_id = simpledialog.askstring(
        "配对ID", "可选。空则不能声称已配对：", parent=app)
    path = filedialog.asksaveasfilename(
        parent=app, defaultextension=".json", initialfile="operator-study.json",
        filetypes=[("操作者对照", "*.json")])
    if not path:
        return None
    recorded = trial(
        condition, human_run=bool(human), elapsed_seconds=elapsed, keystrokes=keys,
        clicks=clicks, backlog_peak=backlog, missed_cards=missed, duplicates=duplicates,
        repair_seconds=repair, pause_reconcile_not_realtime=True,
        **optional_identity_fields(operator_id=operator_id, video_id=video_id, pair_id=pair_id))
    body = append_export(path, recorded)
    _drop_m4_inbox("operator_pairs", path)
    app.set_status(
        "已追加操作者对照；accepted=" + str(body["accepted"])
        + " declared_pair_ids=" + str(body.get("declared_pair_ids"))
        + " " + str(body.get("reason_code")))
    return body


class ScopeSignoffDialog(tk.Toplevel):
    """Human records a bounded confirmation. Software cannot fill the name or phrase."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("范围内人工签收（软件不能代签）")
        self.geometry("640x420")
        from ..analysis.acceptance_pack import (
            HUMAN_CONFIRMATION_PHRASE, SCOPE_LABELS, SCOPES,
        )
        self._scopes = SCOPES
        self._labels = SCOPE_LABELS
        self._phrase = HUMAN_CONFIRMATION_PHRASE
        ttk.Label(
            self, wraplength=600,
            text="技术完成、范围内签收和发布授权不是同一个勾选。"
                 "软件只保存你写下的确认，不会把全局验收包标成通过。",
        ).pack(anchor="w", padx=8, pady=6)
        form = ttk.Frame(self)
        form.pack(fill=tk.BOTH, expand=True, padx=8)
        self.var_scope = tk.StringVar(value=SCOPE_LABELS[SCOPES[0]])
        self.var_name = tk.StringVar(value="")
        self.var_commit = tk.StringVar(value="")
        self.var_criteria = tk.StringVar(value="")
        self.var_result = tk.StringVar(value="accepted")
        self.var_phrase = tk.StringVar(value="")
        ttk.Label(form, text="范围").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            form, textvariable=self.var_scope, state="readonly", width=36,
            values=[SCOPE_LABELS[scope] for scope in SCOPES],
        ).grid(row=0, column=1, sticky="w", pady=3)
        ttk.Label(form, text="确认人姓名").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_name, width=40).grid(row=1, column=1, sticky="w")
        ttk.Label(form, text="代码版本").grid(row=2, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_commit, width=40).grid(row=2, column=1, sticky="w")
        ttk.Label(form, text="验收标准").grid(row=3, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.var_criteria, width=40).grid(row=3, column=1, sticky="w")
        ttk.Label(form, text="结果").grid(row=4, column=0, sticky="w")
        ttk.Combobox(
            form, textvariable=self.var_result, state="readonly", width=18,
            values=("accepted", "rejected", "deferred"),
        ).grid(row=4, column=1, sticky="w")
        ttk.Label(
            form, wraplength=520,
            text="请抄写：" + HUMAN_CONFIRMATION_PHRASE,
        ).grid(row=5, column=0, columnspan=2, sticky="w", pady=(8, 2))
        ttk.Entry(form, textvariable=self.var_phrase, width=56).grid(
            row=6, column=0, columnspan=2, sticky="ew")
        ttk.Button(form, text="保存范围内签收", command=self.save).grid(
            row=7, column=0, columnspan=2, pady=12)

    def save(self):
        from ..analysis.acceptance_pack import (
            SCOPE_LABELS, SCOPES, freeze_status, record_scope_signoff,
        )
        from ..analysis.evidence import write_manifest
        label = self.var_scope.get()
        scope = next(item for item in SCOPES if SCOPE_LABELS[item] == label)
        pack = {"schema": "hakimi-m4-acceptance-pack-v1", "items": {}, "scope_signoffs": []}
        if _M4_PACK.is_file():
            pack = json.loads(_M4_PACK.read_text(encoding="utf-8"))
        try:
            pack = record_scope_signoff(
                pack, scope=scope, attested_by=self.var_name.get(),
                code_commit=self.var_commit.get(), criteria=self.var_criteria.get(),
                result=self.var_result.get(), confirmation_phrase=self.var_phrase.get(),
                recorded_by="ui-scope-signoff")
        except Exception as error:
            messagebox.showerror("不能签收", str(error), parent=self)
            return
        _M4_PACK.parent.mkdir(parents=True, exist_ok=True)
        write_manifest(_M4_PACK, pack)
        status = freeze_status(
            code_commit=self.var_commit.get().strip() or None, m4_pack=pack, scope=scope)
        self.app.set_status(
            f"已保存{SCOPE_LABELS[scope]}签收；全局accepted={pack['accepted']}；"
            f"该范围scope_accepted={status['scope_accepted']}")
        messagebox.showinfo(
            "范围内签收",
            f"已保存。全局验收包仍为未通过。\n该范围：{SCOPE_LABELS[scope]}\n"
            f"scope_accepted={status['scope_accepted']}\nready还取决于干净SHA与绑定测试。",
            parent=self)
        self.destroy()
