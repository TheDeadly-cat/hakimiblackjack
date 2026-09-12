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
    VisionBridgeError, VisionReviewSession,
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
        banner = ttk.Label(
            self,
            text="旁观录像或截图：候选留在工作区。同一张牌多帧只确认一次。确认前不入账。匹配度不是正确概率。",
            wraplength=940, foreground="#7a1f1f")
        banner.pack(fill=tk.X, padx=8, pady=6)
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, padx=8)
        ttk.Button(bar, text="打开本地 PNG/JPEG", command=self.open_image).pack(side=tk.LEFT)
        ttk.Button(bar, text="打开本地录像", command=self.open_video).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="绑定到当前轮", command=self.rebind).pack(side=tk.LEFT, padx=6)
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
        if self.video_reader is not None:
            self.video_reader.close()
            self.video_reader = None
        self.app._vision_win = None
        self.destroy()

    def _shoe_note(self) -> str:
        label = self.var_shoe_start.get()
        kind = next((k for k, v in SHOE_START_LABELS.items() if v == label), SHOE_START_UNCERTAIN)
        return shoe_start_intent(kind).note

    def rebind(self):
        if not self.session:
            return
        try:
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
        if self.video_reader is not None:
            self.video_reader.close()
            self.video_reader = None
        try:
            from ..vision.evidence_store import EvidenceStore
            from ..vision.image_io import load_image
            from ..vision.pipeline import infer_layout, recognize_loaded
            from ..vision.table_crop import apply_layout_crops
            loaded = load_image(path)
            layout = infer_layout(loaded)
            canvas = apply_layout_crops(loaded, layout)
            result = recognize_loaded(canvas, layout=layout)
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
        try:
            from ..vision.tracker import FrameTracker
            from ..vision.video_io import VideoReader
            if self.video_reader is not None:
                self.video_reader.close()
            self.video_reader = VideoReader(path)
            self.tracker = FrameTracker()
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
            from ..vision.pipeline import infer_layout, recognize_loaded
            from ..vision.table_crop import apply_layout_crops
            frame_index = int(float(self.var_frame.get()))
            loaded = self.video_reader.seek(frame_index)
            layout = infer_layout(loaded)
            canvas = apply_layout_crops(loaded, layout)
            result = recognize_loaded(canvas, layout=layout)
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
                for obs in result.observations:
                    self.session.register_observation(obs)
                self.session.result.warnings = result.warnings
            for track in self.tracker.tracks:
                if track.committed:
                    continue
                if track.observation_id in self.session.commits:
                    self.tracker.mark_committed(track.observation_id)
        except Exception as exc:
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
        for child in self.list_frame.winfo_children():
            child.destroy()
        self.photos.clear()
        if not self.session or not self.loaded:
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
        if ids:
            self.cmb_obs.configure(values=ids)
            self.var_obs.set(ids[0])
        if self.session.result.observations:
            first = self.session.result.observations[0]
            if first.accepted_rank():
                self.var_rank.set(first.accepted_rank())
            if first.seat_hint:
                self.var_seat.set(first.seat_hint)

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
