"""Explicit window selection using the existing metadata-only Windows listing."""
import tkinter as tk
from tkinter import messagebox, ttk


class WindowSourcePicker(tk.Toplevel):
    def __init__(self, master, on_selected):
        super().__init__(master)
        self.title('选择实时识牌窗口')
        self.geometry('860x440')
        self.transient(master)
        self.on_selected = on_selected
        self.windows = {}
        ttk.Label(self, text='选择你要捕获的窗口。开始后按所选样式裁区，候选自动显示，确认前不入账。',
                  wraplength=820, padding=10).pack(fill=tk.X)
        self.table = ttk.Treeview(self, columns=('title','process','size'), show='headings', selectmode='browse')
        for name, title, width in [('title','窗口',430),('process','进程',180),('size','尺寸',120)]:
            self.table.heading(name,text=title)
            self.table.column(name,width=width)
        self.table.pack(fill=tk.BOTH,expand=True,padx=10)
        bar=ttk.Frame(self,padding=10);bar.pack(fill=tk.X)
        ttk.Button(bar,text='刷新窗口列表',command=self.refresh).pack(side=tk.LEFT)
        ttk.Button(bar,text='开始所选窗口预览',command=self.select).pack(side=tk.RIGHT)
        self.var_status=tk.StringVar()
        ttk.Label(bar,textvariable=self.var_status).pack(side=tk.LEFT,padx=10)
        self.refresh()

    def refresh(self):
        from ..capture.window_list import list_capturable_windows
        try:found=list_capturable_windows()
        except Exception as exc:
            self.var_status.set(str(exc));return
        self.windows={str(info.hwnd):info for info in found if not info.minimized}
        for row in self.table.get_children():self.table.delete(row)
        for key,info in self.windows.items():
            self.table.insert('',tk.END,iid=key,values=(info.title,info.process_name,f'{info.width}×{info.height}'))
        self.var_status.set(f'{len(self.windows)} 个可选窗口；当前尚未捕获')

    def select(self):
        selected=self.table.selection()
        if not selected:
            self.var_status.set('请先选择一个窗口。');return
        try:
            from ..capture.overlay_exclusion import refuse_overlay_source
            refuse_overlay_source(self.windows[selected[0]])
            self.on_selected(self.windows[selected[0]])
        except Exception as exc:
            messagebox.showerror('窗口预览未启动',str(exc),parent=self);return
        self.destroy()
