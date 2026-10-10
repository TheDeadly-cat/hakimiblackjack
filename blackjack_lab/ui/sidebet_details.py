"""Readable side-bet details and explicit paytable editing, without ledger writes."""
from dataclasses import replace
import tkinter as tk
from tkinter import ttk

from ..analysis.sidebets.contracts import SidebetProfile,CATEGORIES,NAMES
from .table_modes import MODE_LABELS

CONFIRM={'自建研究':'research','赔付未核对':'unconfirmed','真实桌规已核对':'verified'}
CONVENTION={'净赢倍数（25:1填25）':'net_profit','含本金返还（26倍填26）':'gross_return'}
ACE_CHOICES={'待确认':None,'是':True,'否':False}


def result_text(result):
    snapshot=result['input'];profile=SidebetProfile.from_dict(snapshot['profile'])
    lines=[f"{snapshot['seat']} · 第{snapshot['window_round_no']}轮 · "+
        ('发牌前概率' if snapshot['purpose']=='forecast' else '后来信息研究，非原预测' if snapshot['purpose']=='corrected_predeal' else '已封盘原始牌型'),
        snapshot['timing_note'],f"原规则：{profile.source}；{profile.confirmation}",
        '边注各自以1单位计，不与主注动作EV或顶部固定策略开局EV混算。',
        f"事件前缀 #{snapshot['through_seq']} · {snapshot['prefix_digest']}"]
    for name,bet in result['output']['bets'].items():
        lines+=['',name]
        if bet['status']!='available':lines.append(bet['reason']);continue
        if snapshot['purpose'] in ('forecast','corrected_predeal'):
            lines.extend(f"{NAMES[k]}：{p:.6%}（{bet['fractions'][k]}）" for k,p in bet['probabilities'].items())
            lines.append(f"总命中 {bet['hit_probability']:.6%}；"+
                         ('赔付未核对，EV为空' if bet['ev'] is None else f"{'研究' if bet['ev_scope']=='research' else ''}净EV {bet['ev']:+.8f}/1"))
        else:
            lines.append('已揭晓：'+NAMES[bet['category']]+'；这是已记录结果，不是下注前概率。')
            lines.append('原始牌事件：'+'、'.join(bet['card_ids']))
    lines+=['',f"赔付口径：{profile.payout_convention}",f"PP赔付：{profile.perfect_pairs}",
            f"21+3赔付：{profile.twenty_one_plus_three}",
            f"A23={profile.a23}，QKA={profile.qka}，KA2={profile.ka2}；只付配置优先级最高一档。",
            '52类剩余组成：'+str(snapshot['composition']['counts']),
            '数据缺项：'+str(snapshot['composition']['missing'])]
    return '\n'.join(lines)


