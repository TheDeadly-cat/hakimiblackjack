"""Compact view of the existing AnalysisPanel request; no worker of its own."""
import tkinter as tk
from dataclasses import replace
from tkinter import ttk

from ..analysis.decision_summary import DecisionSummary, summarize_result, hand_text, input_identity
from ..core.table import DEALER, ACTION_DOUBLE, ACTION_SPLIT, ACTION_STAND, ACTION_SURRENDER
from ..ledger.events import FACE_HIDDEN
from ..analysis.seat_scenario import NOTE
from .daily_flow import current_flow
from .recent_entry import recent_visible, undo_label

PALETTE = dict(background='#F3F6F8', surface='#FFFFFF', ink='#173A45', accent='#187365',
               caution='#935213', error='#AD3030', muted='#52636B')


class CompactPanel(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=PALETTE['background'], padx=20, pady=16)
        self.app, self.panel = app, app.analysis_panel
        self.detail_window = None
        self.detail_text = None
        self.recording_open = False
        self.editor_open = False
        self.model = None
        self.columnconfigure(0, weight=1)
        self.identity = tk.StringVar()
        self.state = tk.StringVar()
        self.message = tk.StringVar()
        self.notes = tk.StringVar()
        self.recording_hint = tk.StringVar()
        self.heading = tk.StringVar()
        top = tk.Frame(self, bg=PALETTE['background'])
        top.grid(row=0, column=0, sticky='ew')
        top.columnconfigure(0, weight=1)
        self._label(top, variable=self.heading, font=('Microsoft YaHei UI', 10, 'bold')).grid(row=0, column=0, sticky='w')
        self.remove_player = ttk.Button(top, text='− 玩家', width=7, command=lambda: app.change_player_count(-1))
        self.remove_player.grid(row=0, column=1, padx=2)
        self.player_count = tk.StringVar()
        self._label(top, variable=self.player_count, font=('Microsoft YaHei UI', 9)).grid(row=0, column=2)
        self.add_player = ttk.Button(top, text='+ 玩家', width=7, command=lambda: app.change_player_count(1))
        self.add_player.grid(row=0, column=3, padx=2)
        self.identity_label = self._label(self, variable=self.identity, font=('Microsoft YaHei UI', 12, 'bold'), wrap=655)
        self.identity_label.grid(row=1, column=0, sticky='ew', pady=(6, 6))
        selectors = ttk.Frame(self)
        selectors.grid(row=2, column=0, sticky='ew')
        ttk.Label(selectors, text='分析').pack(side=tk.LEFT)
        self.seat = ttk.Combobox(selectors, width=7, state='readonly', textvariable=app.var_analysis_target,
                                values=[f'玩家{i}' for i in range(1, 8)])
        self.seat.pack(side=tk.LEFT, padx=4)
        self.hand = ttk.Combobox(selectors, width=22, state='readonly', textvariable=app.var_analysis_hand)
        self.hand.pack(side=tk.LEFT)
        self.seat.bind('<<ComboboxSelected>>', lambda e: app.refresh_all())
        self.compute = ttk.Button(selectors, text='计算', width=7, command=self.panel.calculate_current)
        self.compute.pack(side=tk.LEFT, padx=(12, 3))
        ttk.Button(selectors, text='取消', width=6, command=self.panel.cancel).pack(side=tk.LEFT)
        ttk.Checkbutton(selectors, text='自动', variable=self.panel.auto).pack(side=tk.LEFT, padx=4)
        self.status_label = self._label(self, variable=self.state, font=('Microsoft YaHei UI', 11, 'bold'))
        self.status_label.grid(row=3, column=0, sticky='w', pady=(10, 3))
        self.rows = []
        # Keep recording controls stationary when a result is invalidated.
        self.rowconfigure(4, minsize=92)
        self.rowconfigure(5, minsize=92)
        self.empty_result = self._label(self, '暂无当前建议', font=('Microsoft YaHei UI', 16))
        self.empty_result.grid(row=4, column=0, rowspan=2, sticky='w', padx=15)
        for i in range(2):
            row = tk.Frame(self, bg=PALETTE['surface'], padx=12, pady=8, highlightthickness=1,
                           highlightbackground='#D6E0E3')
            row.grid(row=4 + i, column=0, sticky='ew', pady=3)
            row.columnconfigure(1, weight=1)
            rank = self._label(row, '', font=('Microsoft YaHei UI', 10), surface=True, width=12)
            rank.grid(row=0, column=0, rowspan=2, sticky='w')
            action = self._label(row, '—', font=('Microsoft YaHei UI', 22, 'bold'), surface=True)
            action.grid(row=0, column=1, sticky='w')
            extra = self._label(row, '', font=('Microsoft YaHei UI', 9), surface=True)
            extra.grid(row=1, column=1, sticky='w')
            profit = self._label(row, '净盈利 —', font=('Microsoft YaHei UI', 14), surface=True)
            profit.grid(row=0, column=2, sticky='e')
            ev = self._label(row, 'EV —', font=('Consolas', 10), surface=True)
            ev.grid(row=1, column=2, sticky='e')
            self.rows.append((row, rank, action, extra, profit, ev))
        self.seat_table_frame = ttk.Frame(self)
        self.seat_table_frame.grid(row=4, column=0, rowspan=2, sticky='nsew', pady=3)
        ttk.Style(self).configure('SeatOverview.Treeview', rowheight=20)
        columns = ('seat', 'cards', 'best', 'second', 'ev', 'state')
        self.seat_table = ttk.Treeview(self.seat_table_frame, columns=columns, show='headings',
                                       height=7, selectmode='browse', style='SeatOverview.Treeview')
        for column, heading, width in zip(columns,
                ('玩家', '牌面', 'EV第1·净盈利', 'EV第2·净盈利', '第1 EV', '状态'),
                (50, 85, 120, 120, 85, 70)):
            self.seat_table.heading(column, text=heading)
            self.seat_table.column(column, width=width, minwidth=40)
        self.seat_table.pack(fill=tk.BOTH, expand=True)
        self._syncing_seat_table = False
        self.seat_table.bind('<<TreeviewSelect>>', self.select_seat)
        self.seat_table_frame.grid_remove()
        self.message_label = self._label(self, variable=self.message, wrap=650)
        self.message_label.grid(row=6, column=0, sticky='ew', pady=(7, 2))
        self._label(self, variable=self.notes, wrap=650, font=('Microsoft YaHei UI', 9)).grid(row=7, column=0, sticky='ew')
        links = ttk.Frame(self)
        links.grid(row=8, column=0, sticky='ew', pady=(10, 4))
        self.record_button = ttk.Button(links, text='录牌／纠错', command=self.toggle_recording)
        self.record_button.pack(side=tk.LEFT)
        self.details_button = ttk.Button(links, text='展开详情', command=self.show_details)
        self.details_button.pack(side=tk.LEFT, padx=5)
        self.current_button = ttk.Button(links, text='返回当前手牌', command=self.panel.return_to_current)
        ttk.Button(links, text='研究工作台', command=app.show_workbench).pack(side=tk.RIGHT)
        self._label(self, variable=self.recording_hint, wrap=650, font=('Microsoft YaHei UI', 9)).grid(row=9, column=0, sticky='w')
        self.flow_area = ttk.Frame(self, height=82)
        self.flow_area.grid(row=10, column=0, sticky='ew', pady=(5, 0))
        self.flow_area.grid_propagate(False)
        self.flow_area.columnconfigure(0, weight=1)
        self.flow_message = tk.StringVar()
        self.flow_label = ttk.Label(self.flow_area, textvariable=self.flow_message, wraplength=405)
        self.flow_label.grid(row=0, column=0, sticky='nw')
        self.flow_primary = ttk.Button(self.flow_area)
        self.flow_primary.grid(row=0, column=1, sticky='ne')
        self.only_settle = ttk.Button(self.flow_area, text='只结算', command=app.act_end_round)
        self.only_settle.grid(row=1, column=1, sticky='e')
        self.settlement_link = ttk.Button(self.flow_area, command=self.show_settlement)
        self.settlement_link.grid(row=1, column=0, sticky='w')
        self._build_recent()
        self.drawer = ttk.Frame(self)
        self.drawer.grid(row=12, column=0, sticky='ew', pady=(8, 0))
        self._build_recording()
        self.drawer.grid_remove()
        self.panel.add_view(self.render)
        self.render()

    def _label(self, parent, text='', variable=None, font=('Microsoft YaHei UI', 10), wrap=0, surface=False, **kw):
        return tk.Label(parent, text=text, textvariable=variable, anchor='w', justify='left', font=font,
                        bg=PALETTE['surface' if surface else 'background'], fg=PALETTE['ink'],
                        wraplength=wrap, **kw)

    def _build_recording(self):
        app = self.app
        self.record_prompt = ttk.Label(self.drawer, textvariable=app.var_entry_prompt, wraplength=650)
        self.record_prompt.pack(anchor='w')
        modes = ttk.Frame(self.drawer)
        modes.pack(fill=tk.X, pady=3)
        self.manual_modes = []
        for label, value in [('新发牌', '新发牌'), ('揭示暗牌／未知牌', '揭示')]:
            button = ttk.Radiobutton(modes, text=label, value=value, variable=app.var_mode)
            button.pack(side=tk.LEFT, padx=3)
            self.manual_modes.append(button)
        self.simple_label = ttk.Label(modes, text='按顺序输入可见牌；底牌自动揭示')
        self.simple_toggle = ttk.Checkbutton(modes, text='下轮简便暗牌', variable=app.var_simple_hole,
                                             command=app.change_simple_hole)
        self.simple_toggle.pack(side=tk.LEFT, padx=8)
        ttk.Button(modes, text='暂停／恢复', command=app._key_pause).pack(side=tk.RIGHT)
        cards = ttk.Frame(self.drawer)
        cards.pack(fill=tk.X, pady=3)
        for rank in ('A', '2', '3', '4', '5', '6', '7', '8', '9', 'T'):
            ttk.Button(cards, text=rank, width=4, command=lambda r=rank: app._key_rank(r)).pack(side=tk.LEFT, padx=2)
        self.hole_button = ttk.Button(cards, text='暗牌 .', width=7, command=app._key_hole)
        self.hole_button.pack(side=tk.LEFT, padx=3)
        actions = ttk.Frame(self.drawer)
        actions.pack(fill=tk.X, pady=3)
        self.action_buttons = []
        for label, action in [('停牌 -', ACTION_STAND), ('加倍 *', ACTION_DOUBLE), ('分牌 /', ACTION_SPLIT), ('投降', ACTION_SURRENDER)]:
            button = ttk.Button(actions, text=label, command=lambda a=action: app.act_action(a), width=9)
            button.pack(side=tk.LEFT, padx=2)
            self.action_buttons.append(button)
        # Exact undo target is always visible next to the recent card.
        control = ttk.Frame(self.drawer)
        control.pack(fill=tk.X, pady=3)
        for label, command in [('已检查，确认非BJ', app.act_peek_negative), ('记录／修正／设置', app.show_workbench)]:
            button = ttk.Button(control, text=label, command=command)
            button.pack(side=tk.LEFT, padx=2)
            if command == app.act_peek_negative:
                self.peek_button = button
        ttk.Label(self.drawer, textvariable=app.var_status, wraplength=650).pack(anchor='w', pady=2)

    def toggle_recording(self):
        self.recording_open = not self.recording_open
        (self.drawer.grid if self.recording_open else self.drawer.grid_remove)()
        self.record_button.configure(text='收起录牌' if self.recording_open else '录牌／纠错')
        self.app.geometry('720x850' if self.recording_open else '720x620')

    def select_seat(self, _event=None):
        if self._syncing_seat_table:
            return
        selected = self.seat_table.selection()
        if selected and selected[0] != self.app.var_analysis_target.get():
            self.app.var_analysis_target.set(selected[0])
            self.app.var_analysis_hand.set('（按顺序行动手）')
            self.app.refresh_all()

    def render_seats(self):
        self._syncing_seat_table = True
        try:
            rows = self.panel.overview.rows
            for iid in self.seat_table.get_children():
                if iid not in rows:
                    self.seat_table.delete(iid)
            for seat, row in rows.items():
                result = row['result']
                summary = summarize_result(result) if result else None
                choices = summary.choices if summary else ()
                def choice_text(index):
                    if len(choices) <= index:
                        return '—'
                    item = choices[index]
                    return f'{item.label} {item.profit:.2%}'
                state = summary.state if summary else row['state']
                if row['state'] == '保存待重试':
                    state = row['state']
                if seat == self.app.var_analysis_target.get() and self.panel.request_id and not self.panel.last_result:
                    state = '计算中'
                values = (seat, row['cards'], choice_text(0), choice_text(1),
                          f'{choices[0].ev:+.4f}' if choices else '—', state)
                if self.seat_table.exists(seat):
                    self.seat_table.item(seat, values=values)
                else:
                    self.seat_table.insert('', 'end', iid=seat, values=values)
            seat = self.app.var_analysis_target.get()
            if self.seat_table.exists(seat) and self.seat_table.selection() != (seat,):
                self.seat_table.selection_set(seat)
        finally:
            self._syncing_seat_table = False

    def live_identity(self):
        seg = self.app._current_seg()
        seat = self.app.var_analysis_target.get()
        if not seg:
            return '庄家明牌 —    |    ' + seat + ' · 尚未录牌'
        dealer = seg.table.dealer.hands
        dealer_text = '庄家 · 尚未录牌'
        if dealer and dealer[0].cards:
            cards = dealer[0].cards
            labels = [('暗牌' if seg.unresolved.get(c.event_id, {}).get('face_state') == FACE_HIDDEN
                       else '未知') if c.is_unknown else c.rank for c in cards]
            total, soft = dealer[0].total()
            score = '点数待确认' if total is None else f"{'软' if soft else ''}{total}点"
            dealer_text = f"庄家 {' '.join(labels)} · {score}"
        hands = seg.table.seat(seat).hands if seat in seg.table.players else []
        selected = self.app._analysis_hand_id(seg)
        index = next((i for i, h in enumerate(hands) if h.hand_id == selected), None)
        hand = hands[index] if index is not None else None
        if self.panel.current_input:
            return input_identity(self.panel.current_input.to_dict(), dealer_text)
        return f'{dealer_text}    |    {seat} · ' + (f'第{index + 1}手  {hand_text(c.rank for c in hand.cards)}' if hand else '尚未录牌')

    def pending_summary(self):
        panel, app = self.panel, self.app
        identity = self.live_identity()
        text = panel.status.get()
        state = '计算中' if panel.request_id else '需核对'
        if panel.request_id:
            text = '正在计算全部适用动作，可以继续录牌或取消。'
        if panel.recomputed_from and panel.request_snapshot:
            return DecisionSummary('历史复算中', input_identity(panel.request_snapshot.to_dict()),
                                   '原时点计算，不代表当前输入。', historical=True)
        if text.startswith('已取消'):
            state = '已取消'
        elif text.startswith('过期'):
            state = '结果已失效'
        elif text.startswith('可计算'):
            state = '待计算'
        if panel.current_input and panel.current_input.legal_actions in (('deal',), ('complete',)):
            state = '等待补牌' if panel.current_input.legal_actions == ('deal',) else '等待庄家'
            text = '当前为确定流程，没有新的玩家动作建议。'
        if not panel.request_id and panel.unavailable_code in ('NO_DECISION', 'NO_LEGAL_ACTION'):
            seg = app._current_seg()
            if seg and app.var_analysis_target.get() in seg.table.players:
                hands = seg.table.players[app.var_analysis_target.get()].hands
                if hands and any(h.awaiting_hit or h.doubled and not h.is_closed for h in hands):
                    state, text = '等待补牌', '已选动作，录入确定要发的一张牌后继续。'
                elif hands and all(seg.table.split_hand_closed(h) for h in hands):
                    state, text = '等待庄家', '玩家手牌已完成，当前没有玩家操作建议。'
        if panel.unavailable_code == 'ROUND_INACTIVE' and not panel.request_id:
            text = app.recording_inactive_message() or text
            state = text.split('；')[0]
        return DecisionSummary(state, identity, text)

    def render(self):
        panel = self.panel
        self.model = summarize_result(panel.last_result, bool(panel.recomputed_from)) if panel.last_result else self.pending_summary()
        if not self.model.historical:
            self.model = replace(self.model, identity=self.live_identity())
        model = self.model
        self.identity.set(model.identity)
        self.state.set(model.state)
        self.message.set(model.message)
        self.notes.set(' '.join(model.notes))
        multi = len(self.panel.overview.rows) > 1 and not model.historical
        if multi:
            self.notes.set(NOTE)
            self.message.set(f'{self.app.var_analysis_target.get()}：' +
                             (model.message or '点“展开详情”查看全部动作。'))
        inactive = self.app.recording_inactive_message()
        seg = self.app._current_seg()
        decks = seg.rules.n_decks if seg else self.app.var_decks.get()
        target = self.app.var_target.get()
        hand = f'／第{self.app._hand_ordinal(target)}手' if target != DEALER else ''
        self.heading.set(f'{decks}副牌 · ' + (inactive.split('；')[0] if inactive else f'当前发牌给：{target}{hand}'))
        count = sum(var.get() for var in self.app.var_participants.values())
        self.player_count.set(f'下一轮 {count} 人')
        self.add_player.state(['disabled'] if count >= 7 else ['!disabled'])
        self.remove_player.state(['disabled'] if count <= 1 else ['!disabled'])
        warning = model.partial or not model.choices
        self.status_label.configure(fg=PALETTE['caution' if warning else 'accent'])
        (self.empty_result.grid_remove if model.choices or multi else self.empty_result.grid)()
        (self.seat_table_frame.grid if multi else self.seat_table_frame.grid_remove)()
        if multi:
            self.render_seats()
        for i, (row, rank, action, extra, profit, ev) in enumerate(self.rows):
            if multi or i >= len(model.choices):
                row.grid_remove()
                rank.configure(text='')
                action.configure(text='—')
                profit.configure(text='净盈利 —')
                ev.configure(text='EV —')
                continue
            row.grid()
            choice = model.choices[i]
            rank.configure(text=choice.rank_label)
            action.configure(text=choice.label, fg=PALETTE['accent' if i == 0 else 'ink'])
            extra.configure(text=f'追加 {choice.additional:g} 单位' if choice.additional else '')
            profit.configure(text=f'净盈利 {choice.profit:.2%}')
            ev.configure(text=f'EV {choice.ev:+.6f}')
        self.hand.configure(values=panel.cmb_analysis_hand.cget('values'))
        self.compute.state(['disabled'] if panel.compute_button.instate(['disabled']) else ['!disabled'])
        if panel.recomputed_from:
            self.current_button.pack(side=tk.LEFT, padx=3)
        else:
            self.current_button.pack_forget()
        plan = self.app.ctrl.entry_plan
        simple = self.app.ctrl.simple_hole_active() and self.app.var_mode.get() != '揭示'
        for button in self.manual_modes:
            if simple:
                button.pack_forget()
            elif not button.winfo_manager():
                button.pack(side=tk.LEFT, padx=3, before=self.simple_toggle)
        if simple:
            self.simple_label.pack(side=tk.LEFT, before=self.simple_toggle)
            self.hole_button.pack_forget()
        else:
            self.simple_label.pack_forget()
            self.hole_button.pack(side=tk.LEFT, padx=3)
        self.peek_button.pack_forget()  # The fixed phase action owns this command.
        self.render_flow()
        self.render_recent()
        self.recording_hint.set(inactive or
                               (('录入已暂停 · ' if plan and plan.input_paused else '录入 ') + self.app.var_target.get()
                                + '  ·  Ctrl+1–7 切玩家  Ctrl+0 庄家  Tab 下一位  Shift+Tab 上一位'))
        for compact, original in zip(self.action_buttons, (self.app.btn_stand, self.app.btn_double, self.app.btn_split, self.app.btn_surr)):
            compact.state(['disabled'] if original.instate(['disabled']) else ['!disabled'])
        if self.detail_window and self.detail_window.winfo_exists():
            self.detail_text.configure(state=tk.NORMAL)
            self.detail_text.delete('1.0', tk.END)
            self.detail_text.insert('1.0', panel.text.get('1.0', 'end-1c'))
            self.detail_text.configure(state=tk.DISABLED)

    def _build_recent(self):
        self.recent_area = ttk.Frame(self)
        self.recent_area.grid(row=11, column=0, sticky='ew', pady=3)
        self.recent_area.columnconfigure(0, weight=1)
        self.recent_text = tk.StringVar(value='最近录入：—')
        ttk.Label(self.recent_area, textvariable=self.recent_text).grid(row=0, column=0, sticky='w')
        self.edit_button = ttk.Button(self.recent_area, text='改牌', width=6, command=self.open_correction)
        self.edit_button.grid(row=0, column=1, padx=3)
        self.undo_button = ttk.Button(self.recent_area, command=self.app.act_undo)
        self.undo_button.grid(row=0, column=2)
        self.editor = ttk.Frame(self.recent_area)
        self.editor.grid(row=1, column=0, columnspan=3, sticky='ew', pady=4)
        self.edit_rank = tk.StringVar()
        self.edit_reason = tk.StringVar(value='误按牌面')
        self.edit_note = tk.StringVar()
        self.edit_error = tk.StringVar()
        self.edit_target = tk.StringVar()
        ttk.Label(self.editor, textvariable=self.edit_target).grid(row=0, column=0, columnspan=4, sticky='w')
        ttk.Label(self.editor, text='正确牌面').grid(row=1, column=0)
        self.rank_select = ttk.Combobox(self.editor, textvariable=self.edit_rank, state='readonly', width=5,
                                       values=('A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'T'))
        self.rank_select.grid(row=1, column=1, padx=4)
        ttk.Combobox(self.editor, textvariable=self.edit_reason, width=12, state='readonly',
                     values=('误按牌面', '重新核对牌面')).grid(row=1, column=2, padx=4)
        ttk.Entry(self.editor, textvariable=self.edit_note, width=20).grid(row=1, column=3)
        ttk.Label(self.editor, text='补充说明可留空；保存后追加纠错，原记录保留。').grid(row=2, column=0, columnspan=4, sticky='w')
        self.save_correction = ttk.Button(self.editor, text='保存改牌', command=self.apply_correction)
        self.save_correction.grid(row=3, column=1)
        ttk.Button(self.editor, text='取消', command=self.close_correction).grid(row=3, column=2)
        ttk.Label(self.editor, textvariable=self.edit_error, wraplength=640, foreground=PALETTE['error']).grid(row=4, column=0, columnspan=4, sticky='w')
        self.editor.grid_remove()

    def render_recent(self):
        recent = recent_visible(self.app.ctrl)
        self.recent_text.set(recent.label if recent else '最近录入：—')
        self.edit_button.state(['!disabled'] if recent else ['disabled'])
        label = undo_label(self.app.ctrl)
        self.undo_button.configure(text=label)
        self.undo_button.state(['disabled'] if label == '无可撤销记录' else ['!disabled'])
        if self.editor_open and self.edit_context != self.app.ctrl.context_token:
            self.edit_error.set('记录已变化；请取消后重新选择最近牌。')
            self.save_correction.state(['disabled'])

    def open_correction(self):
        recent = recent_visible(self.app.ctrl)
        if recent is None:
            return
        self.edit_event_id, self.edit_context = recent.event_id, self.app.ctrl.context_token
        self.edit_target.set('正在修改：' + recent.label.removeprefix('最近录入：'))
        self.edit_rank.set(recent.rank)
        self.edit_note.set('')
        self.edit_error.set('')
        self.save_correction.state(['!disabled'])
        self.editor_open = True
        self.editor.grid()
        self.rank_select.focus_set()

    def close_correction(self):
        self.editor_open = False
        self.editor.grid_remove()
        self.app.focus_set()

    def apply_correction(self):
        before = self.app.ctrl.commit_revision
        try:
            reason = self.edit_reason.get() + (('：' + self.edit_note.get().strip()) if self.edit_note.get().strip() else '')
            self.app.ctrl.correct_recent_visible(self.edit_event_id, self.edit_rank.get(), reason, self.edit_context)
            self.close_correction()
            self.app._sync_from_plan()
            self.app.refresh_all()
            self.app.set_status('已追加纠错并更新计算；原始事件及历史判断保留。')
        except Exception as error:
            saved = self.app.ctrl.commit_revision > before
            self.edit_error.set(('改牌已保存，请刷新，勿重复提交：' if saved else '未保存：') + str(error))
            if saved:
                self.save_correction.state(['disabled'])

    def render_flow(self):
        flow = self.flow = current_flow(self.app.ctrl)
        self.flow_message.set(flow.message if flow.stage != 'player' else
                              self.app.var_legal.get().replace('\n', '；') or flow.message)
        commands = {'review': self.app.show_workbench, 'start': self.app.act_new_round,
                    'resume': self.app._key_pause, 'peek': self.app.act_peek_negative,
                    'next': lambda rid=flow.round_id: self.app.act_complete_and_next(rid)}
        if flow.command:
            self.flow_primary.configure(text=flow.label, command=commands[flow.command])
            self.flow_primary.grid()
        else:
            self.flow_primary.grid_remove()
        (self.only_settle.grid if flow.stage == 'ready' else self.only_settle.grid_remove)()
        for button in self.action_buttons:
            if flow.stage == 'player':
                if not button.winfo_manager():
                    button.pack(side=tk.LEFT, padx=2)
            else:
                button.pack_forget()
        seg = self.app._current_seg()
        results = seg.settlements if seg else []
        self.last_settlement = [r for r in results if r['round'] == results[-1]['round']] if results else []
        if self.last_settlement:
            total = sum(r['net_units'] for r in self.last_settlement)
            self.settlement_link.configure(text=f"第{self.last_settlement[0]['round']}轮已结算 · 合计 {total:+g} 单位 · 查看")
            self.settlement_link.grid()
        else:
            self.settlement_link.grid_remove()

    def show_settlement(self):
        if not self.last_settlement:
            return
        win = tk.Toplevel(self.app)
        win.title('已结算记录 · 确定性输赢，非EV')
        text = '\n'.join(f"{r['seat']} {r['hand_id']}：{r['result']}，{r['net_units']:+g} 单位" for r in self.last_settlement)
        ttk.Label(win, text=text, padding=16).pack()
        ttk.Button(win, text='关闭', command=win.destroy).pack(pady=8)

    def show_details(self):
        if self.detail_window and self.detail_window.winfo_exists():
            self.detail_window.lift()
            return
        win = self.detail_window = tk.Toplevel(self.app)
        win.title('展开详情 · 与小面板共用当前结果')
        win.geometry('800x660')
        ttk.Label(win, textvariable=self.panel.status, wraplength=750).pack(anchor='w', padx=12, pady=8)
        body = ttk.Frame(win)
        body.pack(fill=tk.BOTH, expand=True, padx=10)
        self.detail_text = tk.Text(body, wrap=tk.WORD, font=('Microsoft YaHei UI', 10), state=tk.DISABLED)
        scroll = ttk.Scrollbar(body, command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.detail_text.pack(fill=tk.BOTH, expand=True)
        ttk.Label(win, textvariable=self.panel.persistence, wraplength=750).pack(anchor='w', padx=12, pady=6)
        ttk.Button(win, text='收起详情', command=win.destroy).pack(pady=5)
        self.render()
