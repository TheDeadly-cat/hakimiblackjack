"""Read-only opening history and separately saved original-prefix recalculation."""
from datetime import datetime
import json
from queue import Empty, Queue
import threading
from time import perf_counter
import tkinter as tk
from tkinter import ttk

from ..analysis.opening import NOTE
from ..analysis.opening_service import OpeningService, terminal_opening_result, validate_opening_result
from ..storage.opening_snapshots import algorithm_manifest
from ..analysis.sidebets.background import LatestWorker

STATUS = dict(available='已完成', cancelled='已取消', stale='输入已变化', timeout='超时', failed='失败', unsupported='不支持')


def record_text(saved, verification):
    result, data = saved['result'], saved['result']['input']
    lines = ['历史开局估算 · 固定策略 · 原结果只读，不代表当前牌况', verification,
             f"保存：{datetime.fromtimestamp(saved['saved_at']):%Y-%m-%d %H:%M:%S}",
             f"状态：{STATUS[result['status']]}；记录：{saved['snapshot_id']}",
             f"事件前缀：{data['session_id']} / #{data['through_seq']} / {data['prefix_digest']}",
             f"人数：{len(data['participants'])}；本人：{data['participants'][data['focal']]}",
             f"剩余A～9/T：{data['counts']}",
             f"引擎：{result['engine_version']}；策略：{result['strategy_version']}",
             f"输入摘要：{result['input_digest']}；规则摘要：{result['rules_digest']}",
             f"种子：{result.get('seed')}；耗时：{result['elapsed_seconds']:.3f}秒", '', NOTE,
             '95%区间只描述抽样误差，不包括漏录、桌规或实际策略差异。']
    if saved.get('recomputed_from'):
        lines.append('复算来源：'+saved['recomputed_from']+'；原文件保留。')
    if result['status'] == 'available':
        lines += [f"样本：{result['samples']:,}个独立单轮；每1单位底注EV：{result['ev']:+.6f}",
                  f"优势率：{result['advantage_percent']:+.3f}%；95%区间：{result['interval']}",
                  '完整收益直方图（-4至+4，步长0.5）：'+str(result['histogram'])]
    else:
        lines.append('未发布数值：'+result['reason'])
    lines += ['', '锁定桌规：'+json.dumps(json.loads(data['rules_json']), ensure_ascii=False, indent=2),
              '算法源码：'+json.dumps(saved['algorithm_manifest'], ensure_ascii=False, indent=2)]
    return '\n'.join(lines)


