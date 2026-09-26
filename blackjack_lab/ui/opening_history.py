"""Read-only opening history and separately saved original-prefix recalculation."""
from datetime import datetime
import json
from time import perf_counter
import tkinter as tk
from tkinter import ttk

from ..analysis.opening import NOTE
from ..analysis.opening_service import OpeningService, terminal_opening_result, validate_opening_result
from ..storage.opening_snapshots import algorithm_manifest

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

    def reload(self, selected_id=None):
        old = self.selected()
        selected_id = selected_id or (old['snapshot_id'] if old else None)
        self.entries, damaged = self.app.ctrl.opening_store.list()
        self.listing.delete(0,tk.END)
        for saved in self.entries:
            result = saved['result']
            ev = f" EV {result['ev']:+.4f}" if result['status']=='available' else ''
            stamp = datetime.fromtimestamp(saved['saved_at']).strftime('%m-%d %H:%M:%S')
            self.listing.insert(tk.END, f"{stamp} · #{result['input']['through_seq']} · {STATUS[result['status']]}{ev} · {saved['snapshot_id'][:8]}")
        if self.entries:
            index = next((i for i,e in enumerate(self.entries) if e['snapshot_id']==selected_id),len(self.entries)-1)
            self.listing.selection_set(index)
            self.select()
        else:
            self.recompute_button.state(['disabled'])
            self.status.set('尚无已保存的开局记录。')
        if damaged:
            self.status.set('损坏记录已保留，未读取：'+'；'.join(item['file']+' '+item['error'] for item in damaged))

    def select(self, _event=None):
        saved = self.selected()
        if saved is None:
            return
        try:
            self.app.ctrl.opening_store.verified_input(saved, self.app.ctrl.store.db_path)
            message = '原数据库事件前缀已核对；这是历史结果。'
            self.recompute_button.state(['disabled'] if self.attempt else ['!disabled'])
        except Exception as error:
            message = '原数据库未核验：'+str(error)
            self.recompute_button.state(['disabled'])
        self.display.configure(state=tk.NORMAL)
        self.display.delete('1.0',tk.END)
        self.display.insert('1.0',record_text(saved,message))
        self.display.configure(state=tk.DISABLED)
        self.status.set(message)

    def recompute(self):
        saved = self.selected()
        if saved is None or self.attempt:
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
        try:
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
            self.poll_id = self.after(100,self.poll)

    def cancel(self):
        self.service.cancel()
        if self.attempt:
            attempt, self.attempt = self.attempt, None
            saved = self.owner.persist(terminal_opening_result(attempt['snapshot'],attempt['request_id'],
                'cancelled','已取消历史开局复算',elapsed_seconds=perf_counter()-attempt['started']),attempt)
            self.reload(saved['snapshot_id'] if saved else None)
            self.status.set('已取消复算。')

    def close(self):
        self.after_cancel(self.poll_id)
        self.cancel()
        self.service.close()
        self.destroy()
