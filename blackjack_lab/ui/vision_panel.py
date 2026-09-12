# -*- coding: utf-8 -*-
"""中文工作台的识牌核对窗口。延迟加载 OpenCV；未安装时不影响手动录牌。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..core.cards import RANKS, TEN_BUCKET
from ..core.table import DEALER, player_seat_name
from ..vision.contracts import REVIEW_PENDING
from ..vision.video_contracts import (
    SHOE_START_LABELS, SHOE_START_UNCERTAIN, shoe_start_intent,
)
from .vision_bridge import (
    FACE_HIDDEN_LEDGER, FACE_SHOWN_LEDGER, FACE_UNKNOWN_LEDGER,
    OP_CORRECT, OP_NEW, OP_REJECT, OP_REVEAL, ConfirmDecision,
    VisionBridgeError, VisionReviewSession, capture_bind_context,
)

SEATS = [DEALER] + [player_seat_name(i) for i in range(1, 8)]
OPS_ZH = {
    "新的可见牌": (OP_NEW, FACE_SHOWN_LEDGER),
    "新的未知牌（看不清但确认已发出）": (OP_NEW, FACE_UNKNOWN_LEDGER),
    "新的暗牌（庄家底牌）": (OP_NEW, FACE_HIDDEN_LEDGER),
    "揭示已有暗牌/未知牌": (OP_REVEAL, FACE_SHOWN_LEDGER),
    "纠正已有发牌事件": (OP_CORRECT, FACE_SHOWN_LEDGER),
    "拒绝（假检测，不入账）": (OP_REJECT, FACE_SHOWN_LEDGER),
}


def _ppm(label, rgb, width, height, max_side=120):
    scale = max(1, max(width, height) // max_side)
    sw, sh = max(1, width // scale), max(1, height // scale)
    out = bytearray(sw * sh * 3)
    for y in range(sh):
        src_y = min(height - 1, y * scale)
        for x in range(sw):
            src = (src_y * width + min(width - 1, x * scale)) * 3
            dst = (y * sw + x) * 3
            out[dst:dst + 3] = rgb[src:src + 3]
    header = f"P6 {sw} {sh} 255\n".encode("ascii")
    image = tk.PhotoImage(data=header + bytes(out))
    label.configure(image=image)
    label.image = image


class VisionReviewWindow(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("识牌核对（图像待核对，确认前不入账）")
        self.geometry("980x640")
        self.session: VisionReviewSession | None = None
        self.loaded = None
        self.photos = []
        self.video_reader = None
        self.tracker = None
        from ..vision.model_adapter import RecognitionRuntime
        self.runtime = RecognitionRuntime()
        self.selected_style = None
        self._source_key = ""
        banner = ttk.Label(
            self,
            text="旁观录像或截图：同一张牌多帧只确认一次。新局须先在主窗开轮，再绑定当前轮。确认前不入账。匹配度不是正确概率。",
            wraplength=940, foreground="#7a1f1f")
        banner.pack(fill=tk.X, padx=8, pady=6)
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8)
        ttk.Button(bar, text="打开本地 PNG/JPEG", command=self.open_image).pack(side=tk.LEFT)
        ttk.Button(bar, text="打开本地录像", command=self.open_video).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="绑定到当前轮（确认新轮）", command=self.rebind).pack(side=tk.LEFT, padx=6)
        model_bar = ttk.Frame(self)
        model_bar.pack(fill=tk.X, padx=8, pady=2)
        ttk.Button(model_bar, text="选择样式/区域 JSON", command=self.choose_style).pack(side=tk.LEFT)
        ttk.Button(model_bar, text="选择本地训练模型", command=self.choose_model).pack(side=tk.LEFT, padx=4)
        ttk.Button(model_bar, text="使用原模板", command=self.use_templates).pack(side=tk.LEFT)
        self.var_model = tk.StringVar(value="原模板候选；训练模型需先明确选择样式和本地模型目录。")
        ttk.Label(self, textvariable=self.var_model, wraplength=940).pack(fill=tk.X, padx=8)
        video_bar = ttk.Frame(self)
        video_bar.pack(fill=tk.X, padx=8, pady=2)
        ttk.Label(video_bar, text="开靴").pack(side=tk.LEFT)
        self.var_shoe_start = tk.StringVar(value=SHOE_START_LABELS[SHOE_START_UNCERTAIN])
        ttk.Combobox(
            video_bar, textvariable=self.var_shoe_start, width=28, state="readonly",
            values=list(SHOE_START_LABELS.values())).pack(side=tk.LEFT, padx=4)
        self.var_frame = tk.IntVar(value=0)
        self.scale = ttk.Scale(
            video_bar, from_=0, to=0, variable=self.var_frame, orient=tk.HORIZONTAL,
            length=280, command=lambda _v: None)
        self.scale.pack(side=tk.LEFT, padx=8)
        ttk.Button(video_bar, text="识别本帧", command=self.recognize_current_frame).pack(side=tk.LEFT)
        self.var_video = tk.StringVar(value="未打开录像。录屏请用 NVIDIA / Windows 现成工具。")
        ttk.Label(self, textvariable=self.var_video, wraplength=940).pack(fill=tk.X, padx=8)
        self.var_info = tk.StringVar(value=REVIEW_PENDING)
        ttk.Label(bar, textvariable=self.var_info, wraplength=640).pack(side=tk.LEFT, padx=8)
        self.list_frame = ttk.Frame(self)
        self.list_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self._geometry_dialog = None
        self.geometry_button = ttk.Button(self, text="几何待核（0）：未分类",
                                          command=self.show_geometry_review, state=tk.DISABLED)
        self.geometry_button.pack(anchor="w", padx=8, pady=4)
        form = ttk.LabelFrame(self, text="对选中候选的人工决定")
        form.pack(fill=tk.X, padx=8, pady=4)
        self.var_obs = tk.StringVar()
        self.var_op = tk.StringVar(value="新的可见牌")
        self.var_seat = tk.StringVar(value="玩家1")
        self.var_rank = tk.StringVar(value="A")
        self.var_target = tk.StringVar()
        ttk.Label(form, text="观察").grid(row=0, column=0, sticky="e")
        self.cmb_obs = ttk.Combobox(form, textvariable=self.var_obs, width=36, state="readonly")
        self.cmb_obs.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(form, text="操作").grid(row=0, column=2, sticky="e")
        ttk.Combobox(form, textvariable=self.var_op, width=32, state="readonly",
                     values=list(OPS_ZH)).grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(form, text="座位").grid(row=1, column=0, sticky="e")
        ttk.Combobox(form, textvariable=self.var_seat, width=12, state="readonly",
                     values=SEATS).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="牌面").grid(row=1, column=2, sticky="e")
        ttk.Combobox(form, textvariable=self.var_rank, width=8, state="readonly",
                     values=list(RANKS) + [TEN_BUCKET, "未知"]).grid(row=1, column=3, sticky="w", padx=4)
        ttk.Label(form, text="原事件ID（揭示/纠错）").grid(row=2, column=0, sticky="e")
        ttk.Entry(form, textvariable=self.var_target, width=38).grid(row=2, column=1, columnspan=2, sticky="w", padx=4)
        ttk.Button(form, text="确认写入账本", command=self.commit_selected).grid(row=2, column=3, sticky="w", padx=4)
        ttk.Button(form, text="拒绝该候选", command=self.reject_selected).grid(row=3, column=3, sticky="w", padx=4, pady=4)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):
        self._withdraw("识牌窗口已关闭")
        if self.video_reader is not None:
            self.video_reader.close()
            self.video_reader = None
        self.app._vision_win = None
        self.destroy()

    def _withdraw(self, reason):
        self.runtime.invalidate(reason)
        if self.session is not None:
            self.session.invalidate(reason)
        self.session = None
        self.app.vision_session = None
        self.loaded = None
        self.var_obs.set("")
        self.cmb_obs.configure(values=[])
        self._render_observations()
        self.app.refresh_vision_banner()
        self.var_info.set(reason + "；旧候选已撤回，请重新识别。")

    def choose_style(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择明确的牌桌样式与区域", filetypes=[("JSON", "*.json")])
        if path:
            try:
                self.select_style_path(path)
            except Exception as exc:
                messagebox.showerror("样式未切换", str(exc), parent=self)

    def select_style_path(self, path):
        from ..vision.model_adapter import load_style
        style = load_style(path)
        self._withdraw("样式或区域已改变")
        self.selected_style = style
        self.runtime.select_model(None)
        self.var_model.set(f"样式 {style.style_id}；请明确选择匹配的训练模型（当前为原模板候选）。")

    def choose_model(self):
        directory = filedialog.askdirectory(parent=self, title="选择含 manifest.json 与 model.npz 的本地目录")
        if directory:
            try:
                self.select_model_path(directory)
            except Exception as exc:
                messagebox.showerror("模型未切换", str(exc), parent=self)

    def select_model_path(self, directory):
        from ..vision.model_adapter import TrainedModelAdapter
        if self.selected_style is None:
            raise VisionBridgeError("请先选择明确的样式/区域 JSON，再选择与它匹配的模型。")
        adapter = TrainedModelAdapter(directory, style_id=self.selected_style.style_id)
        self._withdraw("识别模型已改变")
        self.runtime.select_model(adapter)
        self.var_model.set(adapter.identity_text)

    def use_templates(self):
        self._withdraw("切换至原模板")
        self.runtime.select_model(None)
        self.var_model.set("原模板候选；当前未使用训练分类器，仍须人工确认。")

    def _layout_canvas(self, loaded):
        from ..vision.model_adapter import prepare_style_image
        return prepare_style_image(loaded, self.selected_style)

    def _shoe_note(self) -> str:
        label = self.var_shoe_start.get()
        kind = next((k for k, v in SHOE_START_LABELS.items() if v == label), SHOE_START_UNCERTAIN)
        return shoe_start_intent(kind).note

    def _tracker_round_key(self, current):
        import json
        return json.dumps({**current, "video_source": self._source_key},
                          ensure_ascii=False, sort_keys=True)

    def rebind(self):
        try:
            if self.tracker is not None and self.video_reader is not None:
                current = capture_bind_context(self.app.ctrl)
                if not current["shoe_id"] or not current["round_id"]:
                    raise VisionBridgeError("请先在主窗口开靴、开轮，再确认录像的新轮边界。")
                key = self._tracker_round_key(current)
                if key != self.tracker.round_key:
                    self._withdraw("已人工确认新轮边界")
                    self.tracker.confirm_round_boundary(key)
                    self.recognize_current_frame()
                    return
            if not self.session:
                return
            self.session.rebind_current_round()
            self.var_info.set("已绑定当前牌靴/轮次。旧分析在确认入账前不得当作已考虑本图。")
            self.app.refresh_vision_banner()
        except VisionBridgeError as exc:
            messagebox.showerror("不能改绑", str(exc), parent=self)

    def open_image(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择本地牌桌截图",
            filetypes=[("图像", "*.png;*.jpg;*.jpeg"), ("全部", "*.*")])
        if not path:
            return
        self._withdraw("图像来源已改变")
        if self.video_reader is not None:
            self.video_reader.close()
            self.video_reader = None
        try:
            from ..vision.evidence_store import EvidenceStore
            from ..vision.image_io import load_image
            loaded = load_image(path)
            self._source_key = str(Path(path).resolve()) + ":" + loaded.sha256
            layout, canvas = self._layout_canvas(loaded)
            result = self.runtime.recognize_loaded(canvas, layout=layout, source_key=self._source_key)
            if result is None:
                return
            db = Path(self.app.ctrl.store.db_path)
            evidence = Path(str(db) + ".vision") / result.asset_sha256[:16]
            EvidenceStore(evidence).save_result(canvas, result)
            self.loaded = canvas
            self.session = VisionReviewSession(self.app.ctrl, result, evidence)
            self.app.vision_session = self.session
        except Exception as exc:
            messagebox.showerror("识牌失败", str(exc), parent=self)
            return
        self.video_reader = None
        self.tracker = None
        self._render_observations()
        self.app.refresh_vision_banner()
        self.var_info.set(REVIEW_PENDING + "  未确认前账本不变。")
        self.var_video.set("当前是单张截图，不是录像。")

    def open_video(self):
        path = filedialog.askopenfilename(
            parent=self, title="选择本地旁观录像（原文件只读）",
            filetypes=[("录像", "*.mp4;*.mkv;*.avi;*.mov;*.webm"), ("全部", "*.*")])
        if not path:
            return
        self.open_video_path(path)

    def open_video_path(self, path, frame=0):
        self._withdraw("录像来源已改变")
        try:
            from ..vision.tracker import FrameTracker
            from ..vision.video_io import VideoReader
            if self.video_reader is not None:
                self.video_reader.close()
            self.video_reader = VideoReader(path)
            self._source_key = "video:" + self.video_reader.asset.sha256
            self.tracker = FrameTracker()
            current = capture_bind_context(self.app.ctrl)
            if current["shoe_id"] and current["round_id"]:
                self.tracker.confirm_round_boundary(self._tracker_round_key(current))
            self.session = None
            self.app.vision_session = None
            last = max(0, self.video_reader.asset.frame_count - 1)
            self.scale.configure(to=last)
            self.var_frame.set(max(0, min(int(frame), last)))
            self.recognize_current_frame()
        except Exception as exc:
            messagebox.showerror("无法打开录像", str(exc), parent=self)

    def recognize_current_frame(self):
        if self.video_reader is None or self.tracker is None:
            return
        try:
            from ..vision.evidence_store import EvidenceStore
            frame_index = int(float(self.var_frame.get()))
            loaded = self.video_reader.seek(frame_index)
            layout, canvas = self._layout_canvas(loaded)
            result = self.runtime.recognize_loaded(canvas, layout=layout, source_key=self._source_key)
            if result is None:
                return
            result = self.tracker.apply_to_result(
                result, frame_index, self.video_reader.asset.time_ms(frame_index))
            db = Path(self.app.ctrl.store.db_path)
            evidence = Path(str(db) + ".vision") / self.video_reader.asset.sha256[:16]
            EvidenceStore(evidence).save_result(canvas, result)
            self.loaded = canvas
            if self.session is None:
                self.session = VisionReviewSession(self.app.ctrl, result, evidence)
                self.app.vision_session = self.session
            else:
                self.session.present_frame(result)
            for track in self.tracker.tracks:
                if track.committed:
                    continue
                if track.observation_id in self.session.commits:
                    self.tracker.mark_committed(track.observation_id)
        except Exception as exc:
            self._withdraw("当前帧识别失败")
            messagebox.showerror("识牌失败", str(exc), parent=self)
            return
        asset = self.video_reader.asset
        self.var_video.set(
            f"只读 {asset.path.name}  帧 {int(float(self.var_frame.get()))}/{max(0, asset.frame_count - 1)}  "
            f"{self._shoe_note()}")
        self._render_observations()
        self.app.refresh_vision_banner()
        self.var_info.set(REVIEW_PENDING + "  未确认前账本不变。")

    def _render_observations(self):
        if self._geometry_dialog is not None:
            self._geometry_dialog.destroy()
            self._geometry_dialog = None
        pending = self.session.result.geometry_review if self.session else []
        self.geometry_button.configure(text=f"几何待核（{len(pending)}）：未分类、未计入识别",
                                       state=tk.NORMAL if pending else tk.DISABLED)
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.photos.clear()
        if not self.session or not self.loaded:
            self.cmb_obs.configure(values=[])
            self.var_obs.set("")
            return
        from ..vision.image_io import crop_rgb
        ids = []
        for obs in self.session.result.observations:
            box = ttk.Frame(self.list_frame, padding=4)
            box.pack(side=tk.LEFT, anchor="n")
            lbl = tk.Label(box)
            lbl.pack()
            crop = crop_rgb(self.loaded, obs.bbox["x"], obs.bbox["y"], obs.bbox["w"], obs.bbox["h"])
            _ppm(lbl, crop, obs.bbox["w"], obs.bbox["h"])
            rank = obs.accepted_rank() or obs.face_state_candidate
            score = f"{obs.rank_candidates[0].match_score:.3f}" if obs.rank_candidates else "-"
            ttk.Label(
                box,
                text=f"{obs.observation_id[:8]}\n{rank} 匹配度 {score}\n{obs.reject_reason or '待确认'}\n{obs.seat_hint or ''}",
                justify=tk.CENTER,
            ).pack()
            ids.append(obs.observation_id)
        self.cmb_obs.configure(values=ids)
        self.var_obs.set(ids[0] if ids else "")
        if self.session.result.observations:
            first = self.session.result.observations[0]
            if first.accepted_rank():
                self.var_rank.set(first.accepted_rank())
            if first.seat_hint:
                self.var_seat.set(first.seat_hint)

    def show_geometry_review(self):
        """Separate evidence viewer; none of these IDs enter ledger confirmation."""
        if not self.session or not self.loaded or not self.session.result.geometry_review:
            return
        if self._geometry_dialog is not None and self._geometry_dialog.winfo_exists():
            self._geometry_dialog.lift()
            return
        from ..vision.image_io import crop_rgb
        window = self._geometry_dialog = tk.Toplevel(self)
        window.title("几何待核：请对照原图判断上角，下角不补数")
        window.geometry("850x420")
        ttk.Label(window, text="这些框尚未确认方向，未送入点数分类，也没有加入记牌候选。",
                  wraplength=800).pack(anchor="w", padx=10, pady=8)
        body = ttk.Frame(window)
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        listing = tk.Listbox(body, width=40, exportselection=False)
        listing.pack(side=tk.LEFT, fill=tk.BOTH)
        image_label = tk.Label(body)
        image_label.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8)
        details = ttk.Label(window, wraplength=820)
        details.pack(fill=tk.X, padx=10, pady=8)
        rows = self.session.result.geometry_review
        loaded = self.loaded
        for i, row in enumerate(rows):
            listing.insert(tk.END, f"{i+1}. {row['candidate_id'][:8]}  牌缘/方向不确定")
        def select(_event=None):
            if not listing.curselection():
                return
            row = rows[listing.curselection()[0]]
            x,y,w,h = (row["bbox"][k] for k in ("x","y","w","h"))
            left,top = max(0,x-65),max(0,y-55)
            width = min(loaded.width,x+w+85)-left
            height = min(loaded.height,y+h+70)-top
            rgb = crop_rgb(loaded,left,top,width,height)
            # Mark the candidate on a display copy; original pixels stay intact.
            pixels = bytearray(rgb)
            for yy in range(y-top,y-top+h):
                for xx in range(x-left,x-left+w):
                    if yy in (y-top,y-top+h-1) or xx in (x-left,x-left+w-1):
                        offset=(yy*width+xx)*3
                        pixels[offset:offset+3]=b"\xff\xb4\x00"
            _ppm(image_label,bytes(pixels),width,height,max_side=380)
            details.configure(text=f"框 {row['bbox']}  策略 {row['corner_policy']}\n"
                                   "只查看证据；已有人工标注保持原样。")
        listing.bind("<<ListboxSelect>>",select)
        listing.selection_set(0)
        select()
        def close():
            self._geometry_dialog = None
            window.destroy()
        window.protocol("WM_DELETE_WINDOW",close)

    def _decision(self, operation=None, face=None, rank=None) -> ConfirmDecision:
        if not self.session:
            raise VisionBridgeError("尚未打开图片")
        zh = self.var_op.get()
        op, face_default = OPS_ZH[zh]
        if operation:
            op = operation
        if face is None:
            face = face_default
        chosen_rank = rank if rank is not None else self.var_rank.get()
        if chosen_rank == "未知":
            chosen_rank = None
            if op == OP_NEW:
                face = FACE_UNKNOWN_LEDGER
        return ConfirmDecision(
            observation_id=self.var_obs.get(),
            operation=op,
            seat=self.var_seat.get(),
            confirmed_rank=chosen_rank,
            face_state=face,
            target_event_id=self.var_target.get().strip() or None,
        )

    def commit_selected(self):
        self.app._operation_start_revision = self.app.ctrl.commit_revision
        try:
            result = self.session.confirm(self._decision())
            if self.tracker is not None and result.status in {"committed", "duplicate"}:
                self.tracker.mark_committed(result.observation_id)
            self.var_info.set(result.message)
            self.app.refresh_all()
            if result.already_saved:
                messagebox.showinfo("未重复入账", result.message, parent=self)
        except VisionBridgeError as exc:
            messagebox.showerror("不能入账", str(exc), parent=self)
        except Exception as exc:
            self.app.fail(exc)

    def reject_selected(self):
        try:
            result = self.session.confirm(self._decision(operation=OP_REJECT))
            if self.tracker is not None:
                self.tracker.mark_rejected(result.observation_id)
            self.var_info.set(result.message)
            self.app.refresh_vision_banner()
        except VisionBridgeError as exc:
            messagebox.showerror("不能拒绝", str(exc), parent=self)


def open_vision_window(app):
    existing = getattr(app, "_vision_win", None)
    if existing is not None:
        try:
            if existing.winfo_exists():
                existing.lift()
                return existing
        except tk.TclError:
            pass
    win = VisionReviewWindow(app)
    app._vision_win = win
    return win
