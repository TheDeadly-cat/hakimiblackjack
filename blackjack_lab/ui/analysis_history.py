"""Paged main-bet history. Metadata never stands in for a validated result."""
import tkinter as tk
from tkinter import ttk,messagebox

from ..analysis.sidebets.background import LatestWorker
from ..storage.history_catalog import metadata_page
from ..storage.analysis_snapshots import is_minimal_result


class AnalysisHistory(tk.Toplevel):
    def __init__(self,panel):
        super().__init__(panel.app)
        self.panel=panel;self.app=panel.app;self.store=self.app.ctrl.analysis_store
        self.title('历史分析（原结果只读；重算创建新快照）');self.geometry('800x650');self.transient(self.app)
        self.worker=LatestWorker();self.closed=False;self.expected={};self.entries=[]
        self.offset=0;self.total=0;self.loading=False;self.saved=None;self.damage_note=''
        pages=ttk.Frame(self);pages.pack(fill=tk.X,padx=6)
        ttk.Button(pages,text='上一页',command=lambda:self.page(-1)).pack(side=tk.LEFT)
        ttk.Button(pages,text='下一页',command=lambda:self.page(1)).pack(side=tk.LEFT)
        self.page_label=tk.StringVar();ttk.Label(pages,textvariable=self.page_label).pack(side=tk.LEFT)
        self.listing=tk.Listbox(self,height=7,exportselection=False);self.listing.pack(fill=tk.X,padx=6,pady=4)
        self.listing.bind('<<ListboxSelect>>',self.select)
        self.display=tk.Text(self,wrap=tk.WORD,state=tk.DISABLED);self.display.pack(fill=tk.BOTH,expand=True,padx=6)
        self.recompute_button=ttk.Button(self,text='按选中结果的原事件前缀重新计算',command=self.recompute)
        self.recompute_button.pack(pady=5);self.recompute_button.state(['disabled'])
        self.status=tk.StringVar();ttk.Label(self,textvariable=self.status,wraplength=780).pack(pady=3)
        self.protocol('WM_DELETE_WINDOW',self.close);self.reload();self.poll_id=self.after(70,self.poll)

    def page(self,direction):
        offset=self.offset+direction*100
        if self.loading or not 0<=offset<self.total:return
        self.offset=offset;self.reload()

    def reload(self):
        if self.closed or getattr(self.app,'_closing',False):return
        self.loading=True;self.saved=None;self.expected.pop('verify',None)
        self.recompute_button.state(['disabled']);self.status.set('后台读取本页元数据（未核验）；选中记录后完整核验。')
        offset=self.offset
        self.expected['load']=self.worker.submit('history_load',lambda:metadata_page(self.store.directory,offset=offset,cancelled=self.worker.closed.is_set))

    def select(self,_event=None):
        if self.closed or self.loading or getattr(self.app,'_closing',False):return
        indices=self.listing.curselection()
        if not indices:return
        sid=self.entries[indices[0]]['snapshot_id'];self.saved=None
        self.recompute_button.state(['disabled']);self.status.set('正在完整核验文件；原数据库前缀将在复算前核对。')
        self.show('记录尚未核验。')
        self.expected['verify']=self.worker.submit('history_verify',lambda:self.store.load(sid))

    def show(self,text):
        self.display.configure(state=tk.NORMAL);self.display.delete('1.0',tk.END)
        self.display.insert('1.0',text);self.display.configure(state=tk.DISABLED)

    def poll(self):
        if self.closed:return
        if getattr(self.app,'_closing',False):self.poll_id=self.after(70,self.poll);return
        from .analysis_panel import DISPLAY_RESULT_SCHEMAS,format_result
        for message in self.worker.poll():
            kind=next((k for k,v in self.expected.items() if v==message['request_id']),None)
            if kind is None:continue
            self.expected.pop(kind,None)
            if 'error' in message:
                if kind=='load':self.loading=False
                self.status.set('未核验：'+message['error']+self.damage_note);continue
            value=message['result']
            if kind=='load':
                self.loading=False;self.entries=value['entries'];self.total=value['total']
                self.damage_note=('；本页损坏记录已保留：'+'；'.join(d['file']+' '+d['error'] for d in value['damaged'])) if value['damaged'] else ''
                self.page_label.set(f'文件新到旧 · 第 {self.offset//100+1} 页 · 共 {self.total} 条 · 元数据未核验')
                self.listing.delete(0,tk.END)
                for item in self.entries:self.listing.insert(tk.END,f"未核验 · {item['seat']} · #{item['through_seq']} · {item['snapshot_id'][:8]}")
                if self.entries:self.listing.selection_set(0);self.select()
                else:self.status.set('无已保存分析。'+self.damage_note)
            else:
                self.saved=value;result=value['result'];minimal=is_minimal_result(result)
                supported=result['schema'] in DISPLAY_RESULT_SCHEMAS
                self.show('这是兼容的旧存储信封，不包含完整分析结果，不能复算。\n输入摘要：'+result['input_digest'] if minimal
                    else '当前界面尚不支持此结果格式，原文件已保留，不能在此版本复算。' if not supported
                    else format_result(result,historical=True))
                self.recompute_button.state(['disabled'] if minimal or not supported else ['!disabled'])
                self.status.set('文件完整性已核验；原数据库前缀将在复算前核对。'+self.damage_note)
        self.poll_id=self.after(70,self.poll)

    def recompute(self):
        if self.closed or self.saved is None or getattr(self.app,'_closing',False):return
        from .analysis_panel import DISPLAY_RESULT_SCHEMAS
        result=self.saved['result']
        if is_minimal_result(result) or result['schema'] not in DISPLAY_RESULT_SCHEMAS:return
        try:
            snapshot=self.app.ctrl.recompute_input(self.saved)
            self.panel.start(snapshot,self.saved['snapshot_id']);self.close()
        except Exception as error:messagebox.showerror('不能复算',str(error),parent=self)

    def close(self):
        if not self.closed:
            self.closed=True;self.after_cancel(self.poll_id);self.worker.close()
        else:self.after_cancel(self.poll_id)
        self._finish_close()

    def _finish_close(self):
        if not self.worker.stopped:self.poll_id=self.after(25,self._finish_close);return
        self.destroy()
