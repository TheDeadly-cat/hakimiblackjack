"""Read-only history and original-prefix recomputation on a separate bounded worker."""
from datetime import datetime
import tkinter as tk
from tkinter import ttk

from ..analysis.sidebets.background import LatestWorker
from ..analysis.sidebets.information import execute
from ..storage.sidebet_snapshots import algorithm_manifest
from .sidebet_details import result_text


class SidebetHistory(tk.Toplevel):
    def __init__(self,owner):
        super().__init__(owner.app);self.owner=owner;self.worker=LatestWorker()
        self.title('边注历史（原结果只读，复算另存）');self.geometry('820x670');self.transient(owner.app)
        self.closed=False;self.entries=[];self.loading=False;self.verified=None;self.recomputing=False
        self.expected={}
        self.selected_after_load=None
        self.damage_note=''
        self.listing=tk.Listbox(self,height=7,exportselection=False);self.listing.pack(fill=tk.X,padx=8,pady=6)
        self.listing.bind('<<ListboxSelect>>',self.select)
        self.text=tk.Text(self,wrap=tk.WORD,state=tk.DISABLED);self.text.pack(fill=tk.BOTH,expand=True,padx=8)
        self.status=tk.StringVar();ttk.Label(self,textvariable=self.status,wraplength=780).pack(fill=tk.X,padx=8,pady=4)
        buttons=ttk.Frame(self);buttons.pack(pady=5)
        self.recompute_button=ttk.Button(buttons,text='按原时点复算并另存',command=self.recompute)
        self.recompute_button.pack(side=tk.LEFT,padx=4)
        self.corrected_button=ttk.Button(buttons,text='采纳后来纠正 · 研究另存',command=self.corrected)
        self.corrected_button.pack(side=tk.LEFT,padx=4)
        self.corrected_button.state(['disabled'])
        ttk.Button(buttons,text='刷新列表',command=self.reload).pack(side=tk.LEFT,padx=4)
        ttk.Button(buttons,text='重试未保存记录',command=owner.retry_saves).pack(side=tk.LEFT,padx=4)
        self.protocol('WM_DELETE_WINDOW',self.close)
        self.reload();self.poll_id=self.after(70,self.poll)

    def selected(self):
        index=self.listing.curselection()
        return self.entries[index[0]] if index and index[0]<len(self.entries) else None

    def reload(self,selected_id=None):
        if self.closed or self.recomputing or getattr(self.owner.app,'_closing',False):return
        old=self.selected()
        self.selected_after_load=selected_id or (old['snapshot_id'] if old else None)
        self.expected.pop('verify',None)
        self.loading=True;self.verified=None;self.recompute_button.state(['disabled'])
        self.corrected_button.state(['disabled'])
        self.status.set('后台核验历史，可继续录牌；原记录保留。')
        store=self.owner.store
        self.expected['load']=self.worker.submit('history_load',lambda:dict(kind='load',selected_id=selected_id,
            data=store.list(cancelled=self.worker.closed.is_set)))

    def show(self,saved):
        text='历史只读 · 不代表当前牌况或仍可下注的机会\n'
        text+=f"保存 {datetime.fromtimestamp(saved['saved_at']):%Y-%m-%d %H:%M:%S} · {saved['snapshot_id']}\n"
        text+='时点：'+{'captured_predeal':'本地发牌前捕获','observed':'已封盘原始牌型','historical_recompute':'历史复算，非新机会'}[saved['timing']]+'\n'
        if saved.get('recomputed_from'):text+='复算来源：'+saved['recomputed_from']+'\n'
        text+=result_text(saved['result'])
        self.text.configure(state=tk.NORMAL);self.text.delete('1.0',tk.END);self.text.insert('1.0',text);self.text.configure(state=tk.DISABLED)

    def select(self,_event=None):
        if self.closed or self.recomputing or getattr(self.owner.app,'_closing',False):return
        if self.loading:
            saved=self.selected()
            if saved:self.selected_after_load=saved['snapshot_id']
            self.status.set('正在更新历史列表；完成后核对选中记录。')
            return
        saved=self.selected();self.verified=None;self.recompute_button.state(['disabled'])
        self.expected.pop('verify',None)
        self.corrected_button.state(['disabled'])
        if saved is None:return
        self.show(saved);self.status.set('原结果只读，正在核对原数据库前缀。')
        store=self.owner.store;db=self.owner.app.ctrl.store.db_path
        self.expected['verify']=self.worker.submit('history_verify',lambda:dict(kind='verify',snapshot_id=saved['snapshot_id'],input=store.verified_input(saved,db)))

    def recompute(self):
        if self.closed or getattr(self.owner.app,'_closing',False):return
        saved=self.selected()
        if saved is None or self.loading or self.recomputing or self.verified!=saved['snapshot_id']:return
        self.recomputing=True;self.recompute_button.state(['disabled'])
        self.corrected_button.state(['disabled'])
        self.expected.pop('verify',None)
        self.status.set('原时点研究复算；不更改当前标题，不覆盖原文件。')
        owner=self.owner;store=owner.store;db=owner.app.ctrl.store.db_path
        def work():
            snapshot=store.verified_input(saved,db)
            result=execute(snapshot)
            if self.worker.closed.is_set():return dict(kind='cancelled')
            record=dict(result=result,event_prefix=saved['event_prefix'],timing='historical_recompute',
                recomputed_from=saved['snapshot_id'],sources=algorithm_manifest())
            new,error=owner.save_record(record)
            return dict(kind='recompute',saved=new,error=error)
        self.expected['recompute']=self.worker.submit('history_recompute',work)

    def corrected(self):
        if self.closed or getattr(self.owner.app,'_closing',False):return
        saved=self.selected()
        if (saved is None or self.loading or self.recomputing or self.verified!=saved['snapshot_id']
                or saved['result']['input']['purpose'] not in ('forecast','corrected_predeal')):return
        self.recomputing=True;self.recompute_button.state(['disabled']);self.corrected_button.state(['disabled'])
        self.expected.pop('verify',None)
        self.status.set('采纳后来纠正重建原库存，仅作研究，原预测不变。')
        owner=self.owner;db=owner.app.ctrl.store.db_path
        def work():
            from ..analysis.sidebets.contracts import SidebetProfile
            from ..analysis.sidebets.research import origin_of,build_corrected_input
            ledger=owner.store.current_ledger_for(saved,db)
            original=saved['result']['input']
            snapshot=build_corrected_input(ledger,origin_of(original),SidebetProfile.from_dict(original['profile']))
            result=execute(snapshot)
            if self.worker.closed.is_set():return dict(kind='cancelled')
            new,error=owner.save_record(dict(result=result,event_prefix=ledger.to_list(),timing='historical_recompute',
                recomputed_from=saved['snapshot_id'],sources=algorithm_manifest()))
            return dict(kind='recompute',saved=new,error=error)
        self.expected['recompute']=self.worker.submit('history_recompute',work)

    def poll(self):
        if self.closed:return
        if getattr(self.owner.app,'_closing',False):
            self.poll_id=self.after(70,self.poll);return
        for message in self.worker.poll():
            expected_kind=next((kind for kind,rid in self.expected.items() if rid==message['request_id']),None)
            if expected_kind is None:continue
            if 'error' in message:
                self.expected.pop(expected_kind,None)
                if expected_kind=='load':self.loading=False
                if expected_kind=='recompute':self.recomputing=False
                self.verified=None
                self.status.set('未核验 / 未完成：'+message['error']);continue
            value=message['result'];kind=value['kind']
            if kind=='load':
                if message['request_id']!=self.expected.get('load'):continue
                self.expected.pop('load',None)
                self.loading=False;self.entries,damaged=value['data'];self.listing.delete(0,tk.END)
                for saved in self.entries:
                    snapshot=saved['result']['input']
                    self.listing.insert(tk.END,f"#{snapshot['through_seq']} · {snapshot['seat']} · {saved['timing']} · {saved['snapshot_id'][:8]}")
                if self.entries:
                    index=next((i for i,r in enumerate(self.entries) if r['snapshot_id']==self.selected_after_load),len(self.entries)-1)
                    self.listing.selection_set(index);self.select()
                else:self.status.set('尚无已保存的边注记录。')
                self.selected_after_load=None
                self.damage_note=('；损坏记录已保留：'+'；'.join(r['file']+' '+r['error'] for r in damaged)) if damaged else ''
                if damaged:self.status.set(self.status.get()+self.damage_note)
            elif kind=='verify':
                self.expected.pop('verify',None)
                selected=self.selected()
                if selected and selected['snapshot_id']==value['snapshot_id'] and not self.loading and not self.recomputing:
                    self.verified=value['snapshot_id'];self.recompute_button.state(['!disabled'])
                    self.corrected_button.state(['!disabled'] if selected['result']['input']['purpose'] in ('forecast','corrected_predeal') else ['disabled'])
                    self.status.set('原数据库完整前缀已核对；原结果只读，复算另存。'+self.damage_note)
            elif kind=='recompute':
                self.expected.pop('recompute',None)
                self.recomputing=False;saved=value['saved']
                self.reload(saved['snapshot_id'] if saved else None)
                if not saved:self.status.set('已计算、未保存：'+value['error']+'；可重试保存原请求。')
        self.poll_id=self.after(70,self.poll)

    def close(self):
        if self.closed:
            if self.worker.stopped:
                self.after_cancel(self.poll_id);self._finish_close()
            return
        self.closed=True;self.after_cancel(self.poll_id)
        if not self.recomputing:self.worker.close()
        self.status.set('正在结束后台任务；已开始的保存完成后关闭。')
        self.recompute_button.state(['disabled']);self.corrected_button.state(['disabled'])
        self._finish_close()

    def _finish_close(self):
        if self.recomputing and (self.worker.active is not None or self.worker.pending):
            self.poll_id=self.after(25,self._finish_close);return
        self.worker.close()
        if not self.worker.stopped:
            self.poll_id=self.after(25,self._finish_close);return
        self.after_cancel(self.poll_id);self.entries=[];self.destroy()
