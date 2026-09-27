"""Compact independent side-bet results. No card transactions or Tk calls in workers."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import threading
from time import time
import tkinter as tk
from tkinter import ttk
import uuid

from ..analysis.contracts import canonical,digest
from ..analysis.sidebets.contracts import SidebetProfile,CATEGORIES,NAMES
from ..analysis.sidebets.background import LatestWorker
from ..analysis.sidebets.information import build_input,execute,window,dealing_started
from ..ledger.ledger import EventLedger
from ..storage.safe_files import atomic_write
from ..storage.sidebet_snapshots import algorithm_manifest


class SidebetView:
    def __init__(self,app,parent):
        self.app=app
        self.store=app.ctrl.sidebet_store
        self.worker=LatestWorker()
        self._lock=threading.Lock()
        self.pending_saves={}
        self.saved_ids={}
        self.closed=False
        self.history=self.details=None
        self.profile=SidebetProfile()
        self.profile_error=''
        self.settings=Path(str(app.ctrl.store.db_path)+'.sidebet-profile.json')
        self.enabled=tk.BooleanVar(value=app.auto_analysis)
        if self.settings.exists():
            try:
                data=json.loads(self.settings.read_text(encoding='utf-8'))
                if set(data)!= {'schema','profile','enabled'} or type(data['schema']) is not int or data['schema']!=1 or type(data['enabled']) is not bool:
                    raise ValueError('设置格式无效')
                self.profile=SidebetProfile.from_dict(data['profile'])
                self.enabled.set(data['enabled'] and app.auto_analysis)
            except Exception as error:
                self.profile=replace(self.profile,confirmation='unconfirmed')
                self.profile_error='边注设置需核对：'+str(error)
        self.frame=ttk.Frame(parent);self.frame.pack(fill=tk.X,pady=(3,0))
        self.frame.columnconfigure(0,weight=1)
        self.lines={name:tk.StringVar(value='') for name in CATEGORIES}
        for row,(name,var) in enumerate(self.lines.items()):
            ttk.Label(self.frame,textvariable=var,wraplength=620).grid(row=row,column=0,columnspan=3,sticky='w')
        controls=ttk.Frame(self.frame);controls.grid(row=2,column=0,columnspan=3,sticky='ew')
        ttk.Checkbutton(controls,text='边注研究',variable=self.enabled,command=self.change_enabled).pack(side=tk.LEFT)
        ttk.Button(controls,text='奖级 / 规则 / 历史',command=self.show_details).pack(side=tk.LEFT,padx=5)
        self.pending_label=tk.StringVar()
        ttk.Label(controls,textvariable=self.pending_label).pack(side=tk.LEFT)
        self.key=None;self.current_window=None;self.intents={};self.forecasts={}
        self.observed=None;self.observed_request=None;self.sealed=False;self.problem=''
        self.trace=app.var_analysis_target.trace_add('write',lambda *_:self.refresh())
        app.ctrl.add_context_listener(self.refresh)
        self.refresh();self.poll_id=app.after(60,self.poll)

    def apply_profile(self,profile):
        value=dict(schema=1,profile=profile.to_dict(),enabled=self.enabled.get())
        atomic_write(self.settings,canonical(value).encode('utf-8'))
        self.profile=profile;self.profile_error='';self.key=None;self.refresh()

    def change_enabled(self):
        try:self.apply_profile(self.profile)
        except Exception as error:self.profile_error='显示设置未保存：'+str(error)
        self.key=None;self.refresh()

    def save_record(self,record):
        """Worker-only. Failed writes retain the exact original request for explicit retry."""
        request=record['result']['request_id']
        preserved=copy.deepcopy(record)
        with self._lock:self.pending_saves[request]=preserved
        try:
            saved=self.store.save(**record)
            with self._lock:
                self.pending_saves.pop(request,None)
                self.saved_ids[request]=saved['snapshot_id']
            return saved,None
        except Exception as error:return None,str(error)

    def retry_saves(self):
        def work():
            with self._lock:records=list(self.pending_saves.values())
            for record in records:
                if self.worker.closed.is_set():break
                self.save_record(record)
            return None
        self.worker.submit('retry',work)

    def _request(self,purpose,profile,intent=None):
        app=self.app;prefix=app.ctrl.ledger.to_list();session=app.ctrl.session_id
        seat=app.var_analysis_target.get();request_id=uuid.uuid4().hex
        captured_at=time()
        meta=dict(window=self.current_window,seat=seat,profile=profile.to_dict(),request_id=request_id,
                  context=app.ctrl.context_token)
        prediction_id=None
        if intent:
            with self._lock:prediction_id=self.saved_ids.get(intent['request_id'])
        def work():
            ledger=EventLedger.from_list(session,prefix)
            snapshot=build_input(ledger,seat,profile,purpose)
            result=execute(snapshot,request_id)
            if self.worker.closed.is_set():return dict(cancelled=True)
            record=dict(result=result,event_prefix=prefix,timing='captured_predeal' if purpose=='forecast' else 'observed',
                        prediction_id=prediction_id,sources=algorithm_manifest(),captured_at=captured_at)
            saved,error=self.save_record(record)
            return dict(meta=meta,result=result,saved_id=saved['snapshot_id'] if saved else None,save_error=error)
        self.worker.submit(purpose,work,request_id)
        return meta

    def refresh(self):
        if self.closed:return
        key=(self.app.ctrl.context_token,self.app.var_analysis_target.get(),self.profile.rules_digest,self.enabled.get())
        if self.key==key:return
        self.key=key;self.problem=''
        with self.app._view_frame():current=self.app._current_seg()
        identity=(self.app.ctrl.session_id,*(window(current) or (None,None)))
        if identity!=self.current_window:
            self.current_window=identity;self.intents.clear();self.forecasts.clear();self.observed=None
        self.sealed=dealing_started(current)
        self.observed=None;self.observed_request=None
        if not self.enabled.get():self.problem='边注研究已关闭';self.render();return
        if window(current) is None:self.problem='尚无进行中的牌靴';self.render();return
        seat=self.app.var_analysis_target.get()
        if self.sealed:
            intent=self.intents.get(seat)
            profile=SidebetProfile.from_dict(intent['profile']) if intent else self.profile
            self.observed_request=self._request('observed',profile,intent)['request_id']
        else:
            self.forecasts.pop(seat,None)
            self.intents[seat]=self._request('forecast',self.profile)
        self.render()

    def poll(self):
        if self.closed:return
        for message in self.worker.poll():
            if message['channel']=='retry':continue
            if 'error' in message:
                seat=self.app.var_analysis_target.get();intent=self.intents.get(seat,{})
                if message['request_id'] in (self.observed_request,intent.get('request_id')):
                    self.problem='边注需核对：'+message['error']
                continue
            value=message['result']
            if not value or value.get('cancelled'):continue
            meta=value['meta'];seat=meta['seat']
            if meta['window']!=self.current_window:continue
            if message['channel']=='forecast' and self.intents.get(seat,{}).get('request_id')==message['request_id']:
                if not self.sealed and value['result']['input']['prefix_digest']!=digest(self.app.ctrl.ledger.to_list()):
                    self.key=None;continue
                self.forecasts[seat]=value
            elif message['channel']=='observed' and message['request_id']==self.observed_request:
                if meta['context']==self.app.ctrl.context_token and seat==self.app.var_analysis_target.get():
                    if value['result']['input']['prefix_digest']==digest(self.app.ctrl.ledger.to_list()):
                        self.observed=value
                    else:self.key=None
        self.refresh();self.render()
        self.poll_id=self.app.after(60,self.poll)

    def render(self):
        seat=self.app.var_analysis_target.get();forecast=self.forecasts.get(seat)
        for name,var in self.lines.items():
            title='Perfect Pairs' if name=='perfect_pairs' else '21+3'
            text=self.profile_error or self.problem
            if not text:
                observed=self.observed['result']['output']['bets'][name] if self.observed else None
                prediction=forecast['result']['output']['bets'][name] if forecast else None
                if self.sealed and observed and observed['status']=='available':
                    net=observed['net_units']
                    text='已揭晓 '+NAMES[observed['category']]
                    text+=(' · 赔付未核对' if net is None else f" · {'研究' if observed['ev_scope']=='research' else ''}净收益 {net:+g}/1")
                    text+=' · 已封盘'
                elif prediction and prediction['status']=='available':
                    ev=prediction['ev']
                    text=f"命中 {prediction['hit_probability']:.3%} · "
                    text+=('EV —（赔付未核对）' if ev is None else f"{'研究' if prediction['ev_scope']=='research' else ''}EV {ev:+.5f}/1")
                    text+=' · 发牌前记录／已封盘' if self.sealed else ' · 发牌前'
                elif prediction:
                    text=prediction['reason']+(' · 已封盘' if self.sealed else '')
                elif self.sealed:
                    text='已封盘 · '+(observed['reason'] if observed else '正在核对原始牌')
                    if seat not in self.intents:text+='；发牌前记录请查历史'
                else:text='发牌前计算中'
            var.set(f'{title} · {seat}：{text}')
        with self._lock:pending=len(self.pending_saves)
        self.pending_label.set(f'{pending}条已计算、未保存' if pending else '')
        if self.details is not None and self.details.winfo_exists():self.details.render()

    def show_details(self):
        from .sidebet_details import SidebetDetails
        if self.details is not None and self.details.winfo_exists():self.details.lift()
        else:self.details=SidebetDetails(self)

    def show_history(self):
        from .sidebet_history import SidebetHistory
        if self.history is not None and self.history.winfo_exists():self.history.lift()
        else:self.history=SidebetHistory(self)

    def close(self):
        if self.closed:return
        self.closed=True;self.app.after_cancel(self.poll_id)
        self.app.ctrl.remove_context_listener(self.refresh)
        self.app.var_analysis_target.trace_remove('write',self.trace)
        if self.history is not None and self.history.winfo_exists():self.history.close()
        self.worker.close()
        if self.details is not None and self.details.winfo_exists():self.details.destroy()
