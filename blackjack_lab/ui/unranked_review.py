"""Frozen uncertain positions remain inspectable without creating card facts."""
import tkinter as tk
from tkinter import ttk

from .overlay_windows import style_owned_window


class UnrankedReviewWindow(tk.Toplevel):
    def __init__(self, panel):
        self.feed = panel.feed
        self.record = self.feed.snapshot_unranked()
        super().__init__(panel)
        self.panel = panel
        self.title("未定区域 · 此帧固定，后台采集继续")
        self.geometry("1050x650")
        self.status = tk.StringVar(value="这些是模型没有给出牌级的区域，可能是牌、花色、文字或手部；数量不等于牌数。")
        ttk.Label(self, textvariable=self.status, wraplength=1010, padding=8).pack(fill=tk.X)
        toolbar = ttk.Frame(self, padding=4)
        toolbar.pack(fill=tk.X)
        self.choice = tk.StringVar()
        self.selector = ttk.Combobox(toolbar, textvariable=self.choice, state="readonly", width=57,
            values=[f"区域 {i+1} · {r['reason']}" for i, r in enumerate(self.record['regions'])])
        self.selector.pack(side=tk.LEFT, padx=4)
        self.selector.current(0)
        self.selector.bind("<<ComboboxSelected>>", lambda e: self.draw())
        ttk.Button(toolbar, text="作为补录草稿", command=self.add_draft).pack(side=tk.LEFT, padx=4)
        self.zoom = tk.StringVar(value="原始像素")
        zoom = ttk.Combobox(toolbar, textvariable=self.zoom, values=("适应窗口", "原始像素", "放大两倍"),
                            state="readonly", width=10)
        zoom.pack(side=tk.RIGHT)
        zoom.bind("<<ComboboxSelected>>", lambda e: self.draw())
        body = ttk.Frame(self)
        body.pack(fill=tk.BOTH, expand=True)
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(body, bg="#10202a", highlightthickness=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        xscroll = ttk.Scrollbar(body, orient=tk.HORIZONTAL, command=self.canvas.xview)
        yscroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.canvas.yview)
        xscroll.grid(row=1, column=0, sticky="ew")
        yscroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(xscrollcommand=xscroll.set, yscrollcommand=yscroll.set)
        self.original = tk.PhotoImage(file=self.record["source_image"])
        # Windows can deliver the real canvas dimensions after the first idle
        # pass. Recenter on Configure so the chosen crop is not cut off.
        self.canvas.bind("<Configure>", lambda event: self.draw())
        self.update_idletasks()
        self.draw()
        style_owned_window(self, editing=True)

    def draw(self):
        if self.zoom.get() == "放大两倍":
            self.photo, scale = self.original.zoom(2), 2
        elif self.zoom.get() == "适应窗口":
            factor = max(1, (self.original.width()+self.canvas.winfo_width()-1)//self.canvas.winfo_width())
            self.photo, scale = self.original.subsample(factor), 1/factor
        else:
            self.photo, scale = self.original, 1
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self.photo, anchor="nw")
        region = self.record["regions"][self.selector.current()]
        x,y,w,h = [region["bbox"][k]*scale for k in ("x","y","w","h")]
        self.canvas.create_rectangle(x,y,x+w,y+h,outline="#fcb83f",width=3)
        self.canvas.configure(scrollregion=(0,0,self.photo.width(),self.photo.height()))
        self.canvas.xview_moveto(max(0,(x+w/2-self.canvas.winfo_width()/2)/self.photo.width()))
        self.canvas.yview_moveto(max(0,(y+h/2-self.canvas.winfo_height()/2)/self.photo.height()))

    def add_draft(self):
        try:
            if self.panel.feed is not self.feed:
                raise ValueError("来源已重新绑定，请打开新区域快照")
            draft = self.feed.draft_region(self.record, self.selector.current())
            if draft is None:
                raise ValueError("这个区域已经加入草稿，请回面板核对原草稿")
            self.panel.refresh()
            self.panel.var_status.set("手动选择了未定区域；请判断新牌/已有牌、座位及牌级，确认后才记账")
            self.destroy()
            self.panel.focus_set()
        except Exception as exc:
            self.status.set(str(exc))
