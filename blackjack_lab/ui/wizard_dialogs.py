"""Wizard dialogs: one action at a time. Software records files, never accepts."""
from __future__ import annotations

import json
from pathlib import Path
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ..analysis.shoe_event_draft import (
    add_unknown_card, composition_status, confirm_page, confirm_round_coverage,
    coverage_attestation_valid, development_clip_stub,
    keep_unknown, load_draft, review_pages, set_event_rank, set_event_seat, write_draft,
)
from ..capture.fullscreen_acceptance import probe_environment
from ..capture.fullscreen_wizard import (
    as_fullscreen_evidence, conclude_session, current_step, record_step,
    skip_untested, start_session as start_fullscreen,
)
from ..capture.window_list import observe_foreground
from ..capture.foreground_still import grab_foreground_still
from ..core.table_rules_diff import (
    ROW_DECISION_MATCH, ROW_DECISION_MISMATCH, ROW_DECISION_UNSURE,
    from_felt_observation, load_observation, printed_felt_observation,
    record_row_decision, review_rows, write_diff,
)
from ..core.table import DEALER
from ..analysis.shoe_round_pages import crop_style_region
from ..ledger.draft_import import DraftImportError, apply_event_draft
from ..observation.operator_wizard import (
    export_session, record_leg, start_session as start_operator,
)
from .acceptance_dialogs import _drop_m4_inbox

_WIZARD_STILLS = Path(__file__).resolve().parents[2] / ".local-evidence" / "fullscreen-wizard-stills"
_DEV_CLIP = "Desktop 2026.09.12 - 12.58.11.02.mp4"
_LOCAL_DRAFT = Path(__file__).resolve().parents[2] / ".local-evidence" / "dev-clip-12.58.11.02-event-draft.json"
_LOCAL_FELT = Path(__file__).resolve().parents[2] / ".local-evidence" / "dev-clip-12.58.11.02-rounds" / "felt" / "felt-046080.png"
_CROP_DIR = Path(__file__).resolve().parents[2] / ".local-evidence" / "event-draft-crops"


