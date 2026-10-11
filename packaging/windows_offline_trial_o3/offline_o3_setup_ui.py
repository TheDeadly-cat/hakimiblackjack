"""Visible folder selection, staged progress and an explicit launch action."""
from pathlib import Path
import os
import queue
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from offline_o3_identity import TITLE, PACKAGE_ID, EXECUTABLE


class InstallerWindow(tk.Tk):
    def __init__(self, installer, initial=None, reuse_data=False):
        super().__init__()
        self.installer = installer
        self.messages = queue.Queue()
        self.busy = False
        self.result = None
        self.exit_code = 0
        self._poll_id = None
        self.title(TITLE + ' · 安装')
        self.geometry('650x390')
        self.minsize(600,390)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.path = tk.StringVar(value=str(initial or (Path(os.environ['LOCALAPPDATA'])/'HakimiBJTrial'/PACKAGE_ID)))
        self.reuse = tk.BooleanVar(value=reuse_data)
        self.stage = tk.StringVar(value='选择安装位置，然后点击“安装”。')
        self.detail = tk.StringVar(value='每个自定义文件夹使用独立记录。原安装与资料保留。')
        area = ttk.Frame(self,padding=22);area.pack(fill='both',expand=True)
        ttk.Label(area,text='安装 Hakimi Blackjack',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
        ttk.Label(area,text='安装位置',padding=(0,16,0,5)).pack(anchor='w')
        row=ttk.Frame(area);row.pack(fill='x')
        self.entry=ttk.Entry(row,textvariable=self.path);self.entry.pack(side='left',fill='x',expand=True)
        self.browse=ttk.Button(row,text='选择文件夹…',command=self.choose);self.browse.pack(side='left',padx=(8,0))
        self.reuse_check=ttk.Checkbutton(area,text='重新安装时使用此目录保留的记录',variable=self.reuse)
        self.reuse_check.pack(anchor='w',pady=(10,0))
        ttk.Label(area,textvariable=self.stage,font=('Microsoft YaHei UI',11,'bold'),wraplength=585).pack(anchor='w',pady=(18,8))
        ttk.Label(area,textvariable=self.detail,wraplength=585).pack(anchor='w')
        self.bar=ttk.Progressbar(area,mode='indeterminate');self.bar.pack(fill='x',pady=(12,10))
        self.actions=ttk.Frame(area);self.actions.pack(side='bottom',fill='x')
        self.install_button=ttk.Button(self.actions,text='安装',command=self.start);self.install_button.pack(side='left')
        self.launch_button=ttk.Button(self.actions,text='启动 Blackjack',command=self.launch,state='disabled')
        self.launch_button.pack(side='left',padx=8)
        self.open_button=ttk.Button(self.actions,text='打开安装文件夹',command=self.open_folder,state='disabled')
        self.open_button.pack(side='left')
        ttk.Button(self.actions,text='关闭',command=self.close).pack(side='right')
        ttk.Label(area,text='内含 Python/Tk；Windows 需要 .NET Framework 4。试用版，大历史可能延迟。',wraplength=585).pack(side='bottom',anchor='w',pady=(12,12))
        self._poll_id = self.after(75,self.poll)
        self.after_idle(self.lift)

    def choose(self):
        current=Path(self.path.get()).expanduser()
        directory=filedialog.askdirectory(parent=self,title='选择所在文件夹（将在其中新建应用文件夹）',initialdir=str(current.parent if current.parent.exists() else Path.home()))
        if directory:
            folder=Path(directory)
            # Selecting Desktop places the application in its own folder.
            if not (folder/'app/install.json').is_file() and not (folder/'install.json').is_file() and folder.name!='Hakimi Blackjack T1O3':
                folder=folder/'Hakimi Blackjack T1O3'
            self.path.set(str(folder))

    def start(self):
        if self.busy or self.result is not None:return
        value=self.path.get().strip()
        if not value:
            self.stage.set('请选择安装文件夹');return
        destination=Path(value)
        if not destination.is_absolute():
            self.stage.set('请填写完整安装路径');return
        self.busy=True;self.exit_code=0
        for widget in (self.entry,self.browse,self.reuse_check,self.install_button):widget.configure(state='disabled')
        self.stage.set('准备安装');self.detail.set('正在处理，请稍候。关闭窗口不会中断写入。');self.bar.start(12)
        reuse=self.reuse.get()
        def work():
            try:
                result=self.installer(reuse,destination,progress=lambda text:self.messages.put(('stage',text)))
                self.messages.put(('success',result))
            except Exception as error:self.messages.put(('error',str(error)))
        threading.Thread(target=work,daemon=False).start()

    def poll(self):
        self._poll_id = None
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='stage':self.stage.set(value)
                elif kind=='success':
                    self.busy=False;self.result=value;self.bar.stop();self.stage.set('安装完成')
                    root,logs=value
                    self.detail.set('程序：'+str(root)+'\n安装日志：'+str(logs)+'\n点击“启动 Blackjack”开始使用。')
                    self.launch_button.configure(state='normal');self.open_button.configure(state='normal')
                else:
                    self.busy=False;self.exit_code=1;self.bar.stop();self.stage.set('安装未完成')
                    self.detail.set(value+'\n原记录保留，请检查位置或正常关闭已运行程序后重试。')
                    for widget in (self.entry,self.browse,self.reuse_check,self.install_button):widget.configure(state='normal')
        except queue.Empty:pass
        self._poll_id = self.after(75,self.poll)

    def destroy(self):
        if self._poll_id is not None:
            self.after_cancel(self._poll_id)
            self._poll_id = None
        super().destroy()

    def launch(self):
        if self.result is None:return
        root,_=self.result
        try:
            subprocess.Popen([str(root/EXECUTABLE)],cwd=root,creationflags=0x08000000)
            self.stage.set('已启动 Blackjack')
            self.launch_button.configure(state='disabled')
        except OSError as error:messagebox.showerror(TITLE,str(error),parent=self)

    def open_folder(self):
        if self.result is not None:os.startfile(self.result[0])

    def close(self):
        if self.busy:
            self.detail.set('安装正在写入或检查程序，请等待完成后关闭。');return
        self.destroy()


def run_installer(installer,initial=None,reuse_data=False):
    window=InstallerWindow(installer,initial,reuse_data);window.mainloop();return window.exit_code
