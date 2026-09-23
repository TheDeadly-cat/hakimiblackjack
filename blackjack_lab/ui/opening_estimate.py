"""Title-area pre-deal EV, with independent identity and immediate invalidation."""
import tkinter as tk
from tkinter import ttk

from ..analysis.contracts import InputUnavailable
from ..analysis.opening import build_opening_input, NOTE
from ..analysis.opening_service import OpeningService, validate_opening_result


def title_text(result):
    tag = {'positive': '正EV估算', 'negative': '负EV估算', 'uncertain': '正负待定'}[result['sign']]
    return (f"下轮EV估算 {result['ev']:+.4f}/1  ·  优势率 {result['advantage_percent']:+.2f}%"
            f"  ·  95% ±{result['radius'] * 100:.2f}%  ·  {tag}")


class OpeningEstimateView:
    def __init__(self, app):
        self.app = app
        self.service = OpeningService()
        self.snapshot = self.result = self.key = None
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
        if self._closed or self.live_key() == self.key:
            return
        self.key = self.snapshot = self.result = None
        self.cancelled_key = None
        self.service.cancel()
        self.set_text('下轮EV：牌况已变化，等待更新')

    def refresh(self, force=False):
        if self._closed:
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
        self.result = None
        try:
            self.service.start(self.snapshot)
            self.set_text(f'下轮EV估算：计算中（{len(self.snapshot.participants)}人／每1单位底注）')
        except Exception:
            self.set_text('下轮EV：暂时无法启动计算，点击重试')

    def cancel(self):
        if self._closed:
            return
        self.service.cancel()
        self.result = None
        self.cancelled_key = self.key = self.live_key()
        self.set_text('下轮EV：已取消，点击重新计算')

    def poll(self):
        if self._closed:
            return
        try:
            self.refresh()
            was_active = self.service.active is not None
            result = self.service.poll()
            if result is not None:
                # Rebuild from the ledger before publication, even if redraw or
                # notification was missed. Current-hand historical mode is irrelevant.
                try:
                    current = self.current_input()
                    if self.live_key() != self.key or self.snapshot.input_digest != current.input_digest:
                        self.context_changed()
                    elif result.get('status') != 'available':
                        self.set_text('下轮EV：' + result.get('reason', '计算未完成'))
                    else:
                        self.result = validate_opening_result(result, current)
                        self.set_text(title_text(self.result))
                except Exception:
                    self.result = None
                    self.set_text('下轮EV：结果已失效或需核对')
            elif was_active and self.service.active is None:
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
        ttk.Button(self.detail, text='重新计算', command=lambda: self.refresh(force=True)).pack(pady=8)
        self.update_details()

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
        self.detail_text.configure(state=tk.NORMAL)
        self.detail_text.delete('1.0', tk.END)
        self.detail_text.insert(tk.END, '\n'.join(lines))
        self.detail_text.configure(state=tk.DISABLED)

    def close(self):
        self._closed = True
        if self._poll_id:
            self.app.after_cancel(self._poll_id)
        for variable, trace_id in self.traces:
            variable.trace_remove('write', trace_id)
        self.app.ctrl.remove_context_listener(self.context_changed)
        self.service.close()
        if self.detail is not None and self.detail.winfo_exists():
            self.detail.destroy()
