"""One bounded background worker for the remaining seats; the selected seat is shared."""
from time import perf_counter

from ..analysis.contracts import AVAILABLE, InputUnavailable, STATUS_ZH
from ..analysis.service import AnalysisService


class SeatOverview:
    def __init__(self, panel):
        self.panel, self.app = panel, panel.app
        self.service = AnalysisService()
        self.key = None
        self.rows = {}
        self.active_seat = None
        self.ready_at = 0

    def sync(self):
        key = self.app.ctrl.context_token
        if key == self.key:
            return False
        self.service.cancel()
        self.active_seat = None
        self.key, self.rows = key, {}
        self.ready_at = perf_counter() + .3
        seg = self.app._current_seg()
        if seg is None or len(seg.table.participants) < 2:
            return True
        for seat in seg.table.participants:
            hands = seg.table.players[seat].hands
            cards = ' / '.join(' '.join(c.rank for c in h.cards) + ('（已爆牌）' if h.is_bust else '') for h in hands)
            row = self.rows[seat] = dict(cards=cards or '待发牌', state='待计算', result=None, snapshot=None,
                                        busted=bool(hands) and all(h.is_bust for h in hands),
                                        total21=len(hands) == 1 and hands[0].total()[0] == 21)
            try:
                row['snapshot'] = self.app.ctrl.current_decision_input(seat)
            except InputUnavailable as error:
                row.update(state=STATUS_ZH[error.status], reason=error.reason)
        return True

    def accept_selected(self, result):
        self.sync()
        row = self.rows.get(result['input']['seat'])
        if row and row['snapshot'] and row['snapshot'].prefix_digest == result['input']['prefix_digest']:
            row.update(result=result, state=STATUS_ZH[result['status']])

    def poll(self):
        changed = self.sync()
        if (not self.panel.auto.get() or self.panel.recomputed_from
                or self.panel._auto_suppressed_key == self.panel._live_key()):
            if self.active_seat:
                self.rows[self.active_seat]['state'] = '待计算'
                self.service.cancel()
                self.active_seat = None
                changed = True
            return changed
        selected = self.app.var_analysis_target.get()
        if self.active_seat == selected:
            self.service.cancel()
            self.rows[selected]['state'] = '待计算'
            self.active_seat = None
            changed = True
        result = self.service.poll()
        if result and self.active_seat:
            row = self.rows[self.active_seat]
            if (self.key == self.app.ctrl.context_token
                    and result['input_digest'] == row['snapshot'].input_digest):
                row.update(result=result, state=STATUS_ZH[result['status']])
                if result['status'] == AVAILABLE:
                    try:
                        row['saved'] = self.app.ctrl.analysis_store.save(result)
                    except Exception as error:
                        row.update(state='保存待重试', reason=str(error))
            self.active_seat = None
            changed = True
        if self.active_seat is None and perf_counter() >= self.ready_at:
            for seat, row in self.rows.items():
                if seat != selected and row['snapshot'] is not None and row['result'] is None:
                    try:
                        self.service.start(row['snapshot'])
                        row['state'] = '计算中'
                        self.active_seat = seat
                    except Exception as error:
                        row.update(state='计算失败', reason=str(error), snapshot=None)
                    changed = True
                    break
        return changed

    def close(self):
        self.service.close()

    def cancel(self):
        self.service.cancel()
        self.active_seat = None
        for row in self.rows.values():
            row.update(result=None, state='已取消')