def usage_folder(app):
    session = getattr(app, "usage_session", None)
    if not isinstance(session, dict) or not session.get("folder"):
        return None
    folder = Path(session["folder"])
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def append_usage_event(app, kind, **fields):
    """Append one usage-test event. Never marks accepted."""
    folder = usage_folder(app)
    if folder is None:
        return None
    ctrl = getattr(app, "ctrl", None)
    ledger = getattr(ctrl, "ledger", None)
    payload = {
        "kind": kind,
        "t": time.time(),
        "accepted": False,
        "session_id": getattr(ctrl, "session_id", None),
        "commit_revision": getattr(ctrl, "commit_revision", None),
        "event_count": len(getattr(ledger, "events", None) or []),
    }
    payload.update(fields)
    with (folder / "wizard-log.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return folder


def write_usage_json(app, name, body):
    folder = usage_folder(app)
    if folder is None:
        return None
    path = folder / name
    payload = dict(body or {})
    payload["accepted"] = False
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _dialog_alive(dialog):
    return dialog is not None and bool(getattr(dialog, "winfo_exists", lambda: False)())


def _load_usage_json(app, name):
    folder = usage_folder(app)
    if folder is None:
        return {}
    path = folder / name
    if not path.is_file():
        return {}
    try:
        body = json.loads(path.read_text(encoding="utf-8"))
    except (TypeError, ValueError, OSError):
        return {}
    return body if isinstance(body, dict) else {}


def _usage_wizard_session(app):
    wizard = getattr(app, "_fullscreen_wizard", None)
    if _dialog_alive(wizard):
        return dict(wizard.session or {})
    saved = _load_usage_json(app, "fullscreen-wizard-live.json") or _load_usage_json(
        app, "fullscreen-wizard.json")
    return dict(saved.get("wizard") or saved or {})


def _usage_operator_session(app):
    operator = getattr(app, "_operator_wizard", None)
    if _dialog_alive(operator):
        return dict(operator.session or {})
    saved = _load_usage_json(app, "operator-pair-wizard.json")
    return dict(saved or {})


def _enter_f11_observation(wizard_session):
    for row in wizard_session.get("steps") or []:
        if row.get("id") != "enter_f11":
            continue
        observation = row.get("observation") or {}
        still = observation.get("still") if isinstance(observation, dict) else {}
        return {
            "recorded": row.get("status") == "recorded",
            "looks_monitor_sized": bool(
                isinstance(observation, dict) and observation.get("looks_monitor_sized")),
            "still_capture_ok": bool(isinstance(still, dict) and still.get("capture_ok")),
            "foreground": observation.get("foreground") if isinstance(observation, dict) else None,
        }
    return {
        "recorded": False,
        "looks_monitor_sized": False,
        "still_capture_ok": False,
        "foreground": None,
    }


def _current_hand_ranks(ctrl):
    try:
        current = ctrl.state().current
        if current is None or current.table is None:
            return []
        rows = []
        for seat, player in (current.table.players or {}).items():
            for hand in player.hands:
                rows.append({"seat": seat, "ranks": list(hand.ranks)})
        return rows
    except Exception:
        return []


def _usage_source_identity():
    try:
        from ..analysis.experiment_export import _source_identity
        return _source_identity(Path(__file__).resolve().parents[2])
    except Exception:
        return {"commit": None, "dirty_worktree": True, "kind": "unversioned-directory"}


def write_usage_snapshot(app, reason):
    ctrl = getattr(app, "ctrl", None)
    ledger = getattr(ctrl, "ledger", None)
    wizard_session = _usage_wizard_session(app)
    operator_session = _usage_operator_session(app)
    enter_f11 = _enter_f11_observation(wizard_session)
    session = getattr(app, "usage_session", None) or {}
    manifest = session.get("manifest") or {}
    db_path = str(session.get("db") or getattr(getattr(ctrl, "store", None), "db_path", "") or "")
    default_db = str(manifest.get("default_user_db") or "")
    body = {
        "schema": "hakimi-usage-run-v1",
        "reason": reason,
        "accepted": False,
        "passed": False,
        "live_catchup": False,
        "pause_and_fill_is_not_realtime": True,
        "software_cannot_claim_f11": True,
        "session_id": getattr(ctrl, "session_id", None),
        "commit_revision": getattr(ctrl, "commit_revision", None),
        "event_count": len(getattr(ledger, "events", None) or []),
        "current_hands": _current_hand_ranks(ctrl),
        "fullscreen_required_complete": bool(wizard_session.get("required_complete")),
        "fullscreen_recorded_steps": list(wizard_session.get("recorded_steps") or []),
        "enter_f11": enter_f11,
        "operator_pair_id": ((operator_session.get("identity") or {}).get("pair_id")),
        "db": db_path,
        "default_user_db": default_db,
        "uses_user_default_db": bool(db_path and default_db and db_path == default_db),
        "source_identity": _usage_source_identity(),
        "note": (
            "实测快照，不是验收通过。向导点完不等于 F11 已验收；"
            "暂停后补齐不能当成实时跟上。"
        ),
    }
    append_usage_event(
        app, "snapshot", reason=reason,
        fullscreen_required_complete=body["fullscreen_required_complete"],
        operator_pair_id=body["operator_pair_id"],
        looks_monitor_sized=enter_f11["looks_monitor_sized"])
    write_usage_json(app, "usage_run.json", body)
    return write_usage_json(app, "usage-final.json", body)


def _still_dir(app):
    folder = usage_folder(app)
    if folder is None:
        return _WIZARD_STILLS
    dest = folder / "stills"
    dest.mkdir(parents=True, exist_ok=True)
    return dest


class FullscreenWizardDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("全屏验收向导（一步一动作，不能代签）")
        self.geometry("620x360")
        env = probe_environment(app)
        self.session = start_fullscreen(environment=env, recorded_by="ui-fullscreen-wizard")
        self.var_prompt = tk.StringVar()
        self.var_notes = tk.StringVar()
        ttk.Label(
            self, wraplength=580,
            text="每次只做当前这一步。程序记录时间和环境；DPI/多屏未测可保留未测。"
                 "完成全部必做步也不等于 F11 已验收。",
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Label(self, textvariable=self.var_prompt, wraplength=580).pack(anchor="w", padx=8)
        ttk.Entry(self, textvariable=self.var_notes).pack(fill=tk.X, padx=8, pady=6)
        row = ttk.Frame(self)
        row.pack(pady=8)
        ttk.Button(row, text="本步已完成", command=self._done).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="本步失败", command=self._fail).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="未测（仅可选）", command=self._skip).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="保存证据", command=self._save).pack(side=tk.LEFT, padx=4)
        self.var_status = tk.StringVar()
        ttk.Label(self, textvariable=self.var_status, wraplength=580).pack(anchor="w", padx=8)
        self._refresh()
        append_usage_event(self.app, "fullscreen_wizard_opened")

    def _refresh(self):
        conclude_session(self.session)
        step = current_step(self.session)
        if step is None:
            self.var_prompt.set("必做步骤已走完。保存证据后仍须人工签收该范围。")
        else:
            kind = "必做" if step["required"] else "可选"
            self.var_prompt.set(f"[{kind}] {step['prompt']}")
        self.var_status.set(
            f"已记录 {len(self.session.get('recorded_steps') or [])} 步；"
            f"必做齐备={self.session.get('required_complete')}；accepted=false")

    def _observation(self):
        env = self.session.get("environment") or {}
        body = observe_foreground(
            screen_width=env.get("screen_width") or env.get("primary_width"),
            screen_height=env.get("screen_height") or env.get("primary_height"),
        )
        info = body.get("foreground") or {}
        hwnd = info.get("hwnd")
        bbox = None
        if info.get("width") and info.get("height"):
            bbox = (info.get("x") or 0, info.get("y") or 0,
                    (info.get("x") or 0) + info["width"],
                    (info.get("y") or 0) + info["height"])
        still = grab_foreground_still(
            _still_dir(self.app), hwnd=hwnd, bbox=bbox,
            step_id=(current_step(self.session) or {}).get("id") or "step")
        body["still"] = {key: still.get(key) for key in (
            "capture_ok", "path", "sha256", "bytes", "error", "not_acceptance")}
        body["not_acceptance"] = True
        return body, still.get("path") if still.get("capture_ok") else None

    def _done(self):
        step = current_step(self.session)
        if step is None:
            return
        observation, evidence_path = self._observation()
        record_step(
            self.session, step["id"], notes=self.var_notes.get(),
            observation=observation, evidence_path=evidence_path)
        self._persist_fullscreen("recorded", step["id"], observation)
        self.var_notes.set("")
        self._refresh()

    def _fail(self):
        step = current_step(self.session)
        if step is None:
            return
        observation, evidence_path = self._observation()
        record_step(
            self.session, step["id"], notes=self.var_notes.get(), failed=True,
            observation=observation, evidence_path=evidence_path)
        self._persist_fullscreen("failed", step["id"], observation)
        self.var_notes.set("")
        self._refresh()

    def _skip(self):
        step = current_step(self.session)
        if step is None:
            return
        try:
            skip_untested(
                self.session, step["id"],
                reason=self.var_notes.get() or "单屏用户未测，不为此购买硬件")
        except ValueError as error:
            messagebox.showerror("不能标未测", str(error), parent=self)
            return
        self._persist_fullscreen("skipped", step["id"], None)
        self.var_notes.set("")
        self._refresh()

    def _persist_fullscreen(self, status, step_id, observation):
        body = as_fullscreen_evidence(self.session)
        write_usage_json(self.app, "fullscreen-wizard-live.json", body)
        still = None
        if isinstance(observation, dict):
            still = (observation.get("still") or {}).get("path")
        append_usage_event(
            self.app, "fullscreen_step", status=status, step_id=step_id,
            capture_ok=bool(((observation or {}).get("still") or {}).get("capture_ok"))
            if isinstance(observation, dict) else None,
            evidence_path=still,
            required_complete=bool(self.session.get("required_complete")))

    def _save(self):
        body = as_fullscreen_evidence(self.session)
        path = write_usage_json(self.app, "fullscreen-wizard.json", body)
        if path is None:
            chosen = filedialog.asksaveasfilename(
                parent=self, defaultextension=".json", initialfile="fullscreen-wizard.json",
                filetypes=[("全屏向导", "*.json")])
            if not chosen:
                return
            path = Path(chosen)
            path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
        _drop_m4_inbox("fullscreen_f11", path)
        append_usage_event(self.app, "fullscreen_saved", path=str(path))
        write_usage_snapshot(self.app, "fullscreen_save")
        self.app.set_status("已保存全屏向导证据；accepted=" + str(body["accepted"]))
        messagebox.showinfo("全屏向导", body["note"] + "\naccepted=" + str(body["accepted"]), parent=self)


class OperatorWizardDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("配对操作向导（程序生成 ID）")
        self.geometry("640x420")
        human = messagebox.askyesno(
            "操作者对照", "这是配对真人试验吗？选否则保持不能改自动提示默认。", parent=self)
        self.human_run = bool(human)
        if not messagebox.askyesno(
                "实时声明", "确认：暂停后的对账成功不算实时跟上。必须选是才能继续。", parent=self):
            self.destroy()
            return
        usage = getattr(getattr(app, "_quick_panel", None), "usage", None) or {}
        video_label = simple_video_label(app)
        self.session = start_operator(video_label=video_label)
        ident = self.session["identity"]
        ttk.Label(
            self, wraplength=600,
            text="下面三个 ID 由程序生成，不必手写。先做手动段，再做辅助段。",
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Label(
            self,
            text=f"operator_id={ident['operator_id']}\n"
                 f"video_id={ident['video_id']}\n"
                 f"pair_id={ident['pair_id']}",
        ).pack(anchor="w", padx=8)
        self.var_prompt = tk.StringVar()
        ttk.Label(self, textvariable=self.var_prompt, wraplength=600).pack(anchor="w", padx=8, pady=6)
        form = ttk.Frame(self)
        form.pack(fill=tk.X, padx=8)
        self.vars = {}
        for row, (name, label, default) in enumerate((
            ("elapsed_seconds", "用时秒", "0"),
            ("keystrokes", "按键", str(int(usage.get("key_presses") or 0))),
            ("clicks", "点击", str(int(usage.get("mouse_clicks") or 0))),
            ("backlog_peak", "待核对积压峰值", str(int(usage.get("max_pending") or 0))),
            ("missed_cards", "漏牌", "0"),
            ("duplicates", "重复", "0"),
            ("repair_seconds", "纠错耗时秒", "0"),
        )):
            ttk.Label(form, text=label).grid(row=row, column=0, sticky="w")
            var = tk.StringVar(value=default)
            self.vars[name] = var
            ttk.Entry(form, textvariable=var, width=12).grid(row=row, column=1, sticky="w")
        ttk.Button(self, text="完成本段并进入下一段", command=self._complete_leg).pack(pady=6)
        ttk.Button(self, text="导出配对文件", command=self._export).pack()
        self.started = time.time()
        self._refresh()
        append_usage_event(
            self.app, "operator_wizard_opened",
            operator_id=ident["operator_id"], video_id=ident["video_id"],
            pair_id=ident["pair_id"])
        write_usage_json(self.app, "operator-pair-live.json", {
            "identity": ident, "accepted": False, "paired": False,
        })

    def _refresh(self):
        leg = self.session.get("current_leg")
        if leg is None:
            self.var_prompt.set("两段都已记录。导出后仍不是已认证配对，也不能改自动提示默认。")
        else:
            self.var_prompt.set(f"当前：{leg}。{self.session['prompts'][leg]}")
        elapsed = max(0.0, time.time() - self.started)
        self.vars["elapsed_seconds"].set(f"{elapsed:.1f}")

    def _metrics(self):
        def number(name, integer=False):
            raw = self.vars[name].get().strip()
            return int(float(raw)) if integer else float(raw)
        return dict(
            elapsed_seconds=number("elapsed_seconds"),
            keystrokes=number("keystrokes", True),
            clicks=number("clicks", True),
            backlog_peak=number("backlog_peak", True),
            missed_cards=number("missed_cards", True),
            duplicates=number("duplicates", True),
            repair_seconds=number("repair_seconds", True),
        )

    def _complete_leg(self):
        leg = self.session.get("current_leg")
        if not leg:
            return
        try:
            record_leg(self.session, leg, human_run=self.human_run, **self._metrics())
        except Exception as error:
            messagebox.showerror("不能记录", str(error), parent=self)
            return
        self.started = time.time()
        self._refresh()
        append_usage_event(self.app, "operator_leg", leg=leg, next_leg=self.session.get("current_leg"))
        write_usage_json(self.app, "operator-pair-live.json", {
            "identity": self.session.get("identity"),
            "accepted": False,
            "paired": False,
            "current_leg": self.session.get("current_leg"),
        })

    def _export(self):
        folder = usage_folder(self.app)
        if folder is not None:
            path = folder / "operator-pair-wizard.json"
        else:
            chosen = filedialog.asksaveasfilename(
                parent=self, defaultextension=".json", initialfile="operator-pair-wizard.json",
                filetypes=[("操作者对照", "*.json")])
            if not chosen:
                return
            path = chosen
        try:
            body = export_session(self.session, path)
        except Exception as error:
            messagebox.showerror("不能导出", str(error), parent=self)
            return
        _drop_m4_inbox("operator_pairs", path)
        append_usage_event(
            self.app, "operator_exported", path=str(path),
            pair_id=(self.session.get("identity") or {}).get("pair_id"))
        self.app.set_status(
            "已导出配对向导；accepted=" + str(body["accepted"])
            + " paired=" + str(body["paired"]))
        messagebox.showinfo(
            "配对向导",
            f"accepted={body['accepted']} paired={body['paired']}\n{body.get('reason_code')}",
            parent=self)


def simple_video_label(app):
    return "authorized-dev-clip"


class RulesDiffDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("规则差异表（一次一行，不是已验收桌规）")
        self.geometry("720x460")
        self.body = None
        self.row_index = 0
        ttk.Label(
            self, wraplength=680,
            text="请打开目标桌帮助/规则页。每次只核一行不确定项。"
                 "公开规则页只是待确认候选；副数保持空白，不默认为 6，也不把公开页常见的 8 写成该桌。",
        ).pack(anchor="w", padx=8, pady=6)
        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, padx=8)
        ttk.Button(nav, text="上一行", command=self._prev_row).pack(side=tk.LEFT)
        self.var_title = tk.StringVar()
        ttk.Label(nav, textvariable=self.var_title).pack(side=tk.LEFT, padx=12)
        ttk.Button(nav, text="下一行", command=self._next_row).pack(side=tk.LEFT)
        self.text = tk.Text(self, height=14, wrap="word")
        self.text.pack(fill=tk.BOTH, expand=True, padx=8)
        form = ttk.Frame(self)
        form.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(form, text="确认人姓名").pack(side=tk.LEFT)
        self.var_name = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_name, width=16).pack(side=tk.LEFT, padx=4)
        ttk.Button(form, text="与该桌相符", command=lambda: self._decide(ROW_DECISION_MATCH)).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(form, text="与该桌不符", command=lambda: self._decide(ROW_DECISION_MISMATCH)).pack(
            side=tk.LEFT, padx=4)
        ttk.Button(form, text="仍不确定", command=lambda: self._decide(ROW_DECISION_UNSURE)).pack(
            side=tk.LEFT, padx=4)
        row = ttk.Frame(self)
        row.pack(pady=6)
        ttk.Button(row, text="从开发片绒面载入", command=self._load_dev).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="从绒面观察载入", command=self._load).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="保存差异表", command=self._save).pack(side=tk.LEFT, padx=4)
        self._load_dev()

    def _pending(self):
        return review_rows(self.body)

    def _current_row(self):
        pending = self._pending()
        if not pending:
            return None
        if self.row_index >= len(pending):
            self.row_index = 0
        if self.row_index < 0:
            self.row_index = len(pending) - 1
        return pending[self.row_index]

    def _render(self):
        self.text.delete("1.0", tk.END)
        if not self.body:
            self.var_title.set("未载入")
            return
        pending = self._pending()
        current = self._current_row()
        if current is None:
            self.var_title.set("没有待核行")
        else:
            self.var_title.set(f"待核 {self.row_index + 1}/{len(pending)}  {current['label']}")
        lines = [
            f"画面供应: {self.body.get('provider_on_screen')}",
            f"桌名: {self.body.get('table_label_on_screen')}",
            f"不是 Evolution: {self.body.get('not_evolution')}",
            f"适用于真实桌: {self.body.get('applies_to_live_table')}",
            f"n_decks: {self.body.get('n_decks')}",
            f"公开候选副数(非本桌): {self.body.get('public_help_candidate_n_decks')}",
            f"peek 公开冲突: {self.body.get('public_help_peek_conflict')}",
            "",
        ]
        if current is None:
            lines.append("待核行已处理完。这仍不是该真实桌验收。")
        else:
            user = "待你核对" if current["needs_user"] else "已见图"
            lines.append(f"- {current['label']} [{current['status']}/{user}]")
            lines.append(f"  为何影响: {current['why']}")
            if current.get("value"):
                lines.append(f"  现值: {current['value']}")
            if current.get("evidence"):
                lines.append(f"  证据: {current['evidence']}")
            if current.get("user_decision"):
                lines.append(f"  你的判定: {current['user_decision']} / {current.get('user_decision_by')}")
        lines.append("")
        lines.append("全部字段：")
        for row in self.body["rows"]:
            mark = "待核" if row.get("needs_user") else (row.get("user_decision") or "已见图")
            lines.append(f"- {row['label']} [{row['status']}/{mark}]")
        lines.append("")
        sources = self.body.get("public_help_sources") or []
        if sources:
            lines.append("公开规则页（不适用于该真实桌）：")
            for source in sources:
                fetched = "已抓取" if source.get("fetched") else "未抓取全文"
                lines.append(f"- {source['id']} [{fetched}] {source.get('url')}")
                snippet = source.get("quote") or source.get("search_snippet") or ""
                if snippet:
                    lines.append(f"  {snippet}")
        skipped = self.body.get("public_help_skipped")
        if skipped:
            lines.append(f"公开规则页未合并: {skipped}")
        lines.append("")
        lines.append(self.body.get("user_action") or "")
        self.text.insert("1.0", "\n".join(lines))

    def _prev_row(self):
        pending = self._pending()
        if pending:
            self.row_index = (self.row_index - 1) % len(pending)
        self._render()

    def _next_row(self):
        pending = self._pending()
        if pending:
            self.row_index = (self.row_index + 1) % len(pending)
        self._render()

    def _decide(self, decision):
        current = self._current_row()
        if not current:
            messagebox.showerror("规则差异", "没有待核行", parent=self)
            return
        try:
            self.body = record_row_decision(
                self.body, current["id"], decision=decision,
                attested_by=self.var_name.get(), recorded_by="ui-rules-diff")
        except ValueError as error:
            messagebox.showerror("不能确认", str(error), parent=self)
            return
        pending = self._pending()
        if self.row_index >= len(pending):
            self.row_index = 0
        self._render()

    def _load_dev(self):
        still = _LOCAL_FELT if _LOCAL_FELT.is_file() else None
        self.body = from_felt_observation(printed_felt_observation(
            still_path=still, source_video=_DEV_CLIP))
        self.row_index = 0
        self._render()

    def _load(self):
        path = filedialog.askopenfilename(
            parent=self, title="绒面观察 JSON", filetypes=[("观察", "*.json")])
        if not path:
            return
        self.body = from_felt_observation(load_observation(path))
        self.row_index = 0
        self._render()

    def _save(self):
        if not self.body:
            messagebox.showerror("规则差异", "先载入绒面观察", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json", initialfile="table-rules-diff.json",
            filetypes=[("规则差异", "*.json")])
        if not path:
            return
        write_diff(path, self.body)
        _drop_m4_inbox("table_rules", path)
        self.app.set_status("已保存规则差异表；accepted=false，不适用于真实桌")


class EventDraftDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("开发片事件草稿（一轮一页，切牌卡不计牌）")
        self.geometry("980x640")
        self.draft = development_clip_stub(filename=_DEV_CLIP, role="development")
        if _LOCAL_DRAFT.is_file():
            try:
                self.draft = load_draft(_LOCAL_DRAFT)
            except ValueError:
                pass
        self.page_index = 0
        self.still_index = 0
        self._photo = None
        ttk.Label(
            self, wraplength=940,
            text="一轮一页。默认跳过局间遗留。看图核对；确认要写下姓名。软件不能代签。"
                 "多座位请先指定座位再改点数。不清楚就留未知。红牌切牌卡不计牌。"
                 "写入账本前请先开靴；未确认牌面只会记成观察缺口。"
                 "整轮观察范围需单独核对，点完已发现的牌不等于没有漏牌。",
        ).pack(anchor="w", padx=8, pady=6)
        nav = ttk.Frame(self)
        nav.pack(fill=tk.X, padx=8)
        ttk.Button(nav, text="上一页", command=self._prev).pack(side=tk.LEFT)
        self.var_title = tk.StringVar()
        ttk.Label(nav, textvariable=self.var_title).pack(side=tk.LEFT, padx=12)
        ttk.Button(nav, text="下一页", command=self._next).pack(side=tk.LEFT)
        self.var_skip_waiting = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            nav, text="跳过局间遗留页", variable=self.var_skip_waiting,
            command=self._filter_changed,
        ).pack(side=tk.LEFT, padx=12)
        ttk.Button(nav, text="本页上一帧", command=self._still_prev).pack(side=tk.LEFT, padx=8)
        ttk.Button(nav, text="本页下一帧", command=self._still_next).pack(side=tk.LEFT)
        self.image = tk.Label(self, text="无静帧", background="#10202a", foreground="#d4e9ef")
        self.image.pack(fill=tk.X, padx=8, pady=6)
        self.listbox = tk.Listbox(self, height=7, font=("Consolas", 9))
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=8)
        self.listbox.bind("<<ListboxSelect>>", lambda _event: self._on_event_selected())
        form = ttk.Frame(self)
        form.pack(fill=tk.X, padx=8, pady=4)
        ttk.Label(form, text="选中事件牌面").pack(side=tk.LEFT)
        self.var_rank = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_rank, width=8).pack(side=tk.LEFT, padx=4)
        ttk.Button(form, text="只改点数、不确认", command=self._set_rank).pack(side=tk.LEFT, padx=4)
        ttk.Button(form, text="指定座位、不确认", command=self._set_seat).pack(side=tk.LEFT, padx=4)
        ttk.Label(form, text="入账座位").pack(side=tk.LEFT, padx=(16, 4))
        self.var_seat = tk.StringVar(value="玩家1")
        ttk.Combobox(
            form, textvariable=self.var_seat, width=10, state="readonly",
            values=("庄家", "玩家1", "玩家2", "玩家3", "玩家4", "玩家5", "玩家6", "玩家7"),
        ).pack(side=tk.LEFT)
        ttk.Button(form, text="写入已开牌靴（未知为缺口）", command=self._import).pack(
            side=tk.LEFT, padx=8)
        ttk.Label(form, text="确认人姓名").pack(side=tk.LEFT, padx=(16, 4))
        self.var_name = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_name, width=16).pack(side=tk.LEFT)
        row = ttk.Frame(self)
        row.pack(pady=6)
        ttk.Button(row, text="本页保留未知", command=self._keep).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="加一张未知明牌", command=self._add_deal).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="加一张未知暗牌", command=self._add_hidden).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="确认本页", command=self._confirm).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="确认本轮观察范围", command=self._confirm_coverage).pack(side=tk.LEFT, padx=4)
        ttk.Button(row, text="保存草稿", command=self._save).pack(side=tk.LEFT, padx=4)
        self.var_status = tk.StringVar()
        ttk.Label(self, textvariable=self.var_status, wraplength=940).pack(anchor="w", padx=8)
        self._jump_to_review()
        self._refresh()

    def _pages(self):
        skip = bool(getattr(self, "var_skip_waiting", None) and self.var_skip_waiting.get())
        return review_pages(self.draft, skip_waiting_only=skip)

    def _jump_to_review(self):
        for index, page in enumerate(self._pages()):
            if any(event.get("kind") in ("deal", "hidden") for event in page["events"]):
                self.page_index = index
                self.still_index = 0
                return

    def _filter_changed(self):
        self.page_index = 0
        self.still_index = 0
        self._jump_to_review()
        self._refresh()

    def _page(self):
        pages = self._pages()
        if not pages:
            return None
        self.page_index = max(0, min(self.page_index, len(pages) - 1))
        return pages[self.page_index]

    def _stills(self):
        page = self._page()
        if page is None:
            return []
        return list(page.get("sequence_stills") or ([page.get("still_path")] if page.get("still_path") else []))

    def _prev(self):
        self.page_index = max(0, self.page_index - 1)
        self.still_index = 0
        self._refresh()

    def _next(self):
        self.page_index = min(len(self._pages()) - 1, self.page_index + 1)
        self.still_index = 0
        self._refresh()

    def _still_prev(self):
        self.still_index = max(0, self.still_index - 1)
        self._refresh()

    def _still_next(self):
        stills = self._stills()
        if stills:
            self.still_index = min(len(stills) - 1, self.still_index + 1)
        self._refresh()

    def _set_seat(self):
        event_id = self._selected_event_id()
        if not event_id:
            messagebox.showerror("指定座位", "先选中一条发牌或暗牌事件", parent=self)
            return
        try:
            set_event_seat(self.draft, event_id, self.var_seat.get())
        except (ValueError, KeyError) as error:
            messagebox.showerror("不能指定座位", str(error), parent=self)
            return
        self._refresh()

    def _still_file(self, path):
        if not path:
            return None
        candidate = Path(path)
        if candidate.is_file():
            return candidate
        rooted = Path(__file__).resolve().parents[2] / path
        return rooted if rooted.is_file() else None

    def _selected_event(self):
        event_id = self._selected_event_id()
        if not event_id:
            return None
        page = self._page()
        if page is None:
            return None
        return next((event for event in page["events"] if event["event_id"] == event_id), None)

    def _on_event_selected(self):
        event = self._selected_event()
        if event:
            seat = event.get("seat") or event.get("seat_hint")
            if seat:
                self.var_seat.set(seat)
        page = self._page()
        stills = self._stills()
        if stills:
            still = self._still_file(stills[self.still_index])
        else:
            still = self._still_file(page.get("still_path") if page else None)
        self._show_still(still, event)

    def _show_still(self, still, event):
        show = still
        cropped = False
        if still is not None and event is not None:
            seat = event.get("seat") or event.get("seat_hint")
            if seat == DEALER:
                dest = _CROP_DIR / f"{Path(still).stem}-dealer.png"
                try:
                    if not dest.is_file():
                        crop_style_region(still, "dealer", dest)
                    if dest.is_file():
                        show = dest
                        cropped = True
                except Exception:
                    show = still
        if show is not None:
            try:
                photo = tk.PhotoImage(file=str(show))
                if photo.width() > 940:
                    factor = max(2, int(photo.width() / 940) + 1)
                    photo = photo.subsample(factor, factor)
                self._photo = photo
                self.image.configure(image=photo, text="")
            except tk.TclError:
                self._photo = None
                self.image.configure(image="", text="无法显示静帧")
        else:
            self._photo = None
            self.image.configure(image="", text="本页无静帧")
        if cropped:
            title = self.var_title.get()
            if "庄家裁切" not in title:
                self.var_title.set(title + "  庄家裁切")

    def _refresh(self):
        page = self._page()
        pages = self._pages()
        if page is None:
            return
        stills = self._stills()
        if stills:
            self.still_index = max(0, min(self.still_index, len(stills) - 1))
        self.var_title.set(
            f"{self.page_index + 1}/{len(pages)}  {page['title']}"
            + (f"  帧 {self.still_index + 1}/{len(stills)}" if stills else "")
        )
        if stills:
            still = self._still_file(stills[self.still_index])
        else:
            still = self._still_file(page.get("still_path"))
        keep_id = self._selected_event_id()
        self.listbox.delete(0, tk.END)
        ordered = sorted(
            page["events"],
            key=lambda event: (
                0 if event.get("kind") in ("deal", "hidden") else 1,
                event.get("seat") or event.get("seat_hint") or "",
                int(event.get("slot") or 0),
            ),
        )
        restore = None
        for index, event in enumerate(ordered):
            rank = event.get("rank") or "—"
            self.listbox.insert(
                tk.END,
                f"{event['event_id']}  {event['kind']}  {event['status']}  "
                f"rank={rank}  counted={event['counted_in_remaining']}"
                + (f"  seat={event.get('seat')}" if event.get("seat") else "")
                + (f"  hint={event.get('seat_hint')}" if event.get("seat_hint") and not event.get("seat") else "")
                + (f"  slot={event.get('slot')}" if event.get("placeholder_source") else ""),
            )
            if keep_id and event["event_id"] == keep_id:
                restore = index
        if restore is not None:
            self.listbox.selection_set(restore)
        selected = self._selected_event()
        self._show_still(still, selected)
        if selected:
            seat = selected.get("seat") or selected.get("seat_hint")
            if seat in ("庄家", "玩家1", "玩家2", "玩家3", "玩家4", "玩家5", "玩家6", "玩家7"):
                self.var_seat.set(seat)
        status = composition_status(self.draft)
        hidden = max(0, len(review_pages(self.draft)) - len(pages))
        extra = f"；已跳过局间遗留 {hidden} 页" if self.var_skip_waiting.get() and hidden else ""
        coverage = (
            "；观察范围已核对"
            if page["page_id"] not in ("shoe-open", "shoe-end")
            and coverage_attestation_valid(self.draft, page["page_id"])
            else "；观察范围未核对"
        )
        self.var_status.set(
            f"确认牌 {status['confirmed_playing_cards']}；未知 {status['unknown_or_unconfirmed']}；"
            f"切牌标志 {status['cut_markers']}；完整剩余={status['complete_remaining']}；"
            f"accepted=false{extra}{coverage}")

    def _selected_event_id(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        line = self.listbox.get(selection[0])
        return line.split()[0]

    def _keep(self):
        page = self._page()
        if page is None:
            return
        keep_unknown(self.draft, [event["event_id"] for event in page["events"]])
        self._refresh()

    def _confirm(self):
        page = self._page()
        if page is None:
            return
        try:
            confirm_page(self.draft, page["page_id"], confirmed_by=self.var_name.get(),
                         recorded_by="ui-event-draft")
        except (ValueError, KeyError) as error:
            messagebox.showerror("不能确认", str(error), parent=self)
            return
        self._refresh()

    def _confirm_coverage(self):
        page = self._page()
        if page is None:
            return
        try:
            confirm_round_coverage(
                self.draft, page["page_id"],
                confirmed_by=self.var_name.get(),
                recorded_by="ui-event-draft",
            )
        except (ValueError, KeyError) as error:
            messagebox.showerror("不能确认观察范围", str(error), parent=self)
            return
        self._refresh()

    def _set_rank(self):
        event_id = self._selected_event_id()
        if not event_id:
            messagebox.showerror("改点数", "先选中一条发牌事件", parent=self)
            return
        try:
            set_event_rank(self.draft, event_id, self.var_rank.get())
        except (ValueError, KeyError) as error:
            messagebox.showerror("不能改点数", str(error), parent=self)
            return
        self._refresh()

    def _add_deal(self):
        self._add_card("deal")

    def _add_hidden(self):
        self._add_card("hidden")

    def _add_card(self, kind):
        page = self._page()
        if page is None or page["page_id"] in ("shoe-open", "shoe-end"):
            messagebox.showerror("加牌", "请在对局页追加未知牌位", parent=self)
            return
        stills = self._stills()
        still = stills[self.still_index] if stills else page.get("still_path")
        try:
            add_unknown_card(
                self.draft, page["page_id"],
                kind=kind,
                seat=self.var_seat.get() or None,
                still_path=still,
            )
        except ValueError as error:
            messagebox.showerror("不能加牌", str(error), parent=self)
            return
        self._refresh()

    def _import(self):
        try:
            result = apply_event_draft(self.app.ctrl, self.draft, seat=self.var_seat.get())
        except DraftImportError as error:
            title = "已提交，显示失败" if error.committed else "不能写入账本"
            messagebox.showerror(title, str(error), parent=self)
            if error.committed:
                try:
                    self.app.refresh_all()
                except Exception:
                    pass
            return
        except Exception as error:
            messagebox.showerror("不能写入账本", str(error), parent=self)
            return
        self.app.refresh_all()
        self.var_status.set(
            f"已写入 {result['events_written']} 条；确认发牌 {result['card_dealt']}；"
            f"缺口 {result['gaps']}；offline_mc_ready=false；accepted=false"
        )
        self.app.set_status("开发片草稿已写入已开牌靴；未知为缺口，未代签，离线MC未就绪")

    def _save(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile="dev-clip-12.58.11.02-event-draft.json",
            filetypes=[("事件草稿", "*.json")])
        if not path:
            return
        write_draft(path, self.draft)
        if _LOCAL_DRAFT.parent.is_dir():
            write_draft(_LOCAL_DRAFT, self.draft)
        self.app.set_status("已保存开发片事件草稿；accepted=false")


_M4_PACK = Path(__file__).resolve().parents[2] / ".local-evidence" / "m4-acceptance-pending.json"
_LOCAL_ROLES = Path(__file__).resolve().parents[2] / ".local-evidence" / "material-roles.json"


class MaterialRoleDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("十段录像用途（复用已有绑定，不重新哈希）")
        self.geometry("920x420")
        from ..analysis.material_roles import inventory_from_pack, set_role
        self._set_role = set_role
        pack = {}
        if _M4_PACK.is_file():
            pack = json.loads(_M4_PACK.read_text(encoding="utf-8"))
        if _LOCAL_ROLES.is_file():
            self.inventory = json.loads(_LOCAL_ROLES.read_text(encoding="utf-8"))
        else:
            self.inventory = inventory_from_pack(pack, recorded_by="ui-material-roles")
        ttk.Label(
            self, wraplength=880,
            text="请确认每段的实际使用史。未确认前不训练、不调阈值。"
                 "12.58.11.02 不能改成留出。软件不能代签。",
        ).pack(anchor="w", padx=8, pady=6)
        self.listbox = tk.Listbox(self, height=12, font=("Consolas", 9))
        self.listbox.pack(fill=tk.BOTH, expand=True, padx=8)
        form = ttk.Frame(self)
        form.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(form, text="角色").pack(side=tk.LEFT)
        self.var_role = tk.StringVar(value="candidate")
        ttk.Combobox(
            form, textvariable=self.var_role, width=14, state="readonly",
            values=("development", "candidate", "holdout"),
        ).pack(side=tk.LEFT, padx=4)
        ttk.Label(form, text="确认人姓名").pack(side=tk.LEFT, padx=(12, 4))
        self.var_name = tk.StringVar()
        ttk.Entry(form, textvariable=self.var_name, width=16).pack(side=tk.LEFT)
        ttk.Button(form, text="确认选中用途", command=self._confirm).pack(side=tk.LEFT, padx=8)
        ttk.Button(form, text="保存清单", command=self._save).pack(side=tk.LEFT)
        self.var_status = tk.StringVar()
        ttk.Label(self, textvariable=self.var_status, wraplength=880).pack(anchor="w", padx=8, pady=4)
        self._refresh()

    def _refresh(self):
        self.listbox.delete(0, tk.END)
        for row in self.inventory.get("rows") or []:
            who = row.get("role_confirmed_by") or "未确认"
            self.listbox.insert(
                tk.END,
                f"{row.get('filename')}  {row.get('role')}  {who}  sha={str(row.get('sha256') or '')[:12]}",
            )
        self.var_status.set(
            f"{len(self.inventory.get('rows') or [])} 段；accepted=false；不重新哈希"
        )

    def _selected_filename(self):
        selection = self.listbox.curselection()
        if not selection:
            return None
        return (self.inventory.get("rows") or [])[selection[0]].get("filename")

    def _confirm(self):
        filename = self._selected_filename()
        if not filename:
            messagebox.showerror("用途", "先选中一段录像", parent=self)
            return
        try:
            self._set_role(
                self.inventory, filename, self.var_role.get(),
                confirmed_by=self.var_name.get())
        except (ValueError, KeyError) as error:
            messagebox.showerror("不能确认", str(error), parent=self)
            return
        self._refresh()

    def _save(self):
        path = filedialog.asksaveasfilename(
            parent=self, defaultextension=".json",
            initialfile="material-roles.json",
            filetypes=[("用途清单", "*.json")])
        if not path:
            return
        Path(path).write_text(
            json.dumps(self.inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        if _LOCAL_ROLES.parent.is_dir():
            _LOCAL_ROLES.write_text(
                json.dumps(self.inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        self.app.set_status("已保存录像用途清单；accepted=false")