class SidebetDetails(tk.Toplevel):
    def __init__(self,owner):
        super().__init__(owner.app);self.owner=owner
        self.title('边注奖级、研究规则与独立历史');self.geometry('760x670');self.transient(owner.app)
        tabs=ttk.Notebook(self);tabs.pack(fill=tk.BOTH,expand=True,padx=8,pady=8)
        details=ttk.Frame(tabs);settings=ttk.Frame(tabs)
        tabs.add(details,text='当前 / 封盘记录');tabs.add(settings,text='赔付与牌型规则')
        self.text=tk.Text(details,wrap=tk.WORD,state=tk.DISABLED);self.text.pack(fill=tk.BOTH,expand=True)
        controls=ttk.Frame(self);controls.pack(pady=6)
        ttk.Button(controls,text='边注历史 / 原时点复算',command=owner.show_history).pack(side=tk.LEFT,padx=4)
        ttk.Button(controls,text='重试未保存记录',command=owner.retry_saves).pack(side=tk.LEFT,padx=4)
        self.error=tk.StringVar()
        self.profile_scope=tk.StringVar()
        ttk.Label(settings,textvariable=self.profile_scope,wraplength=700).grid(row=0,column=0,columnspan=3,sticky='w',pady=6)
        self.status=tk.StringVar();self.convention=tk.StringVar();self.source=tk.StringVar()
        for row,label,variable,values in ((1,'确认状态',self.status,tuple(CONFIRM)),(2,'赔付口径',self.convention,tuple(CONVENTION))):
            ttk.Label(settings,text=label).grid(row=row,column=0,sticky='w')
            ttk.Combobox(settings,textvariable=variable,values=values,state='readonly',width=28).grid(row=row,column=1,columnspan=2,sticky='w')
        ttk.Label(settings,text='规则来源').grid(row=3,column=0,sticky='w')
        ttk.Entry(settings,textvariable=self.source,width=52).grid(row=3,column=1,columnspan=2,sticky='w')
        self.payouts={};row=4
        for name,categories in CATEGORIES.items():
            for kind in categories:
                variable=tk.StringVar();self.payouts[name,kind]=variable
                ttk.Label(settings,text=name+' '+NAMES[kind]).grid(row=row,column=0,sticky='w',pady=2)
                ttk.Entry(settings,textvariable=variable,width=12).grid(row=row,column=1,sticky='w')
                row+=1
        self.aces={};self.ace_controls={}
        ace_area=ttk.Frame(settings);ace_area.grid(row=row,column=0,columnspan=3,sticky='w',pady=5);row+=1
        for field in ('a23','qka','ka2'):
            group=ttk.Frame(ace_area);group.pack(side=tk.LEFT,padx=4)
            ttk.Label(group,text=field.upper()+'算顺子').pack(side=tk.LEFT)
            var=tk.StringVar();self.aces[field]=var
            control=ttk.Combobox(group,textvariable=var,values=tuple(ACE_CHOICES),state='readonly',width=7)
            control.pack(side=tk.LEFT,padx=2);self.ace_controls[field]=control
        self.priority=[]
        ttk.Label(settings,text='21+3获奖优先顺序（从高到低）').grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        priority=ttk.Frame(settings);priority.grid(row=row,column=0,columnspan=3,sticky='w');row+=1
        for _ in CATEGORIES['21+3']:
            var=tk.StringVar();self.priority.append(var)
            ttk.Combobox(priority,textvariable=var,values=[NAMES[k] for k in CATEGORIES['21+3']],state='readonly',width=9).pack(side=tk.LEFT,padx=2)
        buttons=ttk.Frame(settings);buttons.grid(row=row,column=0,columnspan=3,sticky='w',pady=8);row+=1
        self.save_button=ttk.Button(buttons,text='保存配置',command=self.apply)
        self.save_button.pack(side=tk.LEFT,padx=4)
        self.research_button=ttk.Button(buttons,text='采用自建研究示例',command=self.research)
        self.research_button.pack(side=tk.LEFT,padx=4)
        if not owner.research_allowed:
            self.save_button.state(['disabled']);self.research_button.state(['disabled'])
        ttk.Label(settings,textvariable=self.error,wraplength=690,foreground='#AD3030').grid(row=row,column=0,columnspan=3,sticky='w')
        self.load_profile();self.last_text=None;self.render()

    def load_profile(self):
        p=self.owner.profile
        self.loaded_profile=(self.owner._profile_mode,p.rules_digest)
        self.profile_scope.set(MODE_LABELS.get(self.owner._profile_mode,'当前') +
            ' · 此模式独立设置。修改影响新预测；已封盘原记录保留原赔付，未知不会自动改为是或否。')
        self.status.set(next(k for k,v in CONFIRM.items() if v==p.confirmation))
        self.convention.set(next(k for k,v in CONVENTION.items() if v==p.payout_convention))
        self.source.set(p.source)
        for name,values in (('perfect_pairs',p.perfect_pairs),('21+3',p.twenty_one_plus_three)):
            for i,kind in enumerate(CATEGORIES[name]):self.payouts[name,kind].set('' if values is None else str(values[i]))
        for field,var in self.aces.items():
            var.set(next(label for label,value in ACE_CHOICES.items() if value is getattr(p,field)))
        for var,kind in zip(self.priority,p.three_priority):var.set(NAMES[kind])

    def apply(self):
        try:
            if not self.owner.research_allowed:
                raise ValueError('点值版保留原配置，只在独立研究入口修改')
            if self.loaded_profile!=(self.owner._profile_mode,self.owner.profile.rules_digest):
                raise ValueError('当前模式或边注配置已变更，请重新载入后再保存；未覆盖其他模式')
            confirm=CONFIRM[self.status.get()];source=self.source.get().strip()
            if confirm=='verified' and source==SidebetProfile().source:
                raise ValueError('请填写实际已核对的桌内规则来源；研究示例不是实际确认')
            payouts=[]
            for name in CATEGORIES:
                values=tuple(self.payouts[name,k].get().strip() for k in CATEGORIES[name])
                payouts.append(None if all(not v for v in values) else values)
            by_name={v:k for k,v in NAMES.items()}
            profile=replace(self.owner.profile,version=self.owner.profile.version+1,confirmation=confirm,
                source=source,payout_convention=CONVENTION[self.convention.get()],
                perfect_pairs=payouts[0],twenty_one_plus_three=payouts[1],
                three_priority=tuple(by_name[v.get()] for v in self.priority),
                **{k:ACE_CHOICES[v.get()] for k,v in self.aces.items()})
            self.owner.apply_profile(profile);self.error.set('已保存；已封盘记录及原赔付保持不变。')
        except Exception as error:self.error.set(str(error))

    def research(self):
        try:self.owner.apply_profile(SidebetProfile());self.load_profile();self.error.set('已采用自建研究示例，未确认真实桌规。')
        except Exception as error:self.error.set(str(error))

    def render(self):
        owner=self.owner;seat=owner.app.var_analysis_target.get()
        lines=['边注预测与已发生牌型分开；主注最佳/次佳不受本窗口设置影响。']
        forecast=owner.forecasts.get(seat)
        pause=owner.app.recording_advice_pause()
        if not owner.research_allowed:
            lines.extend(['','牌型边注已停用；这里仅查看原配置和历史。',
                          '已有预测与尚未保存结果保留，当前不派发Perfect Pairs或21+3分析。'])
        elif pause:
            lines.extend(['',pause,'此前记录保留，当前暂停使用。保存回执与当前事件前缀核对一致后再恢复输出。',
                          '原预测、已记录牌型和独立历史没有清空；可从下方历史入口查看原时点记录。'])
        elif not owner.live_enabled():
            lines.extend(['','牌型边注已停用；此前记录和未保存结果保留，当前不作实时预测。'])
        elif forecast:
            lines+=['\n'+('本轮发牌前记录（已封盘）' if owner.sealed else '发牌前预测'),result_text(forecast['result'])]
        else:lines.append('\n暂无当前已加载的发牌前记录；历史结果请使用下方独立入口。')
        if owner.observed and owner.live_enabled() and not pause:lines+=['\n已记录牌型',result_text(owner.observed['result'])]
        with owner._lock:pending=len(owner.pending_saves)
        if pending:lines.append(f'\n{pending}条已计算、未保存；退出前可重试保存原请求。')
        text='\n'.join(lines)
        if text==self.last_text:return
        self.last_text=text;self.text.configure(state=tk.NORMAL);self.text.delete('1.0',tk.END)
        self.text.insert('1.0',text);self.text.configure(state=tk.DISABLED)
