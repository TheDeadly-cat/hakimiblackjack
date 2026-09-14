"""Compact always-on-top human review, with focused local rank entry."""
from __future__ import annotations

import json
from pathlib import Path
import queue
import time
import tkinter as tk
from tkinter import ttk

from .assisted_recording import AssistedRecording, DraftError
from .overlay_windows import CombinationHotkey, HOTKEYS, style_owned_window


SEATS = ("庄家",) + tuple(f"玩家{i}" for i in range(1, 8))
RANK_KEYS = {**{str(i): str(i) for i in range(2, 10)}, "0": "10", "a": "A", "j": "J", "q": "Q", "k": "K"}
OPS = {"新牌": "new", "揭示原未知/暗牌": "reveal", "纠正原记录": "correct",
       "修正座位/手牌": "move",
       "撤回重复牌": "withdraw", "同牌仅关联": "link", "拒绝假检测": "reject"}
FACES = {"可见": "shown", "未知": "unknown", "暗牌": "hidden"}


class QuickRecordPanel(tk.Toplevel):
    def __init__(self, app, *, register_hotkey=True):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.work = AssistedRecording(app.ctrl)
        self.work.seat = app.var_target.get()
        self.feed = None
        self.reconnect = None
        self.expanded = False
        self.closed = False
        self.pressed = set()
        self.displayed_id = None
        self.last_commit_click = 0
        self._photos = {}
        self._preview_key = None
        self._render_key = None
        self._pending = None
        self.hotkey = None
        self.native_status = {}
        self.usage = {"schema": "assisted-panel-usage-1", "started_at": time.time(),
                      "key_presses": 0, "mouse_clicks": 0, "max_pending": 0,
                      "max_lag_seconds": 0, "source_runs": [], "operator": "not_attested"}
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.geometry("360x126+30+60")
        self.configure(background="#142b3a")
        self.title("Hakimi 辅助记牌")
        self.var_summary = tk.StringVar(value="辅助记牌 · 草稿待确认")
        self.var_current = tk.StringVar()
        self.var_status = tk.StringVar(value="先选择座位，再补牌；牌级键 + Enter 确认")
        self.var_hotkey = tk.StringVar(value="全局组合键未开启")
        self.var_seat = tk.StringVar(value=self.work.seat)
        self.var_hand = tk.StringVar()
        self.var_rank = tk.StringVar(value="—")
        self.var_face = tk.StringVar(value="可见")
        self.var_op = tk.StringVar(value="新牌")
        self.var_target = tk.StringVar()
        self.var_reason = tk.StringVar()
        self.var_key = tk.StringVar(value="Ctrl+Alt+Space")

        self.header = tk.Frame(self, bg="#142b3a")
        self.header.pack(fill=tk.X)
        title = tk.Label(self.header, textvariable=self.var_summary, fg="#e6f4ff", bg="#142b3a", anchor="w")
        title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8, pady=5)
        for widget in (self.header, title):
            widget.bind("<ButtonPress-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
        self.toggle_button = tk.Button(self.header, text="展开", command=self.toggle, takefocus=False)
        self.toggle_button.pack(side=tk.RIGHT, padx=2)
        tk.Button(self.header, text="×", command=self.destroy, takefocus=False).pack(side=tk.RIGHT, padx=2)
        strip = tk.Frame(self, bg="#142b3a")
        strip.pack(fill=tk.X)
        self.thumb = tk.Label(strip, text="原图\n裁片", width=7, height=3, fg="#d4e9ef", bg="#243f50")
        self.thumb.pack(side=tk.LEFT, padx=6, pady=3)
        self.thumb.bind("<Button-1>", lambda e: self.show_source())
        tk.Label(strip, textvariable=self.var_current, anchor="w", justify=tk.LEFT,
                 fg="white", bg="#142b3a", wraplength=263).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.editor = ttk.Frame(self, padding=8)
        targetbar = ttk.Frame(self.editor)
        targetbar.pack(fill=tk.X)
        ttk.Label(targetbar, text="录入目标").pack(side=tk.LEFT)
        self.seat_combo = ttk.Combobox(targetbar, textvariable=self.var_seat, values=SEATS, state="readonly", width=7)
        self.seat_combo.pack(side=tk.LEFT, padx=4)
        self.seat_combo.bind("<<ComboboxSelected>>", self.change_target)
        self.hand_combo = ttk.Combobox(targetbar, textvariable=self.var_hand, state="readonly", width=24)
        self.hand_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.hand_combo.bind("<<ComboboxSelected>>", self.change_target)
        controls = ttk.Frame(self.editor)
        controls.pack(fill=tk.X, pady=5)
        for label, command in [("补牌 N", self.new_card), ("上一张", self.previous),
                               ("下一张 / 跳过", self.next), ("原图", self.show_source)]:
            ttk.Button(controls, text=label, command=command).pack(side=tk.LEFT, padx=2)
        form = ttk.Frame(self.editor)
        form.pack(fill=tk.X)
        self.op_combo = ttk.Combobox(form, textvariable=self.var_op, values=tuple(OPS), state="readonly", width=17)
        self.op_combo.pack(side=tk.LEFT)
        self.op_combo.bind("<<ComboboxSelected>>", lambda e: self.sync_form())
        face_combo = ttk.Combobox(form, textvariable=self.var_face, values=tuple(FACES), state="readonly", width=6)
        face_combo.pack(side=tk.LEFT, padx=4)
        face_combo.bind("<<ComboboxSelected>>", lambda e: self.sync_form())
        self.rank_label = ttk.Label(form, textvariable=self.var_rank, font=("Consolas", 22, "bold"), anchor="center")
        self.rank_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.confirm_button = ttk.Button(form, text="确认 Enter", command=self.commit)
        self.confirm_button.pack(side=tk.RIGHT)
        self.target_combo = ttk.Combobox(self.editor, textvariable=self.var_target, state="readonly")
        self.target_combo.pack(fill=tk.X, pady=4)
        self.target_combo.bind("<<ComboboxSelected>>", lambda e: self.sync_form())
        self.reason_entry = ttk.Entry(self.editor, textvariable=self.var_reason)
        self.reason_entry.pack(fill=tk.X)
        ttk.Label(self.editor, text="上框填写纠错依据；2–9 / 0=10 / A J Q K · Esc 收起", foreground="#516b7a").pack(anchor="w")
        ttk.Label(self.editor, textvariable=self.var_status, wraplength=485, foreground="#944018").pack(fill=tk.X, pady=3)
        sourcebar = ttk.Frame(self.editor)
        sourcebar.pack(fill=tk.X)
        self.assist_button = ttk.Button(sourcebar, text="自动提示：未连接", command=self.toggle_assist)
        self.assist_button.pack(side=tk.LEFT)
        ttk.Button(sourcebar, text="绑定当前轮", command=self.rebind_source).pack(side=tk.LEFT, padx=4)
        ttk.Button(sourcebar, text="转为录像复盘", command=self.enter_replay).pack(side=tk.LEFT)
        # The image keeps updating while the selected crop above remains frozen.
        self.table_canvas = tk.Canvas(self.editor, height=118, bg="#10202a", highlightthickness=0)
        self.table_canvas.pack(fill=tk.X)
        self.table_image = self.table_canvas.create_image(0, 0, anchor="nw")
        ttk.Label(self.editor, text="待核对队列 · 清空不代表本轮完整").pack(anchor="w", pady=(3, 0))
        self.queue_list = tk.Listbox(self.editor, height=4, exportselection=False)
        self.queue_list.pack(fill=tk.X)
        self.queue_list.bind("<<ListboxSelect>>", self.select_queue)
        ttk.Label(self.editor, text="本轮已记牌（双击纠错；选中后可查看原图）").pack(anchor="w", pady=(3, 0))
        self.record_list = ttk.Treeview(self.editor, columns=("seat", "hand", "rank"), show="headings", height=4)
        for key, label, width in [("seat", "座位", 65), ("hand", "手牌", 285), ("rank", "牌级", 65)]:
            self.record_list.heading(key, text=label)
            self.record_list.column(key, width=width)
        self.record_list.pack(fill=tk.X)
        self.record_list.bind("<Double-1>", lambda e: self.edit_record())
        bottom = ttk.Frame(self.editor)
        bottom.pack(fill=tk.X, pady=4)
        for label, command in [("观察缺口", self.mark_gap), ("撤回草稿", self.discard),
                               ("连接识别来源", self.open_source), ("回主工作台", self.show_main)]:
            ttk.Button(bottom, text=label, command=command).pack(side=tk.LEFT, padx=2)
        hotbar = ttk.Frame(self.editor)
        hotbar.pack(fill=tk.X)
        key_combo = ttk.Combobox(hotbar, textvariable=self.var_key, values=tuple(HOTKEYS), state="readonly", width=20)
        key_combo.pack(side=tk.LEFT)
        ttk.Button(hotbar, text="应用呼出键", command=self.configure_hotkey).pack(side=tk.LEFT, padx=4)
        ttk.Label(self.editor, textvariable=self.var_hotkey, wraplength=485).pack(fill=tk.X)

        # Before widget class bindings, so a focused button cannot also submit.
        self._key_tag = "QuickRecordInput" + str(id(self))
        self.bind_class(self._key_tag, "<KeyPress>", self.key_down)
        self.bind_class(self._key_tag, "<KeyRelease>", self.key_up)
        self.bind_class(self._key_tag, "<ButtonPress-1>", self.count_click)
        self._install_keys(self)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.app.ctrl.add_context_listener(self.context_changed)
        self.refresh()
        # Install the observation style before mapping, not after a first
        # frame that could activate the newly created tool window.
        self.native_status = style_owned_window(self, editing=False)
        self.deiconify()
        self.native_status = style_owned_window(self, editing=False)
        if register_hotkey:
            config = self.work.root / "panel-settings.json"
            if config.exists():
                try:
                    chosen = json.loads(config.read_text(encoding="utf-8"))["show_hotkey"]
                    if chosen in HOTKEYS:
                        self.var_key.set(chosen)
                except (ValueError, KeyError):
                    pass
            self.configure_hotkey()
        self._pending = self.after(80, self.poll)

    def _install_keys(self, widget):
        widget.bindtags((self._key_tag,) + widget.bindtags())
        for child in widget.winfo_children():
            self._install_keys(child)

    def _drag_start(self, event):
        self._drag = (event.x_root - self.winfo_x(), event.y_root - self.winfo_y())

    def _drag_move(self, event):
        x, y = max(0, event.x_root - self._drag[0]), max(0, event.y_root - self._drag[1])
        self.geometry(f"+{x}+{y}")

    def toggle(self):
        if self.expanded:
            self.expanded = False
            self.toggle_button.configure(text="展开")
            self.editor.pack_forget()
            self.geometry("360x126")
            self.native_status = style_owned_window(self, editing=False)
        else:
            self.expanded = True
            self.toggle_button.configure(text="收起")
            self.editor.pack(fill=tk.BOTH, expand=True)
            self.geometry("520x790")
            self.native_status = style_owned_window(self, editing=True)
            self.focus_set()

    def configure_hotkey(self):
        if self.hotkey:
            self.hotkey.close()
        self.hotkey = CombinationHotkey(self.var_key.get())
        (self.work.root / "panel-settings.json").write_text(
            json.dumps({"show_hotkey": self.var_key.get()}, ensure_ascii=False), encoding="utf-8")

    def key_down(self, event):
        if not self.expanded or event.widget.winfo_toplevel() != self or self.focus_displayof() is None:
            return
        if event.widget.winfo_class() in ("Entry", "TEntry", "Text", "TCombobox"):
            return
        key = event.keysym.lower()
        if key not in {*RANK_KEYS, "return", "kp_enter", "escape", "n"}:
            return
        if event.state & (0x0004 | 0x20000):  # Control/Alt are not ordinary rank input.
            return "break"
        if key in self.pressed:
            return "break"
        self.pressed.add(key)
        self.usage["key_presses"] += 1
        if key in RANK_KEYS:
            self.set_rank(RANK_KEYS[key])
        elif key in ("return", "kp_enter"):
            self.commit()
        elif key == "n":
            self.new_card()
        else:
            self.toggle()
        return "break"

    def key_up(self, event):
        self.pressed.discard(event.keysym.lower())

    def count_click(self, event):
        self.usage["mouse_clicks"] += 1

    def attempt(self, action):
        try:
            result = action()
            self.refresh()
            return result
        except Exception as exc:
            self.var_status.set(str(exc))
            self.refresh()
            return None

    def new_card(self):
        def run():
            self.work.manual()
            self.var_status.set("手动补牌草稿：请核对轮次、目标和发牌位置，再按 Enter")
        self.attempt(run)
        if self.expanded:
            self.focus_set()

    def set_rank(self, rank):
        def run():
            if self.work.selected is None or self.work.selected.status != "pending":
                self.work.manual()
            self.work.edit(rank=rank, face="shown")
            self.var_rank.set(rank)
            self.var_face.set("可见")
        self.attempt(run)

    def change_target(self, event=None):
        def run():
            seat = self.var_seat.get()
            d = self.work.selected
            old = d.original.get("record_before") if d else None
            previous_seat = d.seat if d else self.work.seat
            hand_id = self._hand_labels.get(self.var_hand.get()) if previous_seat == seat else None
            if not old:
                self.work.seat = seat
                self.work.hand_id = hand_id or None
            hand = self.work.target_hand(seat, hand_id, old["round_id"] if old else None)
            if self.work.selected and self.work.selected.status == "pending":
                self.work.edit(seat=seat, hand_id=hand)
            self.var_status.set(f"目标已切换至 {seat}，请核对所选手牌")
        self.attempt(run)
        self.focus_set()

    def sync_form(self):
        if self.work.selected is None or self.work.selected.status != "pending":
            return
        self.work.edit(operation=OPS[self.var_op.get()], face=FACES[self.var_face.get()],
                       target_id=self._target_ids.get(self.var_target.get()), reason=self.var_reason.get())
        if self.var_face.get() != "可见":
            self.work.edit(rank=None)
            self.var_rank.set("?")
        if self.expanded:
            self.focus_set()

    def commit(self):
        now = time.monotonic()
        if now - self.last_commit_click < .35:
            return
        self.last_commit_click = now
        def run():
            self.sync_form()
            if self.displayed_id is None:
                raise DraftError("请先补牌或选择当前候选")
            draft = self.work.items[self.displayed_id]
            event = self.work.confirm(self.displayed_id)
            self.var_status.set("已保存人工关联，未新增牌" if draft.operation == "link" else
                                "已拒绝，未入账" if draft.operation == "reject" else
                                f"已提交 #{event.seq}；分析按最新账本更新")
            self.work.next()
            self.app.refresh_all()
        self.attempt(run)
        self.focus_set()

    def next(self):
        self.attempt(self.work.next)
        self.focus_set()

    def previous(self):
        def run():
            records = self.work.records()
            if not records:
                raise DraftError("本轮还没有已记录牌")
            self.work.history(records[-1]["event_id"])
        self.attempt(run)
        self.focus_set()

    def edit_record(self):
        chosen = self.record_list.selection()
        if chosen:
            self.attempt(lambda: self.work.history(chosen[0]))
            self.focus_set()

    def select_queue(self, event=None):
        selected = self.queue_list.curselection()
        if selected and selected[0] < len(self._queue_ids):
            self.record_list.selection_remove(self.record_list.selection())
            self.attempt(lambda: self.work.select(self._queue_ids[selected[0]]))
            self.focus_set()

    def discard(self):
        def run():
            self.work.discard()
            self.work.next()
        self.attempt(run)

    def mark_gap(self):
        def run():
            reason = self.var_reason.get().strip()
            if not reason:
                raise DraftError("请在依据框写下漏看范围/原因，再点击观察缺口")
            self.app.ctrl.mark_gap(reason)
            self.app.refresh_all()
            self.var_status.set("已登记观察缺口；账本完整性和当前分析已更新")
        self.attempt(run)

    def open_source(self):
        self.app.act_open_vision()
        self.var_status.set("在识牌核对选择样式、模型和录像/窗口，再点预览中的“持续核对”")

    def show_main(self):
        self.app.deiconify()
        self.app.lift()

    def show_source(self):
        path = None
        chosen = self.record_list.selection()
        if chosen:
            row = next((r for r in self.work.records() if r["event_id"] == chosen[0]), None)
            if row:
                path = row["source_image"]
                if path is None and row["evidence"]:
                    try:
                        saved = json.loads(Path(row["evidence"]).read_text(encoding="utf-8"))
                        path = saved.get("draft", {}).get("source_image")
                    except (ValueError, OSError):
                        pass
        elif self.work.selected:
            path = self.work.selected.source_image
        if not path:
            self.var_status.set("这条手动记录未附原图；可以对照持续画面检查")
            return
        def run():
            win = tk.Toplevel(self)
            win.title("原始画面 · 仅回看")
            photo = tk.PhotoImage(file=str(path))
            factor = max(1, (photo.width() + 1199) // 1200)
            photo = photo.subsample(factor)
            label = ttk.Label(win, image=photo)
            label.image = photo
            label.pack()
            style_owned_window(win, editing=True)
        self.attempt(run)

    def attach(self, session, context_guard=lambda: True):
        from .continuous_review import ContinuousReviewFeed
        self.feed = ContinuousReviewFeed(self.work, session, context_guard)
        self.usage["source_runs"].append(session.run_id)
        self.var_status.set("持续采集中；每张候选仍须核对座位、是否新牌及原图")

    def toggle_assist(self):
        if self.feed:
            self.feed.enabled = not self.feed.enabled
            self.work._record("toggle-assistance", enabled=self.feed.enabled)
            self.var_status.set("自动提示已开启" if self.feed.enabled else "已暂停新候选；画面继续，可直接手动补牌")

    def rebind_source(self):
        if self.reconnect:
            self.attempt(self.reconnect)
        else:
            self.var_status.set("请从识牌预览的“持续核对”按钮连接来源")

    def enter_replay(self):
        if self.feed:
            self.attempt(self.feed.enter_replay)

    def context_changed(self):
        if not self.closed:
            self._render_key = None

    def refresh(self):
        d = self.work.selected
        count = len(self.work.pending)
        self.var_summary.set(f"待核对 {count} 张 · 草稿确认后入账")
        problem = self.work.source_problem() if self.feed else "手动录牌"
        lag = f"记录落后 {self.work.lag_seconds():.1f} 秒" if self.feed and count else "队列清空不代表记录完整"
        if self.feed and self.feed.replay_mode:
            problem = "录像复盘 · 已停止采集，不代表当前牌桌"
        self.assist_button.configure(text="自动提示：" + ("开" if self.feed.enabled else "关") if self.feed else "自动提示：未连接")
        if self.work.overflow:
            lag = "队列曾溢出：请检查漏牌并登记观察缺口"
        current = f"{d.seat} · {d.rank or '未知/待填'}" if d else f"{self.work.seat} · 按 N 补牌"
        if d and d.original.get("capture", {}).get("source_frame_index") is not None:
            current += f" · 录像 {d.original['capture']['media_time_ns'] / 1e9:.1f}s"
        self.var_current.set(f"{current}\n{lag}\n{d.blocked if d and d.blocked else problem or '来源持续更新'}")
        if self.displayed_id != (d.draft_id if d else None):
            self.displayed_id = d.draft_id if d else None
            self.var_rank.set(d.rank or "?" if d else "—")
            self.var_op.set(next(k for k, v in OPS.items() if v == d.operation) if d else "新牌")
            self.var_face.set(next(k for k, v in FACES.items() if v == d.face) if d else "可见")
            self.var_reason.set(d.reason if d else "")
            self.var_seat.set(d.seat if d else self.work.seat)
            self.thumb.configure(image="", text="原图\n裁片", width=7, height=3)
            if d and d.crop_image:
                try:
                    photo = tk.PhotoImage(file=d.crop_image)
                    scale = max(1, (max(photo.width(), photo.height()) + 65) // 66)
                    photo = photo.subsample(scale)
                    self._photos["crop"] = photo
                    self.thumb.configure(image=photo, text="", width=68, height=66)
                except tk.TclError:
                    self.var_status.set("原图裁片读取失败；请检查证据后再确认")
        old_round = d.original.get("record_before", {}).get("round_id") if d else None
        hands = self.work.hands(self.var_seat.get(), old_round)
        self._hand_labels = {f"第 {i+1} 手 · {h.display()}": h.hand_id for i, h in enumerate(hands)}
        chosen_hand = d.hand_id if d else self.work.hand_id
        if not hands:
            initial = d.hand_id if d else None
            self._hand_labels = {"首手（待发牌）": initial}
        self.hand_combo.configure(values=tuple(self._hand_labels))
        self.var_hand.set(next((label for label, hid in self._hand_labels.items() if hid == chosen_hand),
                              "请选择分牌后的目标手" if len(hands) > 1 else next(iter(self._hand_labels), "")))
        records = self.work.records()
        hand_names = {h.hand_id: f"第 {i+1} 手" for seat in SEATS for i, h in enumerate(self.work.hands(seat))}
        self._target_ids = {f"#{r['seq']} {r['seat']} / {r['rank']} / {hand_names.get(r['hand_id'], '手牌')}": r["event_id"] for r in records}
        if d and d.original.get("record_before"):
            old = d.original["record_before"]
            self._target_ids = {f"历史第 {old['round_no']} 轮 · #{old['seq']} {old['seat']} / {old['rank']}": old["event_id"]}
        self.target_combo.configure(values=tuple(self._target_ids))
        if d and d.target_id:
            self.var_target.set(next((k for k, v in self._target_ids.items() if v == d.target_id), ""))
        elif not d or d.operation == "new":
            self.var_target.set("")
        ids = [x.draft_id for x in self.work.pending]
        queue_rows = [f"{'[已撤回] ' if x.blocked else ''}{x.seat} · {x.rank or '?'} · {x.draft_id[:6]}" for x in self.work.pending]
        if list(self.queue_list.get(0, tk.END)) != queue_rows:
            self.queue_list.delete(0, tk.END)
            for row in queue_rows:
                self.queue_list.insert(tk.END, row)
        self._queue_ids = ids
        self.queue_list.selection_clear(0, tk.END)
        if self.displayed_id in ids:
            self.queue_list.selection_set(ids.index(self.displayed_id))
        retained = set()
        for row in records:
            eid = row["event_id"]
            retained.add(eid)
            values = (row["seat"], hand_names.get(row["hand_id"], "手牌"), row["rank"])
            if self.record_list.exists(eid):
                self.record_list.item(eid, values=values)
            else:
                self.record_list.insert("", tk.END, iid=eid, values=values)
        for eid in self.record_list.get_children():
            if eid not in retained:
                self.record_list.delete(eid)
        allowed = d is not None and d.status == "pending" and not d.blocked
        if allowed and d.source_epoch is not None and problem:
            allowed = False
        self.confirm_button.configure(state=tk.NORMAL if allowed else tk.DISABLED)

    def poll(self):
        if self.closed:
            return
        try:
            if self.hotkey:
                while True:
                    try:
                        action, message = self.hotkey.events.get_nowait()
                    except queue.Empty:
                        break
                    if action == "toggle":
                        self.toggle()
                    else:
                        self.var_hotkey.set(message)
            if self.feed:
                try:
                    self.feed.poll()
                except Exception as exc:
                    self.feed.error = "候选队列暂停：" + str(exc)
                    self.var_status.set(self.feed.error)
                packet = self.feed.owner.source.preview()
                if self.expanded and packet is not None and packet.frame_id != self._preview_key:
                    from PIL import Image, ImageTk
                    image = Image.fromarray(packet.pixels[:, :, ::-1])
                    image.thumbnail((490, 118))
                    photo = ImageTk.PhotoImage(image)
                    self._photos["table"] = photo
                    self.table_canvas.itemconfigure(self.table_image, image=photo)
                    self._preview_key = packet.frame_id
            self.refresh()
            self.usage["max_pending"] = max(self.usage["max_pending"], len(self.work.pending))
            self.usage["max_lag_seconds"] = max(self.usage["max_lag_seconds"], self.work.lag_seconds())
        except Exception as exc:
            self.var_status.set("工作台刷新失败，暂停候选确认：" + str(exc))
            if self.feed:
                self.feed.error = self.var_status.get()
        finally:
            if not self.closed:
                self._pending = self.after(100, self.poll)

    def destroy(self):
        if self.closed:
            return
        self.closed = True
        if self._pending:
            self.after_cancel(self._pending)
        if self.hotkey:
            self.hotkey.close()
        self.app.ctrl.remove_context_listener(self.context_changed)
        self.work.close()
        self.unbind_class(self._key_tag, "<KeyPress>")
        self.unbind_class(self._key_tag, "<KeyRelease>")
        self.unbind_class(self._key_tag, "<ButtonPress-1>")
        self.usage.update(ended_at=time.time(), pending_at_close=len(self.work.pending),
                          queue_overflow_observations=self.work.overflow,
                          scope="Local panel interaction counts; not a human usability score or full video accuracy")
        self.work._record("usage-summary", **self.usage)
        self.app._quick_panel = None
        super().destroy()
