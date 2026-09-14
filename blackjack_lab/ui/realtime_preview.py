"""Continuously rendered realtime candidates and stage timing in the existing Tk app."""
from __future__ import annotations

import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


class RealtimePreviewWindow(tk.Toplevel):
    def __init__(self, master, session, *, on_review=None, adapters=None, on_assist=None):
        super().__init__(master)
        self.session, self.on_review = session, on_review
        self.adapters=adapters or {'当前检测器':session.adapter}
        is_live=getattr(session.source,'is_live',False)
        self.title('哈基米 · 窗口实时识牌' if is_live else "哈基米 · 1倍速识牌预览")
        self.geometry("1450x880")
        self.minsize(1000, 680)
        self._closed = False
        self._pending_callbacks = set()
        self._frame_key = self._row_id = None
        self._overlay_items = []
        self._photo = None
        self._scale = 1.0
        self._displayed_packet = None
        self._displayed_result = None
        self._ui_updates = 0
        self._last_painted_ns = None
        self.var_state = tk.StringVar(value='正在启动所选窗口捕获…' if is_live else "正在校验源文件并启动 1 倍速播放…")
        self.var_metrics = tk.StringVar(value="等待第一帧；延迟从真实取帧与界面提交记录计算")
        bar = ttk.Frame(self, padding=8)
        bar.pack(fill=tk.X)
        self.var_fps=tk.StringVar(value=f"{session.recognition_fps:g}")
        self.var_detector=tk.StringVar(value=next(k for k,v in self.adapters.items() if v is session.adapter))
        ttk.Combobox(bar,textvariable=self.var_detector,values=tuple(self.adapters),state='readonly',width=12).pack(side=tk.LEFT,padx=4)
        ttk.Label(bar,text="识别 FPS").pack(side=tk.LEFT)
        ttk.Combobox(bar,textvariable=self.var_fps,values=("5","8","10","15"),state="readonly",width=4).pack(side=tk.LEFT,padx=4)
        ttk.Button(bar,text='重新捕获' if is_live else "重新播放",command=self.restart).pack(side=tk.LEFT,padx=4)
        ttk.Label(bar, textvariable=self.var_state).pack(side=tk.LEFT)
        ttk.Button(bar, text="停止", command=self.stop).pack(side=tk.RIGHT)
        ttk.Button(bar, text="保存运行记录", command=self.export).pack(side=tk.RIGHT, padx=6)
        if on_review is not None:
            ttk.Button(bar, text="冻结识别帧并核对", command=self.review).pack(side=tk.RIGHT, padx=6)
        if on_assist is not None:
            ttk.Button(bar, text="持续核对（采集不停）", command=lambda: on_assist(self.session)).pack(side=tk.RIGHT, padx=6)
        ttk.Label(self, textvariable=self.var_metrics, padding=(8, 4)).pack(fill=tk.X)
        self.var_quality=tk.StringVar(value='正在观察标定区域的画面变化…')
        ttk.Label(self,textvariable=self.var_quality,padding=(8,2),wraplength=1400).pack(fill=tk.X)
        ttk.Label(self, text="橙色＝本帧候选　绿色＝短时稳定　灰色＝待核或暂失　稳定值会过期；正式记录仍需人工确认。",
            padding=(8, 2)).pack(fill=tk.X)
        self.canvas = tk.Canvas(self, background="#10202a", highlightthickness=0, height=410)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=5)
        self._image_item = self.canvas.create_image(0, 0, anchor="nw")
        columns = ("id", "current", "stable", "state", "age", "candidate")
        table_frame = ttk.Frame(self)
        table_frame.pack(fill=tk.X, padx=8, pady=4)
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=10)
        scroll = ttk.Scrollbar(table_frame, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        for key, title, width in [("id","候选 ID",160),("current","本帧点数",110),("stable","稳定点数",110),
                                  ("state","身份状态",240),("age","距最近观察",140),("candidate","取帧→候选",140)]:
            self.table.heading(key, text=title)
            self.table.column(key, width=width, anchor="center")
        self.table.pack(side=tk.LEFT, fill=tk.X, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.var_model=tk.StringVar(value=session.adapter.identity_text)
        ttk.Label(self, textvariable=self.var_model, wraplength=1400, padding=8).pack(fill=tk.X)
        from ..vision.temporal_preview import PERSISTENT_OBSERVATION_POLICY
        if getattr(session,'temporal_policy',None)==PERSISTENT_OBSERVATION_POLICY:
            ttk.Label(self,text='持续观察实验：新画面中的一致牌级可稳定显示；相同裁片仍只有一份像素证据。',padding=(8,2)).pack(fill=tk.X)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self._schedule(20, self._poll)
        self.session.start()

    def stop(self):
        self.session.stop()
        self.var_state.set("已停止；画面与候选不再作为新的观察证据")

    def _schedule(self, delay, callback):
        pending = {}
        def run():
            self._pending_callbacks.discard(pending['id'])
            if not self._closed:callback()
        pending['id'] = self.after(delay, run)
        self._pending_callbacks.add(pending['id'])

    def restart(self):
        from ..realtime_preview import RealtimePreviewSession
        old=self.session
        old.stop()
        self.var_state.set("正在停止上一轮处理…")
        def start_when_stopped():
            if self._closed or self.session is not old:
                return
            if not old.finished or not getattr(old.source,'finished',True):
                self._schedule(25,start_when_stopped)
                return
            source=old.source.clone()
            adapter=self.adapters[self.var_detector.get()]
            self.session=RealtimePreviewSession(source,old.style,adapter,recognition_fps=float(self.var_fps.get()),
                evidence_limit=old.records.maxlen,observe_regions=old.region_observer is not None,
                temporal_policy=old.temporal_policy)
            self.var_model.set(adapter.identity_text)
            self._frame_key=None
            self._row_id=None
            self._displayed_result=None
            for rectangle,text in self._overlay_items:
                self.canvas.itemconfigure(rectangle,state="hidden")
                self.canvas.itemconfigure(text,state="hidden")
            for iid in self.table.get_children():self.table.delete(iid)
            self.var_state.set('正在校验窗口并重新捕获，模型继续常驻' if getattr(source,'is_live',False)
                               else "正在校验来源；本轮记录从新播放开始，模型继续常驻")
            self.session.start()
        start_when_stopped()

    def close(self):
        self.destroy()

    def destroy(self):
        # Tk destroys children directly when their owner closes; that route
        # does not invoke this window's WM_DELETE_WINDOW callback.
        if self._closed:
            return
        self._closed = True
        self.session.stop()
        for callback in self._pending_callbacks:
            try:self.after_cancel(callback)
            except tk.TclError:pass
        self._pending_callbacks.clear()
        super().destroy()

    def export(self):
        path = filedialog.asksaveasfilename(parent=self, title="保存本次实时计时记录", defaultextension=".json",
            initialfile="realtime-preview.json", filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            self.session.save(path)
        except Exception as exc:
            messagebox.showerror("未保存", str(exc), parent=self)

    def review(self):
        row = self._displayed_result
        if row is None or not self.session.can_review_result(row):
            messagebox.showinfo('尚无可核对的识别帧', '请等待候选显示；来源改变或已停止的旧结果不能继续核对。', parent=self)
            return
        if self.on_review is not None:
            try:
                if self.on_review(self.session, row):self.close()
            except Exception as exc:
                messagebox.showerror('未送入人工核对', str(exc), parent=self)

    def _draw_source(self, packet):
        from PIL import Image, ImageTk
        pixels = packet.pixels
        rgb = pixels[:, :, ::-1]
        image = Image.fromarray(rgb)
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        self._scale = min(1.0, width/image.width, height/image.height)
        size = (max(1, round(image.width*self._scale)), max(1, round(image.height*self._scale)))
        image = image.resize(size, Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(image)
        self.canvas.itemconfigure(self._image_item, image=self._photo)
        self._displayed_packet = packet
        self._ui_updates += 1
        # Explicitly a Tk display submission timestamp, not a hardware scanout claim.
        self._schedule('idle',lambda p=packet,owner=self.session: self._source_painted(owner,p))

    def _source_painted(self, owner, packet):
        if not self._closed and owner is self.session:
            self._last_painted_ns = time.perf_counter_ns()
            owner.note_source_display(packet, self._last_painted_ns)

    def _draw_result(self, row, now):
        tracks = row.display_tracks(now)
        objects = [(item["bbox"], item["stable_rank"] or item["observed_rank"] or ("?" if item["current"] else "暂失"),
                    "#a1acb3" if not item["current"] else "#56df9c" if item["stable_rank"] else "#ffb44b")
                   for item in tracks if item["identity_state"] != "expired"]
        if now-row.packet.observed_monotonic_ns <= 550_000_000:
            objects += [(item["bbox"], "待核", "#a1acb3") for item in row.recognition.geometry_review]
        for i, (box, label, color) in enumerate(objects):
            if i == len(self._overlay_items):
                self._overlay_items.append((self.canvas.create_rectangle(0,0,1,1,width=2),
                    self.canvas.create_text(0,0,anchor="sw",font=("Microsoft YaHei UI",10,"bold"))))
            rectangle, text = self._overlay_items[i]
            x,y,w,h=(box[k]*self._scale for k in ("x","y","w","h"))
            self.canvas.coords(rectangle,x,y,x+w,y+h)
            self.canvas.itemconfigure(rectangle,outline=color,state="normal")
            self.canvas.coords(text,x,y)
            self.canvas.itemconfigure(text,text=label,fill=color,state="normal")
        for rectangle, text in self._overlay_items[len(objects):]:
            self.canvas.itemconfigure(rectangle,state="hidden")
            self.canvas.itemconfigure(text,state="hidden")
        ids=set()
        candidate=(row.timings["candidate_ready_ns"]-row.packet.observed_monotonic_ns)/1_000_000
        state_names={"expired":"已过期", "temporarily_unseen":"暂时未见", "rank_conflict":"点数冲突／待核",
                     "temporally_associated":"短时关联／物理身份待验", "new_unverified":"新候选／待稳定"}
        for item in tracks:
            iid=item["track_id"];ids.add(iid)
            values=(iid,item["observed_rank"] or "—",item["stable_rank"] or "—",
                    state_names[item["identity_state"]],f"{item['age_ms']:.0f} ms",f"{candidate:.0f} ms")
            if self.table.exists(iid):
                self.table.item(iid,values=values)
            else:
                self.table.insert("",tk.END,iid=iid,values=values)
        for iid in self.table.get_children():
            if iid not in ids:
                self.table.delete(iid)
        self._row_id=row.row_id
        self._displayed_result=row
        # Keep the values submitted to Tk rather than recomputing freshness later.
        self._schedule('idle',lambda rid=row.row_id,owner=self.session,drawn=tracks,
            source=self._displayed_packet,scale=self._scale:
            owner.note_rendered_state(rid,drawn,source,time.perf_counter_ns(),scale))

    def _poll(self):
        if self._closed:
            return
        now=time.perf_counter_ns()
        packet=self.session.source.preview()
        source_changed=False
        if packet is not None:
            key=(packet.token(),packet.frame_id,self.canvas.winfo_width(),self.canvas.winfo_height())
            if key!=self._frame_key:
                self._frame_key=key;source_changed=True;self._draw_source(packet)
        row=self.session.latest_result()
        if row is not None:
            self._draw_result(row,now)
            t=row.timings
            queue=(t['picked_ns']-row.packet.observed_monotonic_ns)/1_000_000
            detect=(t['detection_end_ns']-t['detection_start_ns'])/1_000_000
            classify=(t['classification_end_ns']-t['classification_start_ns'])/1_000_000
            submitted=self.session.result_display_ns(row.row_id)
            display="等待绘制" if submitted is None else f"{(submitted-row.packet.observed_monotonic_ns)/1_000_000:.0f} ms"
            self.var_metrics.set(f"排队 {queue:.0f} ms　检测 {detect:.0f} ms　分类 {classify:.0f} ms　取帧→显示 {display}　处理 {self.session.processed} 帧")
        elif self._row_id is not None:
            self._row_id=None
            for rectangle,text in self._overlay_items:
                self.canvas.itemconfigure(rectangle,state="hidden")
                self.canvas.itemconfigure(text,state="hidden")
            for iid in self.table.get_children():
                self.table.delete(iid)
        report=self.session.source.report()
        quality=self.session.latest_region_observation()
        if self.session.region_observer is None:
            self.var_quality.set('区域观察未启用；使用固定频率对照路径')
        elif self.session.stopped:
            self.var_quality.set('区域观察已停止；旧结果不作为新的证据')
        elif self.session.finished:
            self.var_quality.set(f'区域观察已结束；跳过 {self.session.unchanged_regions_skipped} 次相同画面的重复识别')
        elif quality is not None:
            age=max(0,(now-quality['observed_monotonic_ns'])/1e6)
            states=[r['detail_state'] for r in quality['regions']]
            if quality['calibrated_size_matches'] is False:
                message='来源尺寸与检测器标定不一致，请恢复已标定来源'
            elif age>550:
                message='暂未收到新的区域画面；旧结果仍会过期'
            elif 'little_detail' in states:
                message='部分区域缺少画面细节，请检查来源；这不表示桌面没有牌'
            elif 'detail_decreased' in states:
                message='部分区域的画面细节明显下降，请检查清晰度；候选仍须核对'
            elif quality['same_as_completed_inference']:
                message='识别画面与上次相同；复用已有显示，不增加稳定票数'
            else:
                message='收到新画面；按最新帧进行识别'
            self.var_quality.set(message+f'　已跳过重复识别 {self.session.unchanged_regions_skipped} 次')
        media=(packet.media_time_ns/1_000_000_000) if packet is not None and packet.media_time_ns is not None else 0
        is_live=getattr(self.session.source,'is_live',False)
        if is_live:
            base=getattr(self.session.source,'base_ns',None)
            media=(now-base)/1_000_000_000 if base is not None else 0
        error=self.session.error or report.get('error')
        if self.session.stopped:
            self.var_state.set("已停止；保留的画面仅供回看，旧候选已撤回")
        elif error:
            self.var_state.set("运行中断："+error)
        elif self.session.finished:
            self.var_state.set(f"播放已结束　源时刻 {media:.2f}s　记录仍可保存；完整事件准确率待评估")
        elif packet is not None:
            origin=f"WGC 窗口　运行 {media:.1f}s" if is_live else f"1倍速播放　源时刻 {media:.2f}s"
            if is_live:
                from ..capture.contracts import STATUS_LABELS
                origin+='　'+STATUS_LABELS.get(report.get('status'),'等待窗口帧')
            self.var_state.set(f"{origin}　目标识别 {self.session.recognition_fps:g} FPS　队列丢帧 {report.get('dropped_by_queue',0)}　源跳帧 {report.get('source_frames_skipped_for_preview',0)}")
        self._schedule(33,self._poll)
