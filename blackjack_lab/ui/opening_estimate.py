"""Title-area pre-deal EV, with independent identity and immediate invalidation."""
import tkinter as tk
from tkinter import ttk
import copy
from time import perf_counter, sleep
import uuid

from ..analysis.contracts import InputUnavailable
from ..analysis.opening import build_opening_input, NOTE
from ..analysis.opening_service import OpeningService, validate_opening_result, terminal_opening_result
from ..storage.opening_snapshots import algorithm_manifest


def title_text(result):
    tag = {'positive': '正EV估算', 'negative': '负EV估算', 'uncertain': '正负待定'}[result['sign']]
    return (f"下轮EV估算·固定策略 {result['ev']:+.4f}/1  ·  优势率 {result['advantage_percent']:+.2f}%"
            f"  ·  95% ±{result['radius'] * 100:.2f}%  ·  {tag}")


class OpeningEstimateView:
    def __init__(self, app):
        self.app = app
        self.service = OpeningService()
        self.snapshot = self.result = self.key = None
        self.attempt = self.saved = None
        self.pending_saves = []
        self.writer = None
        self.reaper = None
        self.history = None
        self.cancelled_key = None
        self._closed = False
        self._poll_id = None
        self.detail = None
        self.detail_text = None
        self.traces = []
        self.app.ctrl.add_context_listener(self.context_changed)
        variables = [*app.var_participants.values(), app.var_my_seat, app.var_deal_direction, app.analysis_panel.auto]
        for variable in variables:
            self.traces.append((variable, variable.trace_add('write', self.context_changed)))
        self.refresh()
        self._poll_id = app.after(100, self.poll)

    def live_key(self):
        return (self.app.ctrl.context_token, tuple(k for k, v in self.app.var_participants.items() if v.get()),
                self.app.var_my_seat.get(), self.app.var_deal_direction.get(), self.app.analysis_panel.auto.get())

    def current_input(self):
        # Cheap UI eligibility guard before validating the full historical prefix.
        # The builder still performs all checks when an actual estimate is requested.
        from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS
        seg = self.app._current_seg()
        if seg and seg.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS) and any(
                h.cards for seat in [seg.table.dealer, *seg.table.players.values()] for h in seat.hands):
            raise InputUnavailable('ROUND_ACTIVE', '本轮发牌中，结束后计算')
        return build_opening_input(self.app.ctrl.ledger,
                                   tuple(k for k, v in self.app.var_participants.items() if v.get()),
                                   self.app.var_my_seat.get(), self.app.var_deal_direction.get())

    def set_text(self, text):
        self.app.var_opening_ev.set(text)
        if self.detail is not None and self.detail.winfo_exists():
            self.update_details()

    def context_changed(self, *_):
        if self._closed or getattr(self.app,'_closing',False) or self.live_key() == self.key:
            return
        self.stop_request('stale')
        self.key = self.snapshot = self.result = self.saved = None
        self.cancelled_key = None
        self.set_text('下轮EV：牌况已变化，等待更新')

    def persist(self, result, attempt):
        record=self.queue_record(result,attempt)
        if self.app.background_recording and not getattr(self.app, '_closing', False):
            self._schedule_record(record)
            return None
        return self.write_pending_record(record)

    def _schedule_record(self, record):
        from .result_writer import ResultWriter
        if self.writer is None:
            self.writer = ResultWriter(self._write_when_recording_ready)
        try:
            if not self.writer.submit(record):
                record['save_error'] = '保存任务未接收；原结果仍保留，可明确重试'
        except Exception as error:
            record['save_error'] = str(error)

    def _write_when_recording_ready(self, record):
        # Preserve the frozen original now; validate/save it after accepted card
        # input and its GUI publication finish. No Tk access on this worker.
        while self.app.recording_busy or self.app._recording_receipt_active:
            sleep(.05)
        return self.write_pending_record(record)

    def queue_record(self,result,attempt):
        from .read_snapshot import PrefixSnapshot
        prefix = attempt['prefix']
        record = dict(result=copy.deepcopy(result),
                      event_prefix=prefix if isinstance(prefix, PrefixSnapshot) else copy.deepcopy(prefix),
                      sources=attempt['sources'], recomputed_from=attempt.get('recomputed_from'))
        self.pending_saves.append(record)
        return record

    def write_pending_record(self,record):
        """No Tk calls; shutdown retries run only while views are suspended."""
        try:
            from .read_snapshot import PrefixSnapshot
            data = {k:v for k,v in record.items() if k!='save_error'}
            if isinstance(data['event_prefix'], PrefixSnapshot):
                data['event_prefix'] = data['event_prefix'].to_list()
            saved = self.app.ctrl.opening_store.save(**data)
            self.pending_saves.remove(record)
            return saved
        except Exception as error:
            record['save_error'] = str(error)
            return None

    def queue_exit_cancellation(self,source=None):
        source=source or self
        attempt,source.attempt=source.attempt,None
        if attempt is None:return None
        return self.queue_record(terminal_opening_result(attempt['snapshot'],attempt['request_id'],
            'cancelled','关闭时取消尚未完成的计算',elapsed_seconds=perf_counter()-attempt['started']),attempt)

    def retry_saves(self):
        for record in list(self.pending_saves):
            if self.app.background_recording:
                self._schedule_record(record)
                continue
            saved = self.write_pending_record(record)
            if saved and self.result and record['result']['request_id'] == self.result['request_id']:
                self.saved = saved
        if self.result:
            self.set_text(title_text(self.result) + ('' if self.saved else ' · 已计算、未保存'))
        elif self.detail is not None and self.detail.winfo_exists():
            self.update_details()
        if self.history is not None and self.history.winfo_exists():
            self.history.reload()

    def stop_request(self, status='cancelled'):
        attempt = self.attempt
        if self.app.background_recording and not getattr(self.app, '_closing', False) and self.service.active:
            from .job_reaper import JobReaper
            if self.reaper is None:
                self.reaper = JobReaper()
            job = self.service.active
            self.reaper.submit(job)
            self.service.active = self.service.result = None
        else:
            self.service.cancel()
        self.attempt = None
        if attempt is not None:
            result = terminal_opening_result(attempt['snapshot'], attempt['request_id'], status,
                '输入变化，原开局请求已失效' if status == 'stale' else
                '未收到匹配的开局结果' if status == 'failed' else '已取消开局计算',
                elapsed_seconds=perf_counter()-attempt['started'])
            self.persist(result, attempt)

    def refresh(self, force=False):
        if self._closed or getattr(self.app,'_closing',False):
            return
        key = self.live_key()
        if not force and key == self.key:
            return
        if not force and key == self.cancelled_key:
            return
        self.context_changed()
        self.key = key
        try:
            self.snapshot = self.current_input()
        except InputUnavailable as error:
            self.set_text('下轮EV：' + error.reason)
            return
        except Exception:
            self.set_text('下轮EV：记录需核对')
            return
        if self.app.analysis_panel.auto.get() or force:
            self.start()
        else:
            self.set_text('下轮EV：自动计算已关闭，点击查看／计算')

    def start(self):
        self.cancelled_key = None
        self.result = self.saved = None
        self.stop_request('stale')
        self.attempt = dict(snapshot=self.snapshot, request_id=uuid.uuid4().hex, started=perf_counter(),
            prefix=self.app.ctrl.read_prefix() if self.app.background_recording else
                   [e for e in self.app.ctrl.ledger.to_list() if e['seq'] <= self.snapshot.through_seq],
            sources=algorithm_manifest())
        try:
            self.attempt['request_id'] = self.service.start(self.snapshot)
            self.set_text(f'下轮EV估算·固定策略：计算中（{len(self.snapshot.participants)}人／每1单位底注）')
        except Exception as error:
            attempt, self.attempt = self.attempt, None
            self.persist(terminal_opening_result(self.snapshot, attempt['request_id'], 'failed',
                         str(error), reason_code='START_FAILED'), attempt)
            self.set_text('下轮EV：暂时无法启动计算，点击重试')

    def cancel(self):
        if self._closed:
            return
        self.stop_request()
        self.result = None
        self.cancelled_key = self.key = self.live_key()
        self.set_text('下轮EV：已取消，点击重新计算')

    def poll(self):
        if self._closed:
            return
        if getattr(self.app,'_closing',False):
            self._poll_id=self.app.after(100,self.poll);return
        if self.app.recording_busy or self.app._recording_faults:
            self._poll_id=self.app.after(100,self.poll);return
        try:
            if self.writer:
                for record, saved in self.writer.poll():
                    if saved and self.result and record['result']['request_id'] == self.result['request_id']:
                        self.saved = saved
                        self.set_text(title_text(self.result))
            self.refresh()
            was_active = self.service.active is not None
            result = self.service.poll()
            if result is not None:
                # Rebuild from the ledger before publication, even if redraw or
                # notification was missed. Current-hand historical mode is irrelevant.
                try:
                    attempt, self.attempt = self.attempt, None
                    if attempt is None or result.get('request_id') != attempt['request_id']:
                        raise ValueError('开局请求身份已失效')
                    current = self.current_input()
                    if self.live_key() != self.key or self.snapshot.input_digest != current.input_digest:
                        self.persist(terminal_opening_result(attempt['snapshot'], attempt['request_id'],
                                     'stale', '结果返回时输入已变化'), attempt)
                        self.context_changed()
                    elif result.get('status') != 'available':
                        terminal = terminal_opening_result(attempt['snapshot'], attempt['request_id'],
                            result.get('status') if result.get('status') in ('timeout','failed','unsupported','cancelled','stale') else 'failed',
                            result.get('reason', '计算未完成'), reason_code=result.get('reason_code'),
                            elapsed_seconds=result.get('elapsed_seconds', 0))
                        self.persist(terminal, attempt)
                        self.set_text('下轮EV：' + result.get('reason', '计算未完成'))
                    else:
                        self.result = validate_opening_result(result, current)
                        self.saved = self.persist(self.result, attempt)
                        self.set_text(title_text(self.result) + ('' if self.saved else ' · 已计算、未保存'))
                except Exception as error:
                    if attempt is not None:
                        self.persist(terminal_opening_result(attempt['snapshot'], attempt['request_id'],
                                     'stale', str(error)), attempt)
                    self.result = None
                    self.set_text('下轮EV：结果已失效或需核对')
            elif was_active and self.service.active is None:
                self.stop_request('failed')
                self.set_text('下轮EV：未收到匹配结果，点击重试')
        finally:
            if not self._closed:
                self._poll_id = self.app.after(100, self.poll)

    def show_details(self):
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.lift()
            return
        self.detail = tk.Toplevel(self.app)
        self.detail.title('下一轮投注EV估算')
        self.detail.geometry('680x420')
        self.detail_text = tk.Text(self.detail, wrap='word', padx=12, pady=12)
        self.detail_text.pack(fill=tk.BOTH, expand=True)
        buttons = ttk.Frame(self.detail)
        buttons.pack(pady=8)
        ttk.Button(buttons, text='重新计算当前', command=lambda: self.refresh(force=True)).pack(side=tk.LEFT,padx=4)
        ttk.Button(buttons, text='开局历史 / 原时点复算', command=self.show_history).pack(side=tk.LEFT,padx=4)
        ttk.Button(buttons, text='重试未保存记录', command=self.retry_saves).pack(side=tk.LEFT,padx=4)
        self.update_details()

    def show_history(self):
        from .opening_history import OpeningHistory
        if self.history is not None and self.history.winfo_exists():
            self.history.reload()
            self.history.lift()
        else:
            self.history = OpeningHistory(self)

    def update_details(self):
        lines = [self.app.var_opening_ev.get(), '', NOTE,
                 '区间只反映本次模拟的抽样误差，不包括桌规错误、漏录或策略选择差异。',
                 '只有整个95%区间高于0才标“正EV估算”；跨过0时显示“正负待定”。',
                 '正在发牌、未揭暗牌、未知烧牌、观察缺口或不支持的桌规不会显示旧数值。']
        if self.snapshot:
            import json
            rules = json.loads(self.snapshot.rules_json)
            order = '两手先各补一张，再顺序行动' if rules['split_deal_order'] == 'both_second_cards_first' else '第一手完成后再补第二手'
            peek = 'A检查、十点不检查BJ' if rules['check_bj_when'] == 'before_player_actions_A' else 'A与十点检查BJ'
            lines += ['', f'参与：{len(self.snapshot.participants)}人；本人：{self.snapshot.participants[self.snapshot.focal]}',
                      f'剩余：{sum(self.snapshot.counts)}张；牌面A～9／T：{self.snapshot.counts}',
                      f'桌规：S17、3:2、{peek}、同值分牌（{order}）、无再分、分A一张。']
        if self.result:
            low, high = self.result['interval']
            lines += [f"样本：{self.result['samples']:,}个独立模拟轮次；每轮从同一剩余组成重新洗牌。",
                      f'每1单位底注预期净收益：{self.result["ev"]:+.6f}',
                      f'95%区间：[{low:+.6f}, {high:+.6f}]；不是盈利保证。',
                      f'本次计算用时：{self.result["elapsed_seconds"]:.2f}秒。']
            lines.append('已保存为独立开局历史：'+self.saved['snapshot_id'] if self.saved else '已计算、未保存：结果仍在本窗口内，可重试保存。')
        if self.pending_saves:
            lines += ['', f'{len(self.pending_saves)}条开局记录待保存；点击“重试未保存记录”。']
            lines += [item.get('save_error','') for item in self.pending_saves[-3:]]
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete('1.0', tk.END)
        self.detail_text.insert(tk.END, '\n'.join(lines))
        self.detail_text.configure(state=tk.DISABLED)

    def close(self):
        self._closed = True
        if self.writer:
            self.writer.close()
        if self.reaper:
            self.reaper.close()
        if self._poll_id:
            self.app.after_cancel(self._poll_id)
        for variable, trace_id in self.traces:
            variable.trace_remove('write', trace_id)
        self.app.ctrl.remove_context_listener(self.context_changed)
        self.stop_request()
        self.service.close()
        if self.history is not None and self.history.winfo_exists():
            self.history.close()
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.destroy()
