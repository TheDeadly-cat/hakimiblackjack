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
from .observation_identity import observed_identity
from .table_modes import BCLC, PRAGMATIC, mode_for_rules


class SidebetView:
    def __init__(self,app,parent):
        self.app=app
        self.research_allowed=app.sidebet_research
        self.store=app.ctrl.sidebet_store
        self.worker=LatestWorker()
        self._lock=threading.Lock()
        self.pending_saves={}
        self.saved_ids={}
        self.closed=False
        self.history=self.details=None
        self.profile=SidebetProfile()
        self.profile_error=''
        self._profile_mode=PRAGMATIC
        self.settings=Path(str(app.ctrl.store.db_path)+'.sidebet-profile.json')
        self.enabled=tk.BooleanVar(value=app.auto_analysis and self.research_allowed)
        if self.settings.exists():
            try:
                data=json.loads(self.settings.read_text(encoding='utf-8'))
                if set(data)!= {'schema','profile','enabled'} or type(data['schema']) is not int or data['schema']!=1 or type(data['enabled']) is not bool:
                    raise ValueError('设置格式无效')
                self.profile=SidebetProfile.from_dict(data['profile'])
                self.enabled.set(data['enabled'] and app.auto_analysis and self.research_allowed)
            except Exception as error:
                self.profile=replace(self.profile,confirmation='unconfirmed')
                self.profile_error='边注设置需核对：'+str(error)
        self.frame=ttk.Frame(parent);self.frame.pack(fill=tk.X,pady=(3,0))
        self.frame.columnconfigure(0,weight=1)
        self.lines={name:tk.StringVar(value='') for name in CATEGORIES}
        for row,(name,var) in enumerate(self.lines.items()):
            ttk.Label(self.frame,textvariable=var,wraplength=620).grid(row=row,column=0,columnspan=3,sticky='w')
        controls=ttk.Frame(self.frame);controls.grid(row=2,column=0,columnspan=3,sticky='ew')
        self.toggle=ttk.Checkbutton(controls,text='边注研究',variable=self.enabled,command=self.change_enabled)
        self.toggle.pack(side=tk.LEFT)
        if not self.research_allowed:self.toggle.state(['disabled'])
        ttk.Button(controls,text='边注历史 / 原配置',command=self.show_details).pack(side=tk.LEFT,padx=5)
        self.pending_label=tk.StringVar()
        ttk.Label(controls,textvariable=self.pending_label).pack(side=tk.LEFT)
        self.key=None;self.current_window=None;self.intents={};self.forecasts={}
        self.observed=None;self.observed_request=None;self.observation_key=None;self.sealed=False;self.problem=''
        self.recording_refresh_id=None
        self.poll_id=None;self._publication_paused=False
        self.trace=app.var_analysis_target.trace_add('write',lambda *_:self.refresh())
        self.mode_trace=app.var_table_mode.trace_add('write',lambda *_:self.refresh())
        app.ctrl.add_context_listener(self.refresh)
        # Configuration belongs to the locked shoe even while recovery faults
        # keep numerical publication paused. No calculation or ledger write.
        with app._view_frame():
            self._select_table_profile(app._current_seg())
        self.refresh()

    def live_enabled(self):
        return self.research_allowed and self.enabled.get()

    def _light_key(self):
        app=self.app;plan=app.ctrl.entry_plan
        return (app.ctrl.context_token,app.var_table_mode.get(),app.var_analysis_target.get(),
                self.profile.rules_digest,self.live_enabled(),app.recording_busy,bool(app._recording_faults),
                (plan.mode,plan.ledger_seq,plan.input_paused) if plan else None)

    def _arm_poll(self):
        needed=(not self.closed and not getattr(self.app,'_closing',False)
                and (self.live_enabled() or self.worker.busy))
        if not needed and self.poll_id is not None:
            self.app.after_cancel(self.poll_id);self.poll_id=None
        elif needed and self.poll_id is None:
            self.poll_id=self.app.after(60,self.poll)

    def apply_profile(self,profile):
        if not self.research_allowed:
            raise ValueError('点值版仅保留边注历史与原配置，请在独立研究入口修改')
        value=dict(schema=1,profile=profile.to_dict(),enabled=self.enabled.get())
        atomic_write(self.settings,canonical(value).encode('utf-8'))
        changed = profile.rules_digest != self.profile.rules_digest
        self.profile=profile;self.profile_error='';self.key=None
        if changed and self.details is not None and self.details.winfo_exists():
            self.details.load_profile()
        self.refresh()

    def change_enabled(self):
        if not self.research_allowed:
            self.enabled.set(False);self.key=None;self.refresh();return
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
        self.worker.submit('retry',work);self._arm_poll()

    def _request(self,purpose,profile,intent=None,observation_key=None):
        if not self.live_enabled():return None
        app=self.app;frozen=app.ctrl.read_prefix();session=frozen.session_id
        seat=app.var_analysis_target.get();request_id=uuid.uuid4().hex
        captured_at=time()
        meta=dict(window=self.current_window,seat=seat,profile=profile.to_dict(),request_id=request_id,
                  context=app.ctrl.context_token,observation_key=observation_key)
        prediction_id=None
        if intent:
            with self._lock:prediction_id=self.saved_ids.get(intent['request_id'])
        def work():
            prefix=frozen.to_list()
            ledger=EventLedger.from_list(session,prefix)
            snapshot=build_input(ledger,seat,profile,purpose)
            result=execute(snapshot,request_id)
            if self.worker.closed.is_set():return dict(cancelled=True)
            record=dict(result=result,event_prefix=prefix,timing='captured_predeal' if purpose=='forecast' else 'observed',
                        prediction_id=prediction_id,sources=algorithm_manifest(),captured_at=captured_at)
            saved,error=self.save_record(record)
            return dict(meta=meta,result=result,saved_id=saved['snapshot_id'] if saved else None,save_error=error)
        self.worker.submit(purpose,work,request_id)
        self._arm_poll()
        return meta

    def refresh(self):
        if self.closed or getattr(self.app,'_closing',False):return
        if not self.research_allowed and self.enabled.get():self.enabled.set(False)
        key=self._light_key()
        if self.key==key:
            self._arm_poll();return
        self._publication_paused=bool(self.app.recording_advice_pause())
        if self._publication_paused:
            self.key=key;self.pause_recording();self._arm_poll();return
        if self.app._coalescing_recording_views:
            if self.recording_refresh_id is None:
                self.recording_refresh_id=self.app.after(50,self._after_recording)
            return
        with self.app._view_frame():current=self.app._current_seg()
        self._select_table_profile(current)
        self.key=self._light_key();self.problem=''
        if not self.live_enabled():
            self.problem='牌型边注已停用；历史、原配置及未保存结果保留'
            self.render();self._arm_poll();return
        identity=(self.app.ctrl.session_id,*(window(current) or (None,None)))
        if identity!=self.current_window:
            self.current_window=identity;self.intents.clear();self.forecasts.clear();self.observed=None
        self.sealed=dealing_started(current)
        if window(current) is None:
            self.observed=None;self.observed_request=None;self.observation_key=None
            self.problem='尚无进行中的牌靴';self.render();self._arm_poll();return
        seat=self.app.var_analysis_target.get()
        if self.sealed:
            intent=self.intents.get(seat)
            profile=SidebetProfile.from_dict(intent['profile']) if intent else self.profile
            observation_key=observed_identity(self.app.ctrl.ledger,current,seat,profile)
            if observation_key!=self.observation_key:
                self.observed=None;self.observation_key=observation_key
                self.observed_request=self._request('observed',profile,intent,observation_key)['request_id']
        else:
            self.observed=None;self.observed_request=None;self.observation_key=None
            self.forecasts.pop(seat,None)
            self.intents[seat]=self._request('forecast',self.profile)
        self.render();self._arm_poll()

    def _after_recording(self):
        self.recording_refresh_id=None
        self.refresh()

    def _select_table_profile(self, current):
        mode = mode_for_rules(current.rules) if current else self.app.var_table_mode.get()
        mode = mode or PRAGMATIC
        if mode == self._profile_mode:
            return
        self._profile_mode = mode
        suffix = '.bclc-sidebet-profile.json' if mode == BCLC else '.sidebet-profile.json'
        self.settings = Path(str(self.app.ctrl.store.db_path) + suffix)
        default = (SidebetProfile(profile_id='bclc-playnow-sidebets-unconfirmed-v2', version=2,
                                  source='用户提供手册1.1已列Perfect Pairs/21+3奖级；赔付和A顺子细则待确认',
                                  confirmation='unconfirmed', perfect_pairs=None,
                                  twenty_one_plus_three=None, a23=None, qka=None, ka2=None) if mode == BCLC else SidebetProfile())
        self.profile, self.profile_error = default, ''
        if self.settings.exists():
            try:
                data = json.loads(self.settings.read_text(encoding='utf-8'))
                if set(data) != {'schema', 'profile', 'enabled'} or type(data['schema']) is not int or data['schema'] != 1 or type(data['enabled']) is not bool:
                    raise ValueError('设置格式无效')
                self.profile = SidebetProfile.from_dict(data['profile'])
                if (mode == BCLC and self.profile.profile_id == 'bclc-playnow-sidebets-unconfirmed-v1'
                        and self.profile.version == 1 and self.profile.confirmation == 'unconfirmed'
                        and self.profile.perfect_pairs is None and self.profile.twenty_one_plus_three is None
                        and self.profile.source == 'BCLC视频中的边注；奖级和赔付待确认，不沿用Pragmatic研究表'):
                    self.profile = default
                self.enabled.set(data['enabled'] and self.app.auto_analysis and self.research_allowed)
            except Exception as error:
                self.profile = replace(default, confirmation='unconfirmed')
                self.profile_error = '边注设置需核对：' + str(error)
        if self.details is not None and self.details.winfo_exists():
            self.details.load_profile()

    def poll(self):
        if self.closed:return
        if self.poll_id is not None:self.app.after_cancel(self.poll_id)
        self.poll_id=None
        if getattr(self.app,'_closing',False):
            return
        self.refresh()
        if self._publication_paused and self.live_enabled():
            self._arm_poll();return
        messages=self.worker.poll()
        for message in messages:
            if message['channel']=='retry':continue
            # Work already accepted before disabling has saved its original
            # record (or retained it in pending_saves). No current publication
            # or current-prefix validation is needed while this module is off.
            if not self.live_enabled():continue
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
                if not self.sealed and value['result']['input']['prefix_digest']!=self.app.ctrl.read_prefix().prefix_digest:
                    self.key=None;continue
                self.forecasts[seat]=value
            elif message['channel']=='observed' and message['request_id']==self.observed_request:
                if seat==self.app.var_analysis_target.get():
                    with self.app._view_frame():current=self.app._current_seg()
                    profile=SidebetProfile.from_dict(meta['profile'])
                    fresh=observed_identity(self.app.ctrl.ledger,current,seat,profile) if current else None
                    if meta['observation_key']==fresh==self.observation_key:self.observed=value
                    else:self.key=None
        self.refresh()
        if messages:self.render()
        self._arm_poll()

    def render(self):
        if self.research_allowed and self.app.recording_advice_pause():
            self.pause_recording();return
        if not self.live_enabled():
            for var in self.lines.values():var.set('牌型边注已停用；历史、原配置及未保存结果保留')
            with self._lock:pending=len(self.pending_saves)
            self.pending_label.set(f'{pending}条已计算、未保存' if pending else '')
            if self.details is not None and self.details.winfo_exists():self.details.render()
            return
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

    def pause_recording(self):
        if not self.research_allowed:
            self.render();return
        message = self.app.recording_advice_pause()
        if not message:
            return
        seat = self.app.var_analysis_target.get()
        for name, var in self.lines.items():
            title = 'Perfect Pairs' if name == 'perfect_pairs' else '21+3'
            text = f'{title} · {seat}：{message}；此前记录保留，当前暂停使用'
            if var.get() != text:
                var.set(text)
        with self._lock:
            pending = len(self.pending_saves)
        self.pending_label.set(f'{pending}条已计算、未保存' if pending else '')
        if self.details is not None and self.details.winfo_exists():
            self.details.render()

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
        self.closed=True
        if self.poll_id is not None:self.app.after_cancel(self.poll_id);self.poll_id=None
        if self.recording_refresh_id is not None:
            self.app.after_cancel(self.recording_refresh_id)
            self.recording_refresh_id=None
        self.app.ctrl.remove_context_listener(self.refresh)
        self.app.var_analysis_target.trace_remove('write',self.trace)
        self.app.var_table_mode.trace_remove('write',self.mode_trace)
        if self.history is not None and self.history.winfo_exists():self.history.close()
        self.worker.close()
        if self.details is not None and self.details.winfo_exists():self.details.destroy()