class OpeningHistory(tk.Toplevel):
    def __init__(self, owner):
        super().__init__(owner.app)
        self.owner, self.app = owner, owner.app
        self.title('开局历史（原结果只读；复算另存）')
        self.geometry('800x650')
        self.transient(self.app)
        self.service = OpeningService()
        self.attempt = None
        self.entries = []
        self._closed = False
        self._loading = False
        self._load_generation = 0
        self._load_cancel = threading.Event()
        self._load_queue = Queue()
        self._load_threads=[]
        self._close_thread=None
        self.worker=LatestWorker();self.expected={};self._verifying=False;self.verified=None
        self._load_thread=self.worker.thread;self._load_threads=[self.worker.thread]
        self.page_offset=0;self.total=0;self.damage_note=''
        pages=ttk.Frame(self);pages.pack(fill=tk.X,padx=8)
        ttk.Button(pages,text='上一页',command=lambda:self.page(-1)).pack(side=tk.LEFT)
        ttk.Button(pages,text='下一页',command=lambda:self.page(1)).pack(side=tk.LEFT)
        self.page_label=tk.StringVar()
        ttk.Label(pages,textvariable=self.page_label).pack(side=tk.LEFT)
        self.listing = tk.Listbox(self, height=7, exportselection=False)
        self.listing.pack(fill=tk.X, padx=8, pady=6)
        self.listing.bind('<<ListboxSelect>>', self.select)
        self.display = tk.Text(self, wrap=tk.WORD, state=tk.DISABLED)
        self.display.pack(fill=tk.BOTH, expand=True, padx=8)
        self.status = tk.StringVar(value='')
        ttk.Label(self, textvariable=self.status, wraplength=760).pack(fill=tk.X,padx=8,pady=4)
        buttons = ttk.Frame(self); buttons.pack(pady=6)
        self.recompute_button = ttk.Button(buttons, text='按原时点复算并另存', command=self.recompute)
        self.recompute_button.pack(side=tk.LEFT,padx=4)
        ttk.Button(buttons, text='取消复算', command=self.cancel).pack(side=tk.LEFT,padx=4)
        ttk.Button(buttons, text='重试未保存记录', command=owner.retry_saves).pack(side=tk.LEFT,padx=4)
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.reload()
        self.poll_id = self.after(100,self.poll)

    def selected(self):
        indices = self.listing.curselection()
        return self.entries[indices[0]] if indices else None

    def page(self,direction):
        offset=self.page_offset+direction*100
        if self.attempt or self._loading or not 0<=offset<self.total:return
        self.page_offset=offset;self.reload()

    def reload(self, selected_id=None):
        if self._closed or self.attempt or getattr(self.app,'_closing',False):
            return
        old = self.selected()
        if selected_id:self.page_offset=0
        selected_id = selected_id or (old['snapshot_id'] if old else None)
        self._load_cancel.set()
        cancel = self._load_cancel = threading.Event()
        self._load_generation += 1
        generation = self._load_generation
        store = self.app.ctrl.opening_store
        self._loading = True
        self._verifying=False;self.verified=None;self.expected.pop('verify',None)
        self.recompute_button.state(['disabled'])
        self.status.set('后台读取本页元数据（未核验）；选中记录后完整核验。')
        offset=self.page_offset
        def read():
            return dict(kind='load',data=store.page(offset=offset,cancelled=cancel.is_set),selected_id=selected_id)
        self.expected['load']=self.worker.submit('history_load',read)
        self._load_thread=self.worker.thread;self._load_threads=[self.worker.thread]

    def poll_loading(self):
        for message in self.worker.poll():
            kind=next((k for k,v in self.expected.items() if v==message['request_id']),None)
            if kind is None:continue
            self.expected.pop(kind,None)
            if 'error' in message:
                if kind=='load':self._loading=False
                if kind=='verify':self._verifying=False
                self.status.set('未核验：'+message['error']+self.damage_note);continue
            value=message['result']
            if kind=='load':
                self._loading=False;data=value['data'];self.total=data['total']
                self.page_label.set(f'文件新到旧 · 第 {self.page_offset//100+1} 页 · 共 {self.total} 条 · 元数据未核验')
                self.publish_list(data['entries'],data['damaged'],value['selected_id'])
            elif kind=='verify':
                self._verifying=False;selected=self.selected()
                if selected is None or selected['snapshot_id']!=value['saved']['snapshot_id']:continue
                saved=value['saved'];self.entries[self.listing.curselection()[0]]=saved
                self.verified=saved['snapshot_id'] if value['error'] is None else None
                message=('原数据库未核验：'+value['error'] if value['error'] else '原数据库事件前缀已核对；这是历史结果。')
                self.display.configure(state=tk.NORMAL);self.display.delete('1.0',tk.END)
                self.display.insert('1.0',record_text(saved,message));self.display.configure(state=tk.DISABLED)
                self.recompute_button.state(['!disabled'] if self.verified and not self.attempt else ['disabled'])
                self.status.set(message+self.damage_note)

    def publish_list(self, entries, damaged, selected_id):
        self.entries = entries
        self.listing.delete(0,tk.END)
        for saved in self.entries:
            self.listing.insert(tk.END, f"未核验 · #{saved['through_seq']} · {saved['status']} · {saved['snapshot_id'][:8]}")
        self.damage_note=('；本页损坏记录已保留：'+'；'.join(item['file']+' '+item['error'] for item in damaged)) if damaged else ''
        if self.entries:
            index = next((i for i,e in enumerate(self.entries) if e['snapshot_id']==selected_id),0)
            self.listing.selection_set(index)
            self.select()
        else:
            self.recompute_button.state(['disabled'])
            self.status.set('尚无已保存的开局记录。')
        if damaged:
            self.status.set('损坏记录已保留，未读取：'+'；'.join(item['file']+' '+item['error'] for item in damaged))

    def select(self, _event=None):
        if self._closed or self._loading or self.attempt or getattr(self.app,'_closing',False):return
        saved = self.selected()
        if saved is None:
            return
        self.verified=None;self._verifying=True;self.recompute_button.state(['disabled'])
        self.display.configure(state=tk.NORMAL)
        self.display.delete('1.0',tk.END)
        self.display.insert('1.0','尚未核验，正在后台读取完整文件和原数据库前缀。')
        self.display.configure(state=tk.DISABLED)
        self.status.set('正在完整核验选中记录。'+self.damage_note)
        snapshot_id=saved['snapshot_id'];store=self.app.ctrl.opening_store;db=self.app.ctrl.store.db_path
        def verify():
            full=store.load(snapshot_id)
            try:store.verified_input(full,db);error=None
            except Exception as exc:error=str(exc)
            return dict(saved=full,error=error)
        self.expected['verify']=self.worker.submit('history_verify',verify)

    def recompute(self):
        saved = self.selected()
        if saved is None or self.attempt or self._loading or self.verified!=saved['snapshot_id']:
            return
        try:
            snapshot = self.app.ctrl.opening_store.verified_input(saved,self.app.ctrl.store.db_path)
            request_id = self.service.start(snapshot)
            self.attempt = dict(snapshot=snapshot, request_id=request_id, started=perf_counter(),
                prefix=saved['event_prefix'], sources=algorithm_manifest(), recomputed_from=saved['snapshot_id'])
            self.recompute_button.state(['disabled'])
            self.status.set('正在复算原时点；不更新当前牌况标题，不覆盖原结果。')
        except Exception as error:
            self.status.set('不能复算：'+str(error))

    def poll(self):
        if self._closed:
            return
        if getattr(self.app,'_closing',False):
            self.poll_id=self.after(100,self.poll);return
        try:
            self.poll_loading()
            result = self.service.poll()
            if result is not None and self.attempt:
                attempt, self.attempt = self.attempt, None
                try:
                    if result.get('request_id') != attempt['request_id']:
                        raise ValueError('原时点复算请求身份不符')
                    if result['status']=='available':
                        validate_opening_result(result,attempt['snapshot'])
                    else:
                        result = terminal_opening_result(attempt['snapshot'],attempt['request_id'],
                            result.get('status','failed'),result.get('reason','复算未完成'),
                            elapsed_seconds=result.get('elapsed_seconds',0))
                except Exception as error:
                    result = terminal_opening_result(attempt['snapshot'],attempt['request_id'],'failed',str(error))
                saved = self.owner.persist(result,attempt)
                self.reload(saved['snapshot_id'] if saved else None)
                self.status.set('已另存复算结果；原结果保留。' if saved else '复算完成、未保存；可重试保存。')
        finally:
            if not self._closed:
                self.poll_id = self.after(100,self.poll)

    def cancel(self):
        self.service.cancel()
        if self.attempt:
            attempt, self.attempt = self.attempt, None
            saved = self.owner.persist(terminal_opening_result(attempt['snapshot'],attempt['request_id'],
                'cancelled','已取消历史开局复算',elapsed_seconds=perf_counter()-attempt['started']),attempt)
            if not self._closed:
                self.reload(saved['snapshot_id'] if saved else None)
                self.status.set('已取消复算。')

    def close(self):
        if self._closed:
            if not self.winfo_exists():return
            if ((self._close_thread is None or not self._close_thread.is_alive()) and not any(t.is_alive() for t in self._load_threads)):
                self.after_cancel(self.poll_id);self._finish_close()
            return
        self._closed = True
        self._load_cancel.set()
        self.worker.close()
        self.after_cancel(self.poll_id)
        record=self.owner.queue_exit_cancellation(self)
        self.status.set('正在结束后台任务；保存完成后关闭，失败结果保留在应用中。')
        self.recompute_button.state(['disabled'])
        if record is None and self.service.active is None and not any(t.is_alive() for t in self._load_threads):
            self.entries=[];self.destroy();return
        def finish_work():
            self.service.close()
            if record:self.owner.write_pending_record(record)
        self._close_thread=threading.Thread(target=finish_work,daemon=True,name='opening-history-close')
        self._close_thread.start()
        self._finish_close()

    def _finish_close(self):
        if (self._close_thread is not None and self._close_thread.is_alive()) or any(thread.is_alive() for thread in self._load_threads):
            self.poll_id=self.after(25,self._finish_close);return
        self.after_cancel(self.poll_id)
        self.entries = []
        while True:
            try:
                self._load_queue.get_nowait()
            except Empty:
                break
        self.destroy()
