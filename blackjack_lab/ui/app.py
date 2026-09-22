# -*- coding: utf-8 -*-
"""Hakimi Blackjack Lab —— V0.2b1 手动录牌与顺序分牌分析工作台（Tkinter）。

界面结构遵循开发大纲第 5 节：
- 顶部：模式、6/7/8 副、规则确认状态、牌靴/轮次、记录完整性、分析状态；
- 左侧：手动录牌区（不做假实时动画）；
- 中部：庄家与 7 座位、手牌与分牌关系；
- 右侧：当前合法动作、账面组成及真实的受限单手概率/EV分析；
- 底部：事件时间线、待确认/缺口提醒、纠错入口。
不使用颜色单独表达状态，不出现"必胜/稳赢"类措辞。
"""
from __future__ import annotations

import tkinter as tk
import json
from functools import wraps
from contextlib import contextmanager
from dataclasses import asdict
from tkinter import filedialog, messagebox, simpledialog, ttk
from pathlib import Path
from typing import Dict, List, Optional

from .. import ENGINE_VERSION, __version__
from ..core.cards import RANKS, SUIT_NAME, TEN_BUCKET, UNKNOWN
from ..core.rules import (
    CAPABILITY_MATRIX, CONFIRM_UNKNOWN, CONFIRM_VERIFIED, RuleProfile,
)
from ..core.shoe import ConsistencyError
from ..core.table import (
    ACTION_DOUBLE, ACTION_HIT, ACTION_SPLIT, ACTION_STAND, ACTION_SURRENDER, DEALER,
    PHASE_DEALING, PHASE_IN_PROGRESS, PHASE_NO_ROUND, TableError, player_seat_name,
)
from ..ledger.events import (
    CARD_DEALT, CARD_REVEALED, FACE_HIDDEN, FACE_UNKNOWN, SOURCE_MANUAL,
    BURN_CARDS, OBSERVATION_GAP, PEEK_NEGATIVE, ROUND_ENDED,
)
from ..ledger.ledger import LedgerError
from ..storage.export import export_csv, export_json
from ..storage.database import LocalStore
from .controller import SessionController
from .analysis_panel import AnalysisPanel
from .compact_panel import CompactPanel
from .window_layout import WindowLayout
from .automatic_flow import dealer_finish_message
from .deal_entry import (
    MODE_CONTINUATION, MODE_DEALER, MODE_INITIAL, MODE_MANUAL, MODE_PEEK_WAIT, MODE_UNALIGNED,
    dealer_up_requires_peek, next_open_hand,
)
from .manual_keymap import (
    KIND_DOUBLE, KIND_HIT, KIND_HOLE, KIND_JUMP, KIND_NEXT, KIND_PAUSE, KIND_PREV,
    KIND_RANK, KIND_SPLIT, KIND_STAND, KIND_UNDO, ManualKeyBinder, TEXT_WIDGETS,
)
from ..analysis.contracts import research_rules
from ..analysis.split_contracts import (
    ALL_SPLIT_PROFILES, DAS_PROFILE, SAME_VALUE_DAS_PROFILE, SAME_VALUE_SPLIT_PROFILE,
    SPLIT_PROFILE, das_research_rules, same_value_das_research_rules,
    same_value_split_research_rules, split_research_rules,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = PROJECT_ROOT / "data" / "blackjack_lab.db"
SEAT_NAMES = [DEALER] + [player_seat_name(i) for i in range(1, 8)]
CARD_BUTTONS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10",
                "J", "Q", "K")


def tracked_operation(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        self._operation_start_revision = self.ctrl.commit_revision
        return function(self, *args, **kwargs)
    return invoke


class RoundObservationDialog(simpledialog.Dialog):
    """Explicit observation attestation; unknown is the default, never inferred from prose."""
    choices = {"未知：不能确认是否漏牌": "unknown", "完整：确认全部移出牌均已记录": "complete",
               "不完整：已知存在漏录": "incomplete"}

    def body(self, parent):
        ttk.Label(parent, text="观察完整性与是否计算输赢分别记录。\n已知缺失的初始牌或待补牌不能靠声明完整解除。", wraplength=410).pack(padx=10, pady=10)
        self.selection = tk.StringVar(value=next(iter(self.choices)))
        self.selector = ttk.Combobox(parent, state="readonly", width=43,
                                    textvariable=self.selection, values=list(self.choices))
        self.selector.pack(padx=10, pady=10)
        return self.selector

    def apply(self):
        self.result = self.choices[self.selection.get()]

    def buttonbox(self):
        buttons = ttk.Frame(self)
        ttk.Button(buttons, text="确认观察状态", command=self.ok).pack(side=tk.LEFT, padx=5)
        ttk.Button(buttons, text="取消，不结束本轮", command=self.cancel).pack(side=tk.LEFT, padx=5)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        buttons.pack(pady=8)


class BlackjackLabApp(tk.Tk):
    def __init__(self, db_path: str | Path = DEFAULT_DB, recording_source=SOURCE_MANUAL, *, auto_analysis=True):
        super().__init__()
        self.recording_source = recording_source
        self.auto_analysis = auto_analysis
        self.title(f"Hakimi Blackjack Lab V{__version__} 手动记录工作台（本地离线）")
        self.geometry("720x620")
        self.minsize(660, 460)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Style(self).configure("Card.TButton", font=("Consolas", 12), padding=2)

        self._ask_recover_if_any(db_path)
        self._operation_start_revision = self.ctrl.commit_revision
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # 变量
        self.var_decks = tk.IntVar(value=8)
        self.var_s17 = tk.StringVar(value="S17")
        self.var_bjp = tk.StringVar(value="3:2")
        self.var_split_match = tk.StringVar(value="same_rank")
        self.var_das = tk.StringVar(value="未知")
        self.var_surrender = tk.StringVar(value="不支持")
        self.var_confirm = tk.StringVar(value=CONFIRM_UNKNOWN)
        self.var_target = tk.StringVar(value=player_seat_name(1))
        self.var_analysis_target = tk.StringVar(value=player_seat_name(1))
        self.var_analysis_hand = tk.StringVar(value="（最新一手）")
        self.var_my_seat = tk.StringVar(value=player_seat_name(1))
        self.var_deal_direction = tk.StringVar(value="forward")
        self.var_entry_prompt = tk.StringVar(value="尚未冻结本轮发牌计划。确认参与座位和本人座位后开新一轮。")
        self.var_hand = tk.StringVar(value="（最新一手）")
        self.var_mode = tk.StringVar(value="新发牌")
        self.var_simple_hole = tk.BooleanVar(value=False)
        self.var_suit = tk.StringVar(value="未知")
        self.var_status = tk.StringVar(value="就绪：请先选择牌副数并新建牌靴")
        self.rule_details = {}
        self._load_common_settings()
        self.var_participants = {name: tk.BooleanVar(value=name == "玩家1") for name in SEAT_NAMES[1:]}
        self._hand_ids = {}
        self._analysis_hand_ids = {}
        self._syncing_target = False
        self._key_binder = None
        self._pending_slot_id = None
        self._preferred_recording_hand_id = None

        self.workbench = ttk.Frame(self)
        self.workbench.columnconfigure(0, weight=1)
        self.workbench.rowconfigure(1, weight=1)
        self._build_top()
        self._build_body()
        self._build_bottom()
        self.window_layout = WindowLayout(self, db_path)
        self.compact_viewport = ttk.Frame(self)
        self.compact_canvas = tk.Canvas(self.compact_viewport, highlightthickness=0, bg='#F3F6F8')
        self.compact_scroll = ttk.Scrollbar(self.compact_viewport, command=self.compact_canvas.yview)
        self.compact_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.compact_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.compact_canvas.configure(yscrollcommand=self.compact_scroll.set)
        self.compact_panel = CompactPanel(self.compact_canvas, self)
        self.compact_window = self.compact_canvas.create_window((0, 0), window=self.compact_panel, anchor='nw')
        self.compact_canvas.bind('<Configure>', lambda event: self.compact_canvas.itemconfigure(self.compact_window, width=event.width))
        self.compact_panel.bind('<Configure>', lambda event: self.compact_canvas.configure(scrollregion=self.compact_canvas.bbox('all')))
        self.show_compact()
        self._bind_keys()
        self._restore_plan_identity()
        self.refresh_all()
        self.experiment_window = None

    # ============================================================
    # 界面构建
    # ============================================================
    def _ask_recover_if_any(self, db_path) -> None:
        store = LocalStore(db_path)
        try:
            old = [s for s in store.list_sessions() if s["event_count"] > 1]
        finally:
            store.close()
        if old:
            if messagebox.askyesno("恢复会话",
                                   f"发现 {len(old)} 个本地历史会话，是否恢复最近一个？\n"
                                   "选“否”将开始全新会话（旧记录仍保留）。"):
                latest = old[-1]
                try:
                    self.ctrl = SessionController.recover(db_path, latest["session_id"])
                    return
                except Exception as e:  # 恢复失败不允许假装成功
                    messagebox.showerror("恢复失败", str(e))
        self.ctrl = SessionController(db_path, recording_source=self.recording_source)

    def _build_top(self) -> None:
        bar = ttk.LabelFrame(self.workbench, text="下一牌靴设置（当前锁定快照见状态行）")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=4)
        ttk.Button(bar, text="返回小面板", command=self.show_compact).grid(row=0, column=6, padx=8)

        ttk.Label(bar, text=f"V0.2b1 手动录牌 · {self.recording_source}").grid(
            row=0, column=0, sticky="w", padx=4, pady=2)

        deck_box = ttk.Frame(bar)
        deck_box.grid(row=0, column=1, padx=8)
        ttk.Label(deck_box, text="牌副数").pack(side=tk.LEFT)
        for n in (6, 7, 8):
            ttk.Radiobutton(deck_box, text=f"{n}副", value=n,
                            variable=self.var_decks).pack(side=tk.LEFT)

        rule_box = ttk.Frame(bar)
        rule_box.grid(row=1, column=0, columnspan=4, sticky="w", padx=4)
        ttk.Label(rule_box, text="软17").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_s17, width=5,
                     values=["S17", "H17", "未知"], state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Label(rule_box, text="BJ赔付").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_bjp, width=5,
                     values=["3:2", "6:5", "未知"], state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Label(rule_box, text="分牌条件").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_split_match, width=10,
                     values=["same_rank 同牌面", "same_value 同点值"],
                     state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Label(rule_box, text="分后加倍").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_das, width=5,
                     values=["允许", "禁止", "未知"], state="readonly").pack(side=tk.LEFT, padx=2)
        ttk.Label(rule_box, text="投降").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_surrender, width=6,
                     values=["不支持", "late", "early"], state="readonly"
                     ).pack(side=tk.LEFT, padx=2)
        ttk.Label(rule_box, text="规则确认").pack(side=tk.LEFT)
        ttk.Combobox(rule_box, textvariable=self.var_confirm, width=7,
                     values=[CONFIRM_UNKNOWN, CONFIRM_VERIFIED],
                     state="readonly").pack(side=tk.LEFT, padx=2)

        ttk.Button(bar, text="新建牌靴（锁定规则）",
                   command=self.act_new_shoe).grid(row=0, column=2, padx=6)
        ttk.Button(bar, text="规则详情 / 补充字段",
                   command=self.act_rule_details).grid(row=0, column=3, padx=6)
        template_button = ttk.Menubutton(bar, text="研究模板（新靴用）")
        template_menu = tk.Menu(template_button, tearoff=False)
        template_menu.add_command(label="一键常用设置（含简便暗牌）", command=self.act_common_settings)
        template_menu.add_separator()
        template_menu.add_command(label="V0.2a 原单手分析模板（四手录牌规则）", command=self.act_research_template)
        template_menu.add_command(label="V0.2b1 两手顺序分牌模板", command=lambda: self.act_research_template(split=True))
        template_menu.add_command(label="V0.2b2 两手顺序分牌DAS模板", command=lambda: self.act_research_template(das=True))
        template_menu.add_command(label="两手顺序分牌·同点值", command=lambda: self.act_research_template(split=True, same_value=True))
        template_menu.add_command(label="两手顺序分牌DAS·同点值", command=lambda: self.act_research_template(das=True, same_value=True))
        template_button.configure(menu=template_menu)
        template_button.grid(row=0, column=4, padx=6)
        ttk.Button(bar, text="对照实验", command=self.act_experiments).grid(row=0, column=5, padx=6)

        self.var_topinfo = tk.StringVar()
        ttk.Label(bar, textvariable=self.var_topinfo, foreground="#1a3c6e"
                  ).grid(row=2, column=0, columnspan=6, sticky="w", padx=4)

    def _build_body(self) -> None:
        body = ttk.Frame(self.workbench)
        body.grid(row=1, column=0, sticky="nsew", padx=4)

        # ---------- 左侧：录牌 ----------
        left = ttk.LabelFrame(body, text="左侧：手动录牌")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)

        seat_box = ttk.LabelFrame(left, text="目标座位")
        seat_box.pack(fill=tk.X, padx=4, pady=3)
        for i, name in enumerate(SEAT_NAMES):
            ttk.Radiobutton(seat_box, text=name, value=name,
                            variable=self.var_target,
                            command=self._on_recording_seat_clicked).grid(row=i // 4, column=i % 4, sticky="w", padx=2)
        participants = ttk.LabelFrame(left, text="下一轮参与座位（空座不勾选，编号不变）")
        participants.pack(fill=tk.X, padx=4, pady=3)
        for i, name in enumerate(SEAT_NAMES[1:]):
            ttk.Checkbutton(participants, text=name, variable=self.var_participants[name]).grid(
                row=i // 4, column=i % 4, sticky="w")
        mine = ttk.Frame(participants)
        mine.grid(row=2, column=0, columnspan=4, sticky="w", padx=2, pady=2)
        ttk.Label(mine, text="我的座位").pack(side=tk.LEFT)
        ttk.Combobox(mine, textvariable=self.var_my_seat, values=SEAT_NAMES[1:],
                     state="readonly", width=8).pack(side=tk.LEFT, padx=4)
        ttk.Radiobutton(mine, text="1→7", value="forward",
                        variable=self.var_deal_direction).pack(side=tk.LEFT)
        ttk.Radiobutton(mine, text="7→1", value="reverse",
                        variable=self.var_deal_direction).pack(side=tk.LEFT)
        self.var_my_seat.trace_add("write", lambda *_: self.var_analysis_target.set(self.var_my_seat.get()))

        hand_box = ttk.LabelFrame(left, text="目标手牌（分牌后选择）")
        hand_box.pack(fill=tk.X, padx=4, pady=3)
        self.cmb_hand = ttk.Combobox(hand_box, textvariable=self.var_hand,
                                     values=["（最新一手）"], state="readonly")
        self.cmb_hand.pack(fill=tk.X, padx=4, pady=2)
        ttk.Button(hand_box, text="核对后人工继续", command=self.act_manual_alignment).pack(fill=tk.X, padx=4)
        self.cmb_hand.bind("<<ComboboxSelected>>", lambda e: self._on_recording_hand_clicked())

        mode_box = ttk.Frame(left)
        mode_box.pack(fill=tk.X, padx=4)
        ttk.Radiobutton(mode_box, text="新发牌", value="新发牌",
                        variable=self.var_mode).pack(side=tk.LEFT)
        ttk.Radiobutton(mode_box, text="揭示暗牌/未知牌", value="揭示",
                        variable=self.var_mode).pack(side=tk.LEFT)

        suit_box = ttk.Frame(left)
        suit_box.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(suit_box, text="花色").pack(side=tk.LEFT)
        for txt, val in [("未知", "未知"), ("♠", "S"), ("♥", "H"),
                         ("♦", "D"), ("♣", "C")]:
            ttk.Radiobutton(suit_box, text=txt, value=val,
                            variable=self.var_suit).pack(side=tk.LEFT)

        card_box = ttk.LabelFrame(left, text="牌面（点击录入；T=10点未细分）")
        card_box.pack(fill=tk.X, padx=4, pady=3)
        self.workbench_card_buttons = []
        for i, r in enumerate(CARD_BUTTONS):
            button = ttk.Button(card_box, text=r, width=3, style="Card.TButton",
                                command=lambda x=r: self.act_card(x))
            button.grid(row=i // 5, column=i % 5, padx=2, pady=2)
            self.workbench_card_buttons.append(button)
        button = ttk.Button(card_box, text="T 未细分", width=8, command=lambda: self.act_card(TEN_BUCKET))
        button.grid(row=3, column=0, columnspan=2, sticky="we", padx=2)
        self.workbench_card_buttons.append(button)
        ttk.Button(card_box, text="未知牌面", width=8,
                   command=self.act_unknown_card
                   ).grid(row=3, column=2, columnspan=3, sticky="we", padx=2)
        ttk.Button(card_box, text="暗牌（先不揭示）",
                   command=self.act_hidden_card
                   ).grid(row=4, column=0, columnspan=5, sticky="we", padx=2, pady=2)

        # ---------- 中部：牌桌 ----------
        mid = ttk.LabelFrame(body, text="中部：牌桌（庄家 + 7 座位）")
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.entry_prompt_label = ttk.Label(mid, textvariable=self.var_entry_prompt,
                                            justify=tk.LEFT, foreground="#1a3c6e", wraplength=500)
        self.entry_prompt_label.pack(anchor="w", padx=6, pady=4)
        ttk.Label(mid, text="0=十点T  1=A  2–9=点值  .=暗牌已发  Enter只换目标  -停牌",
                  foreground="#555", wraplength=500).pack(anchor="w", padx=6, pady=3)
        self.dealer_view = ttk.LabelFrame(mid, text="庄家")
        self.dealer_view.pack(fill=tk.X, padx=6, pady=4)
        self.lbl_dealer = ttk.Label(self.dealer_view, text="（无）", justify=tk.LEFT)
        self.lbl_dealer.pack(anchor="w", padx=6, pady=4)

        grid = ttk.Frame(mid)
        grid.pack(fill=tk.BOTH, expand=True, padx=4, pady=2)
        self.seat_views: Dict[str, ttk.LabelFrame] = {}
        self.seat_labels: Dict[str, ttk.Label] = {}
        for i in range(1, 8):
            name = player_seat_name(i)
            f = ttk.LabelFrame(grid, text=name)
            f.grid(row=(i - 1) // 2, column=(i - 1) % 2,
                   sticky="nsew", padx=4, pady=3)
            grid.columnconfigure((i - 1) % 2, weight=1)
            grid.rowconfigure((i - 1) // 2, weight=1)
            lbl = ttk.Label(f, text="（空座）", justify=tk.LEFT, width=34)
            lbl.pack(anchor="w", padx=4, pady=3)
            self.seat_views[name] = f
            self.seat_labels[name] = lbl
        # 第 8 格放规则能力矩阵摘要
        f = ttk.LabelFrame(grid, text="能力边界")
        f.grid(row=3, column=1, sticky="nsew", padx=4, pady=3)
        ttk.Label(f, justify=tk.LEFT, text=(
            "手动录牌 / 分牌归属 / 撤销纠错\n"
            "6·7·8副守恒/SQLite恢复/JSON导出\n"
            "V0.2b1：单手 / 两手顺序合计EV / 复盘\n"
            "V0.2b2：显式DAS模板（非A分手一次加倍）\n"
            "再分/多玩家EV/识别捕获未支持"),
            foreground="#555").pack(anchor="w", padx=4, pady=3)

        # ---------- 右侧：动作 + 组成 ----------
        right = ttk.LabelFrame(body, text="右侧：合法动作 / 牌靴组成 / 分析状态", width=330)
        right.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)
        right.pack_propagate(False)
        right.grid_propagate(False)
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        act_box = ttk.LabelFrame(right, text="当前合法动作（针对选中手牌）")
        act_box.grid(row=0, column=0, sticky="ew", padx=4, pady=3)
        self.btn_stand = ttk.Button(act_box, text="停牌",
                                    command=lambda: self.act_action(ACTION_STAND))
        act_box.columnconfigure(0, weight=1)
        act_box.columnconfigure(1, weight=1)
        self.btn_stand.grid(row=0, column=0, sticky="ew", padx=2, pady=2)
        self.btn_double = ttk.Button(act_box, text="加倍",
                                     command=lambda: self.act_action(ACTION_DOUBLE))
        self.btn_double.grid(row=0, column=1, sticky="ew", padx=2, pady=2)
        self.btn_split = ttk.Button(act_box, text="分牌",
                                    command=lambda: self.act_action(ACTION_SPLIT))
        self.btn_split.grid(row=1, column=0, sticky="ew", padx=2, pady=2)
        self.btn_surr = ttk.Button(act_box, text="投降",
                                   command=lambda: self.act_action(ACTION_SURRENDER))
        self.btn_surr.grid(row=1, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(act_box, text="庄家检查底牌：确认非 BJ",
                   command=self.act_peek_negative).grid(row=2, column=0, columnspan=2, sticky="ew", padx=2, pady=2)
        self.var_legal = tk.StringVar(value="")
        ttk.Label(act_box, textvariable=self.var_legal, justify=tk.LEFT,
                  foreground="#444", wraplength=295).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=2)

        self.analysis_tabs = ttk.Notebook(right)
        self.analysis_tabs.grid(row=1, column=0, sticky="nsew", padx=3, pady=3)
        self.analysis_panel = AnalysisPanel(self.analysis_tabs, self)
        self.analysis_tabs.add(self.analysis_panel, text="动作EV / 概率")
        comp_box = ttk.Frame(self.analysis_tabs)
        self.analysis_tabs.add(comp_box, text="牌靴组成")
        self.txt_comp = tk.Text(comp_box, width=38, height=16, wrap=tk.WORD,
                                state=tk.DISABLED, font=("Consolas", 9))
        comp_scroll = ttk.Scrollbar(comp_box, command=self.txt_comp.yview)
        self.txt_comp.configure(yscrollcommand=comp_scroll.set)
        comp_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_comp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _build_bottom(self) -> None:
        bottom = ttk.LabelFrame(self.workbench, text="底部：轮次操作 / 事件时间线 / 纠错")
        bottom.grid(row=2, column=0, sticky="ew", padx=6, pady=4)

        btns = ttk.Frame(bottom)
        btns.pack(fill=tk.X, padx=4, pady=2)
        for text, cmd in [
            ("新开一轮", self.act_new_round),
            ("结束本轮并结算", self.act_end_round),
            ("结束本牌靴", self.act_end_shoe),
            ("撤销最后事件", self.act_undo),
            ("登记烧牌(数量已知)", self.act_burn),
            ("标记观察缺口", self.act_gap),
            ("导出 JSON", self.act_export_json),
            ("导出 CSV", self.act_export_csv),
        ]:
            ttk.Button(btns, text=text, command=cmd).pack(side=tk.LEFT, padx=3, pady=2)
        more = ttk.Frame(bottom)
        more.pack(fill=tk.X, padx=4, pady=2)
        for text, cmd in [
            ("修正选中事件", self.act_correct), ("查看选中时点", self.act_history),
            ("结束本轮（未结算）", self.act_end_unsettled),
            ("导入 JSON / CSV", self.act_import), ("恢复历史会话", self.act_recover),
            ("备份数据库", self.act_backup), ("旧会话诊断", self.act_diagnose),
            ("刷新界面", self.act_refresh),
        ]:
            ttk.Button(more, text=text, command=cmd).pack(side=tk.LEFT, padx=3)

        line = ttk.Frame(bottom)
        line.pack(fill=tk.X, padx=4)
        self.lst_timeline = tk.Listbox(line, height=6, font=("Consolas", 9), exportselection=False)
        sb = ttk.Scrollbar(line, orient=tk.VERTICAL,
                           command=self.lst_timeline.yview)
        self.lst_timeline.configure(yscrollcommand=sb.set)
        self.lst_timeline.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)

        ttk.Label(bottom, textvariable=self.var_status,
                  foreground="#1a3c6e", wraplength=1120).pack(anchor="w", padx=6, pady=2)

    def show_compact(self):
        self.workbench.grid_remove()
        self.compact_viewport.grid(row=1, column=0, sticky='nsew')
        self.window_layout.switch('drawer' if self.compact_panel.recording_open else 'compact')
        self.title('Hakimi Blackjack Lab · 当前手牌')
        self.compact_panel.render()

    def show_workbench(self):
        self.compact_panel.close_correction()
        self.compact_viewport.grid_remove()
        self.workbench.grid(row=1, column=0, sticky='nsew')
        self.window_layout.switch('workbench')
        self.title('Hakimi Blackjack Lab · 研究工作台')

    def _bind_keys(self) -> None:
        self._key_binder = ManualKeyBinder(self, self._on_manual_command,
                                           is_recording_surface=self._is_recording_surface)

    def _is_recording_surface(self, event) -> bool:
        if (hasattr(self, 'compact_panel') and self.compact_panel.editor_open
                and str(getattr(event, 'widget', '')).startswith(str(self.compact_panel.editor))):
            return False  # The correction fields keep their normal editing keys.
        widget = getattr(event, "widget", None)
        if widget is None:
            return False
        try:
            if widget.winfo_toplevel() != self:
                return False
            if self.grab_current() not in (None, self):
                return False
            if widget.winfo_class() in TEXT_WIDGETS:
                return False
        except tk.TclError:
            return False
        return True

    def _on_recording_seat_clicked(self) -> None:
        if not self._syncing_target:
            plan = self.ctrl.entry_plan
            if plan and plan.mode == MODE_INITIAL and not plan.paused:
                current = plan.slot()
                plan.pause_manual("直接选择座位，自动轮转已暂停",
                                  current.slot_id if current and current.slot_id not in plan.filled_slots else None)
                self.ctrl.save_entry_plan()
        self.refresh_all()
        self._on_recording_hand_clicked()

    def _on_recording_hand_clicked(self) -> None:
        plan = self.ctrl.entry_plan
        seg = self._current_seg()
        if plan and seg and plan.mode in (MODE_CONTINUATION, MODE_MANUAL):
            plan.continuation_seat = self.var_target.get()
            plan.continuation_hand_id = self._selected_hand_id(seg)
            plan.continuation_hand_ordinal = self._hand_ordinal(self.var_target.get())
            try:
                self.ctrl.save_entry_plan()
            except Exception as error:
                self.ctrl._pause_entry_recovery(f"录入位置未能保存，请核对：{error}")
                self.fail(error)
        self.refresh_actions()
        self.refresh_entry_prompt()

    def _set_recording_target(self, seat: str, hand_label: str | None = None) -> None:
        self._syncing_target = True
        try:
            self.var_target.set(seat)
            if hand_label:
                self.var_hand.set(hand_label)
        finally:
            self._syncing_target = False

    def _on_manual_command(self, command) -> None:
        if hasattr(self, 'compact_panel') and self.compact_panel.editor_open:
            return  # Consume recording keys outside the editor without invoking buttons.
        dispatch = {
            KIND_RANK: lambda: self._key_rank(command.rank),
            KIND_HOLE: self._key_hole,
            KIND_NEXT: lambda: self._key_navigate(1),
            KIND_PREV: lambda: self._key_navigate(-1),
            KIND_JUMP: lambda: self._key_jump(command.seat_number),
            KIND_HIT: lambda: self.act_action(ACTION_HIT),
            KIND_STAND: self._key_stand,
            KIND_DOUBLE: lambda: self.act_action(ACTION_DOUBLE),
            KIND_SPLIT: lambda: self.act_action(ACTION_SPLIT),
            KIND_PAUSE: self._key_pause,
            KIND_UNDO: self.act_undo,
        }
        action = dispatch.get(command.kind)
        if action:
            action()

    def _shortcut(self, event, action):
        if not self._is_recording_surface(event):
            return
        action()
        return "break"

    def _restore_plan_identity(self) -> None:
        plan = self.ctrl.entry_plan
        if plan is None:
            return
        self.var_simple_hole.set(plan.simple_hole)
        if plan.participating_seats:
            for name, var in self.var_participants.items():
                var.set(name in plan.participating_seats)
        if plan.my_seat:
            self.var_my_seat.set(plan.my_seat)
            self.var_analysis_target.set(plan.my_seat)
        if plan.deal_direction:
            self.var_deal_direction.set(plan.deal_direction)
        self._sync_from_plan()

    def _sync_from_plan(self) -> None:
        plan = self.ctrl.entry_plan
        if plan is None:
            return
        if plan.mode == MODE_INITIAL:
            slot = plan.slot()
            if slot:
                self._set_recording_target(slot.seat)
        elif plan.mode == MODE_PEEK_WAIT:
            self._set_recording_target(DEALER)
        elif plan.continuation_seat:
            self._set_recording_target(plan.continuation_seat)
            if plan.continuation_hand_id:
                self._preferred_recording_hand_id = plan.continuation_hand_id

    def _requires_peek(self, up_rank):
        seg = self._current_seg()
        return dealer_up_requires_peek(seg.rules if seg else None, up_rank)

    def _note_shown(self, seat, event, rank, slot_id=None) -> None:
        # The controller has already synchronized and persisted the plan before
        # publishing the event. These methods only select the visible focus.
        plan = self.ctrl.entry_plan
        if plan and (slot_id or plan.mode in (MODE_CONTINUATION, MODE_DEALER)
                     or plan.simple_hole and plan.mode in (MODE_INITIAL, MODE_PEEK_WAIT)):
            self._sync_from_plan()

    def _note_hidden(self, seat, event, slot_id=None) -> None:
        self._sync_from_plan()

    def _hand_ordinal(self, seat: str) -> int:
        seg = self._current_seg()
        if seg is None:
            return 1
        hand_id = self._selected_hand_id(seg)
        seat_state = seg.table.seat(seat) if (seat == DEALER or seat in seg.table.players) else None
        if not seat_state:
            return 1
        for index, hand in enumerate(seat_state.hands, start=1):
            if hand.hand_id == hand_id:
                return index
        return max(len(seat_state.hands), 1)

    @tracked_operation
    def _key_rank(self, rank: str) -> None:
        try:
            self._ensure_recording_enabled(key=True, card=True)
            plan = self.ctrl.entry_plan
            slot_id = None
            if self.var_mode.get() != "揭示" and plan and plan.mode in (MODE_INITIAL, MODE_MANUAL):
                slot = plan.slot()
                if slot and slot.expected_face != "shown":
                    raise TableError("当前槽位是庄家暗牌，不能用点值键代替暗牌确认")
                if slot and slot.expected_face == "shown" and slot.slot_id not in plan.filled_slots:
                    if not plan.paused:
                        slot = plan.accept_shown_on_cursor()
                        slot_id = slot.slot_id
                        self._set_recording_target(slot.seat)
                    elif slot.seat == self.var_target.get():
                        slot_id = slot.slot_id
            self._pending_slot_id = slot_id
            self.act_card(rank)
        except Exception as error:
            self.fail(error)
        finally:
            self._pending_slot_id = None

    @tracked_operation
    def _key_hole(self) -> None:
        try:
            self._ensure_recording_enabled(key=True)
            plan = self.ctrl.entry_plan
            if plan is None:
                raise TableError("尚未冻结本轮发牌计划")
            slot = plan.accept_hole_on_cursor()
            self._set_recording_target(slot.seat)
            self._pending_slot_id = slot.slot_id
            self.act_hidden_card()
        except Exception as error:
            self.fail(error)
        finally:
            self._pending_slot_id = None

    @tracked_operation
    def _key_navigate(self, step: int) -> None:
        try:
            plan = self.ctrl.entry_plan
            if plan is None:
                raise TableError("尚未冻结本轮发牌计划")
            if plan.mode in (MODE_CONTINUATION, MODE_DEALER):
                self._move_recording_hand(step)
            else:
                slot = plan.navigate(step)
                if slot:
                    self._set_recording_target(slot.seat)
                    plan.continuation_seat = slot.seat
                    plan.continuation_hand_id = None
                self.ctrl.save_entry_plan()
            self.refresh_all()
        except Exception as error:
            self.fail(error)

    def _move_recording_hand(self, step: int) -> None:
        seg = self._current_seg()
        plan = self.ctrl.entry_plan
        if seg is None or plan is None:
            return
        targets = [(name, hand.hand_id, index)
                   for name in (*plan.participating_seats, DEALER)
                   for index, hand in enumerate(seg.table.seat(name).hands, 1)]
        current = (self.var_target.get(), self._selected_hand_id(seg))
        index = next((i for i, target in enumerate(targets) if target[:2] == current), 0)
        if targets:
            seat, hand_id, ordinal = targets[(index + step) % len(targets)]
            plan.mode = MODE_CONTINUATION
            plan.continuation_seat = seat
            plan.continuation_hand_id = hand_id
            plan.continuation_hand_ordinal = ordinal
            self._set_recording_target(seat)
            self._preferred_recording_hand_id = hand_id
            self.ctrl.save_entry_plan()

    @tracked_operation
    def _key_jump(self, number: int) -> None:
        try:
            seat = DEALER if number == 0 else player_seat_name(number)
            plan = self.ctrl.entry_plan
            if plan:
                plan.jump_seat(seat)
                self.ctrl.save_entry_plan()
            self._set_recording_target(seat)
            self.refresh_all()
            self._on_recording_hand_clicked()
        except Exception as error:
            self.fail(error)

    @tracked_operation
    def _key_pause(self) -> None:
        try:
            plan = self.ctrl.entry_plan
            if plan is None:
                return
            plan.toggle_input_pause()
            self.ctrl.save_entry_plan()
            self.refresh_all()
        except Exception as error:
            self.fail(error)

    @tracked_operation
    def _key_stand(self) -> None:
        self.act_action(ACTION_STAND)
        self._sync_from_plan()
        self.refresh_all()

    def _ensure_recording_enabled(self, *, key=False, action=False, card=False) -> None:
        plan = self.ctrl.entry_plan
        if plan and (plan.input_paused or key and plan.mode == MODE_UNALIGNED):
            raise TableError("录入已暂停；请恢复录入或先核对已保存记录，不要重复录牌")
        automatic_reveal = (card and self.var_mode.get() != '揭示'
                            and self.ctrl.simple_hole_active() and self.var_target.get() == DEALER
                            and self.ctrl.simple_dealer_route(self.var_target.get(), self._selected_hand_id(self._current_seg())))
        if plan and plan.mode == MODE_PEEK_WAIT and self.var_mode.get() != "揭示" and not automatic_reveal:
            raise TableError("等待实际庄家检查结果，不能继续录入玩家牌")
        if (plan and plan.mode == MODE_CONTINUATION and plan.initial_complete()
                and (action or self.var_mode.get() != "揭示")):
            seg = self._current_seg()
            acting = next_open_hand(seg.table, plan.participating_seats)
            selected = (self.var_target.get(), self._selected_hand_id(seg))
            if acting and selected != acting[:2]:
                raise TableError(f"当前实际行动位置：{acting[0]}／第{acting[2]}手；导航不会提前行动")

    @tracked_operation
    def act_manual_alignment(self) -> None:
        plan = self.ctrl.entry_plan
        if plan is None:
            return
        if not messagebox.askyesno("核对后人工继续", "请先核对牌桌和下方已保存事件。已保存的牌不要重录。\n"
                                  "确认后仅按你选择的座位、手牌人工录入，直到下一轮重建自动队列。", parent=self):
            return
        try:
            plan.mode = MODE_MANUAL
            plan.simple_hole = False
            plan.slots = ()
            plan.participating_seats = ()
            plan.cursor_slot_id = None
            plan.filled_slots = {}
            plan.unresolved_slots = []
            plan.paused = True
            plan.input_paused = False
            plan.input_pause_reason = ""
            plan.pause_reason = "已确认人工录入；自动队列将在下一轮重新建立"
            plan.observed_card_ids = self.ctrl.live_card_event_ids()
            self.ctrl.entry_warning = ""
            self.ctrl.save_entry_plan()
            self.refresh_all()
        except Exception as error:
            self.fail(error)

    def _analysis_hand_id(self, seg):
        seat_name = self.var_analysis_target.get()
        if seg is None or (seat_name != DEALER and seat_name not in seg.table.players):
            return None
        seat = seg.table.seat(seat_name)
        if self.var_analysis_hand.get() == "（按顺序行动手）":
            return next((h.hand_id for h in seat.hands if not seg.table.split_hand_closed(h)),
                        seat.hands[-1].hand_id if seat.hands else None)
        if self.var_analysis_hand.get() in ("", "（最新一手）"):
            return seat.hands[-1].hand_id if seat.hands else None
        return self._analysis_hand_ids.get(self.var_analysis_hand.get())


    # ============================================================
    # 动作
    # ============================================================
    def _set_rule_form(self, rules):
        self.var_decks.set(rules.n_decks)
        self.var_s17.set(rules.dealer_soft17 or '未知')
        self.var_bjp.set(':'.join(map(str, rules.blackjack_payout)) if rules.blackjack_payout else '未知')
        self.var_split_match.set('same_value 同点值' if rules.split_match == 'same_value' else 'same_rank 同牌面')
        self.var_das.set({True: '允许', False: '禁止', None: '未知'}[rules.double_after_split])
        self.var_surrender.set(rules.surrender or '不支持')
        self.var_confirm.set(rules.confirm_status)
        excluded = {'n_decks', 'dealer_soft17', 'blackjack_payout', 'split_match', 'double_after_split', 'surrender', 'confirm_status'}
        self.rule_details = {k: v for k, v in asdict(rules).items() if k not in excluded}

    def _load_common_settings(self):
        # A user-selected local preset, never a fallback for imported unknown rules.
        rules = same_value_das_research_rules(8)
        rules.vendor = '本机常用设置'
        rules.rule_source = '用户指定S17/同值分牌/晚投降/允许加倍；其余沿用本机研究模板'
        self._set_rule_form(rules)
        self.var_simple_hole.set(True)
        self.var_status.set('常用设置已就绪：8副 / S17 / 同值分牌 / 晚投降 / 允许加倍（含非A分后加倍）/ 简便暗牌。')

    @tracked_operation
    def act_common_settings(self):
        self._load_common_settings()
        self.set_status(self.var_status.get() + '\n新牌盒使用这套桌规；简便暗牌从下一轮生效，当前已录牌局保持原设置。')
        self.compact_panel.render()

    @tracked_operation
    def act_research_template(self, split=False, das=False, same_value=False):
        if das and same_value:
            rules = same_value_das_research_rules(self.var_decks.get())
            scope = "同点值配对（含T/T）；两手顺序分牌，非A允许DAS。"
        elif split and same_value:
            rules = same_value_split_research_rules(self.var_decks.get())
            scope = "同点值配对（含T/T）；两手顺序分牌，无DAS。"
        elif das:
            rules = das_research_rules(self.var_decks.get())
            scope = "两手顺序分牌，首手完成后才给第二手补牌；非A允许DAS/无再分/分A一张。"
        elif split:
            rules = split_research_rules(self.var_decks.get())
            scope = "两手顺序分牌，首手完成后才给第二手补牌；无DAS/无再分/分A一张。"
        else:
            rules = research_rules(self.var_decks.get())
            scope = "原单手分析，保留四手录牌规则。"
        self._set_rule_form(rules)
        self.set_status("已载入自建研究模板（非平台桌规）：" + scope + "请新建牌靴使用；当前规则快照不变。")

    def act_experiments(self):
        if self.experiment_window is not None and self.experiment_window.winfo_exists():
            self.experiment_window.lift()
            return
        from .experiment_panel import ExperimentWindow
        self.experiment_window = ExperimentWindow(self)

    @tracked_operation
    def act_refresh(self):
        try:
            self.refresh_all()
            self.set_status("已从账本刷新界面，请核对最新记录")
        except Exception as error:
            self.fail(error)

    @tracked_operation
    def act_diagnose(self):
        sessions = self.ctrl.list_recoverable()
        if not sessions:
            return
        lines = [f"{i + 1}. {s['session_id'][:12]} / {s['event_count']}条" for i, s in enumerate(sessions)]
        choice = simpledialog.askinteger("只读诊断旧会话", "\n".join(lines) + "\n选择序号（不会更改记录）：", parent=self, minvalue=1, maxvalue=len(sessions))
        if not choice:
            return
        session_id = sessions[choice - 1]["session_id"]
        result = self.ctrl.store.diagnose_session(session_id)
        messagebox.showinfo("只读诊断", result["error"] or "事件可重放；是否可分析仍需核对规则与信息条件。", parent=self)
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".json", initialfile="raw_session_diagnostic.json", filetypes=[("原始诊断", "*.json")])
        if path:
            try:
                self.ctrl.export_diagnostic(session_id, path)
                self.set_status("已原样导出数据库行供核对；未修改原会话，不将非法旧记录用于分析")
            except Exception as error:
                self.fail(error)

    @tracked_operation
    def act_rule_details(self):
        """补充桌规用表单录入；当前牌靴继续使用其冻结快照。"""
        win = tk.Toplevel(self)
        win.title("规则详情（用于下个牌靴；当前牌靴快照不变）")
        win.transient(self)
        win.grab_set()
        definitions = [
            ("profile_id", "规则档案ID", None), ("version", "规则版本（整数）", None),
            ("vendor", "厂商", None), ("game_name", "游戏名", None),
            ("table_id", "桌标识", None), ("rule_source", "规则来源", None),
            ("verify_date", "核对日期", None), ("n_seats", "玩家座位上限", ["1", "2", "3", "4", "5", "6", "7"]),
            ("shoe_model", "牌靴模型", ["有限不放回", "每轮重置（未支持）", "未知"]),
            ("american_hole_card", "是否美式底牌", ["未知", "是", "否"]),
            ("check_bj_when", "Blackjack 检查时机", None),
            ("dealer_bj_extra_bet_rule", "庄家BJ追加注结算", ["未知", "全部注损失", "仅原注（未支持）"]),
            ("double_on_totals", "可加倍点数（逗号分隔，空为任意）", None),
            ("max_split_hands", "最大分牌手数", [str(n) for n in range(1, 9)]),
            ("resplit_aces", "是否允许再分A", ["未知", "是", "否"]),
            ("split_ace_hit_once", "分A是否只补一张", ["未知", "是", "否"]),
            ("split_deal_order", "分牌发牌顺序", ["未知", "首手完成后第二手", "先发两手第二张"]),
            ("start_from_new_shoe", "是否从新牌靴开始记录", ["未知", "是", "否"]),
            ("burn_cards_known", "是否已知烧牌数量（含零张）", ["未知", "是", "否"]),
            ("initial_burn_count", "初始烧牌数量（空为未知）", None),
            ("cut_shuffle_note", "切牌 / 洗牌约定", None), ("remark", "备注 / 素材来源", None),
        ]
        profile = self._build_rules()
        enum_maps = {
            "shoe_model": {"有限不放回": "finite_no_replacement", "每轮重置（未支持）": "per_round_reset", "未知": "unknown"},
            "dealer_bj_extra_bet_rule": {"未知": None, "全部注损失": "all_bets_lost", "仅原注（未支持）": "original_bets_only"},
            "split_deal_order": {"未知": None, "首手完成后第二手": "sequential_complete_first", "先发两手第二张": "both_second_cards_first"},
        }
        booleans = {"american_hole_card", "resplit_aces", "split_ace_hit_once", "start_from_new_shoe", "burn_cards_known"}
        variables = {}
        for i, (key, label, choices) in enumerate(definitions):
            row, column = i // 2, (i % 2) * 2
            ttk.Label(win, text=label).grid(row=row, column=column, sticky="w", padx=8, pady=5)
            value = getattr(profile, key)
            mapping = enum_maps.get(key, {"未知": None, "是": True, "否": False} if key in booleans else {})
            if mapping:
                value = next(k for k, v in mapping.items() if v == value)
            elif key == "double_on_totals":
                value = "" if value is None else ",".join(map(str, value))
            variable = tk.StringVar(value="" if value is None else str(value))
            variables[key] = variable
            widget = ttk.Combobox(win, textvariable=variable, values=choices, state="readonly", width=25) if choices else ttk.Entry(win, textvariable=variable, width=27)
            widget.grid(row=row, column=column + 1, padx=8, pady=5, sticky="ew")

        def save():
            try:
                details = {}
                for key, variable in variables.items():
                    value = variable.get().strip()
                    if key in booleans:
                        value = {"未知": None, "是": True, "否": False}[value]
                    elif key in enum_maps:
                        value = enum_maps[key][value]
                    elif key in ("version", "n_seats", "max_split_hands"):
                        value = int(value)
                    elif key == "initial_burn_count":
                        value = int(value) if value else None
                    elif key == "double_on_totals":
                        value = tuple(int(n.strip()) for n in value.replace("，", ",").split(",")) if value else None
                    else:
                        value = value or None
                    details[key] = value
                proposed = json.loads(self._build_rules().to_json())
                proposed.update(details)
                RuleProfile(**proposed)
                self.rule_details = details
                win.destroy()
                self.set_status("已更新下个牌靴的规则表单；当前牌靴仍沿用锁定快照")
            except Exception as exc:
                messagebox.showerror("规则无效", str(exc), parent=win)

        ttk.Label(win, text="分析支持原单手及显式两手顺序研究模板；未知字段或其他顺序不自动套用。", wraplength=800).grid(row=12, column=0, columnspan=4, padx=8, pady=8)
        ttk.Button(win, text="保存表单", command=save).grid(row=13, column=1, pady=8)
        ttk.Button(win, text="取消", command=win.destroy).grid(row=13, column=2, pady=8)

    def _selected_event(self):
        selection = self.lst_timeline.curselection()
        if not selection:
            raise LedgerError("请先在时间线选中一条事件")
        return self.ctrl.ledger.events[selection[0]]

    @tracked_operation
    def act_correct(self):
        try:
            event = self._selected_event()
            if event.etype in (CARD_DEALT, CARD_REVEALED):
                rank = simpledialog.askstring("修正牌面", "输入 A、2～10、J、Q、K、T；? 表示观察未知：", parent=self)
                if rank is None:
                    return
                rank = rank.strip().upper()
                if rank not in (*RANKS, TEN_BUCKET, UNKNOWN):
                    raise ValueError("请输入有效牌面")
                fix = {"rank": rank}
                if event.etype == CARD_DEALT:
                    fix.update(rank=None if rank == UNKNOWN else rank,
                               face_state=FACE_UNKNOWN if rank == UNKNOWN else "shown")
            elif event.etype == BURN_CARDS:
                count = simpledialog.askinteger("修正烧牌", "实际烧牌数量：", parent=self, minvalue=0)
                if count is None:
                    return
                fix = {"count": count}
            elif event.etype == OBSERVATION_GAP:
                if not messagebox.askyesno("缺口核对", "仅在实际记录已补齐或确认原缺口为误报时解除。是否已核对？", parent=self):
                    return
                fix = {"resolved": True}
            elif event.etype == PEEK_NEGATIVE:
                fix = {"invalidated": True}
            elif event.etype == ROUND_ENDED:
                observation = self.ask_observation_status()
                if observation is None:
                    return
                fix = {"observation_status": observation}
            else:
                raise LedgerError("此事件不能直接修正；可逆序撤销。修改规则请新建牌靴。")
            reason = simpledialog.askstring("纠错依据", "说明纠错原因 / 已核对的记录依据：", parent=self)
            if not reason:
                return
            self.ctrl.correct(event.event_id, fix, reason)
            self.refresh_all()
            self.set_status("已追加纠错并重算；原始事件仍保留。若后续操作与修正冲突，需先逆序撤销后续事件。")
        except Exception as exc:
            self.fail(exc)

    @tracked_operation
    def act_history(self):
        try:
            event = self._selected_event()
            replay = self.ctrl.ledger.replay(through_seq=event.seq)
            seg = replay.current
            lines = [f"截至事件 #{event.seq}，仅使用当时已经记录的信息。"]
            if seg:
                lines.extend([f"{seg.rules.n_decks}副，第{seg.table.round_no}轮，{seg.table.phase}",
                              f"物理剩余：{seg.shoe.physical_remaining()}；{self._record_status(seg)}"])
                for seat in [seg.table.dealer, *seg.table.players.values()]:
                    if seat.hands:
                        lines.append(seat.name + "：" + " / ".join(h.display() for h in seat.hands))
            messagebox.showinfo("历史时点（不使用后续揭示与纠错）", "\n".join(lines), parent=self)
        except Exception as exc:
            self.fail(exc)

    @tracked_operation
    def act_end_unsettled(self):
        reason = simpledialog.askstring("结束本轮（不输出结算）", "信息不足或结算规则未支持的原因：", parent=self)
        if reason:
            observation = self.ask_observation_status()
            if observation is None:
                return
            try:
                self.ctrl.end_round_unsettled(reason, observation)
                self.refresh_all()
                self.set_status("本轮已结束但未结算；未知牌与信息状态继续保留，可开下一轮")
            except Exception as exc:
                self.fail(exc)

    def ask_observation_status(self):
        return RoundObservationDialog(self, "确认本轮观察完整性").result

    @tracked_operation
    def act_import(self):
        path = filedialog.askopenfilename(filetypes=[("会话文件", "*.json *.csv")], parent=self)
        if path:
            try:
                self.ctrl.import_file(path)
                self._restore_plan_identity()
                self.refresh_all()
                self.set_status("已校验并导入会话；原有其他会话仍保留")
            except Exception as exc:
                self.fail(exc)

    @tracked_operation
    def act_recover(self):
        sessions = [s for s in self.ctrl.list_recoverable() if s["event_count"] > 0]
        labels = [f"{i + 1}. {s['session_id'][:12]} — {s['event_count']}条事件" for i, s in enumerate(sessions)]
        choice = simpledialog.askinteger("恢复会话", "\n".join(labels) + "\n输入会话序号：", minvalue=1, maxvalue=len(sessions), parent=self)
        if choice:
            try:
                self.ctrl.load_session(sessions[choice - 1]["session_id"])
                self._restore_plan_identity()
                self.refresh_all()
                self.set_status("已恢复选中会话，后续录入将追加到该会话")
            except Exception as exc:
                self.fail(exc)

    @tracked_operation
    def act_backup(self):
        path = filedialog.asksaveasfilename(defaultextension=".db", initialfile="blackjack_backup.db", filetypes=[("SQLite", "*.db")], parent=self)
        if path:
            try:
                self.ctrl.store.backup(path)
                self.set_status(f"数据库备份完成：{path}")
            except Exception as exc:
                self.fail(exc)

    @contextmanager
    def _view_frame(self):
        # Only read-only painting shares this snapshot. Ledger writes and
        # analysis inputs still replay independently through the controller.
        previous = getattr(self, '_view_snapshot', None)
        self._view_snapshot = previous or (self.ctrl.context_token, self.ctrl.state())
        try:
            yield
        finally:
            self._view_snapshot = previous

    def _view_state(self):
        snapshot = getattr(self, '_view_snapshot', None)
        if snapshot is None:
            return self.ctrl.state()
        if snapshot[0] != self.ctrl.context_token:
            snapshot = self._view_snapshot = (self.ctrl.context_token, self.ctrl.state())
        return snapshot[1]

    def _current_seg(self):
        return self._view_state().current

    def _selected_hand_id(self, seg) -> Optional[str]:
        seat_name = self.var_target.get()
        if seat_name != DEALER and seat_name not in seg.table.players:
            return None
        seat = seg.table.seat(seat_name)
        if self.var_hand.get() == "（按顺序行动手）":
            return next((h.hand_id for h in seat.hands if not seg.table.split_hand_closed(h)),
                        seat.hands[-1].hand_id if seat.hands else None)
        if self.var_hand.get() in ("", "（最新一手）"):
            return seat.hands[-1].hand_id if seat.hands else None
        return self._hand_ids.get(self.var_hand.get())

    def _suit(self):
        v = self.var_suit.get()
        return None if v == "未知" else v

    def _build_rules(self) -> RuleProfile:
        bjp = None if self.var_bjp.get() == "未知" else tuple(
            int(x) for x in self.var_bjp.get().split(":"))
        das = {"允许": True, "禁止": False, "未知": None}[self.var_das.get()]
        surr = None if self.var_surrender.get() == "不支持" else self.var_surrender.get()
        s17 = None if self.var_s17.get() == "未知" else self.var_s17.get()
        return RuleProfile(
            n_decks=self.var_decks.get(),
            dealer_soft17=s17, blackjack_payout=bjp,
            split_match="same_rank" if self.var_split_match.get().startswith("same_rank")
            else "same_value",
            double_after_split=das, surrender=surr,
            confirm_status=self.var_confirm.get(),
            **self.rule_details,
        )

    @tracked_operation
    def act_new_shoe(self) -> None:
        seg = self._current_seg()
        if seg and seg.table.phase in ("发牌中", "进行中"):
            messagebox.showerror("不能新建牌靴", "当前轮尚未结束")
            return
        try:
            self.ctrl.new_shoe(self._build_rules())
            self.set_status("新牌靴已创建并锁定规则快照")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_new_round(self) -> None:
        try:
            seg = self._current_seg()
            players = [name for name, var in self.var_participants.items() if var.get() and seg and name in seg.table.players]
            self.ctrl.start_round(players, my_seat=self.var_my_seat.get(),
                                  deal_direction=self.var_deal_direction.get(), simple_hole=self.var_simple_hole.get())
            if self.var_simple_hole.get():
                self.var_mode.set('新发牌')
            plan = self.ctrl.entry_plan
            if plan and plan.mode == MODE_INITIAL:
                first = plan.slot()
                if first:
                    self._set_recording_target(first.seat)
            self.var_analysis_target.set(self.var_my_seat.get())
            self.set_status("新一轮开始；初始发牌按玩家第一张→庄家明牌→玩家第二张→庄家暗牌")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def change_player_count(self, delta):
        from .round_players import first_pass_open, preview_players, apply_players
        seg = self._current_seg()
        current = first_pass_open(self.ctrl, seg)
        selected = list(seg.table.participants) if current else [name for name, var in self.var_participants.items() if var.get()]
        selected = [name for name in self.var_participants if name in selected]
        if delta > 0 and len(selected) < 7:
            selected.append(next(name for name in self.var_participants if name not in selected))
        elif delta < 0 and len(selected) > 1:
            target = selected[-1] if current else next(name for name in reversed(selected) if name != self.var_my_seat.get())
            selected.remove(target)
        else:
            return
        try:
            if current:
                preview = preview_players(self.ctrl, selected)
                changed_my_seat = preview.my_seat != self.ctrl.entry_plan.my_seat
                if preview.moved or preview.add_hole or changed_my_seat:
                    changes = [f'第{i}张 {rank}：{before} → {after}' for i, rank, before, after in preview.moved]
                    if changed_my_seat:
                        changes.append(f'原本人座位已移除，本人分析座位改为{preview.my_seat}。')
                    if preview.add_hole:
                        changes.append('初始可见牌已录齐：按简便暗牌约定登记一张未知底牌。')
                    if not messagebox.askyesno('调整本轮人数',
                            f'本轮改为 {len(selected)} 人，按已录入顺序继续发牌。\n\n' + '\n'.join(changes)
                            + '\n\n确认这些牌的归属？取消则保持原记录。', parent=self):
                        return
                apply_players(self.ctrl, preview)
                for name, var in self.var_participants.items():
                    var.set(name in selected)
                self._preferred_recording_hand_id = None
                self.var_hand.set('（最新一手）')
                self.var_analysis_hand.set('（按顺序行动手）')
                self._restore_plan_identity()
                self.set_status(f'本轮已改为 {len(selected)} 人；' +
                    ('发牌位置需核对，请勿重复录入。' if self.ctrl.entry_plan.paused else '已录牌按原顺序保留，继续输入下一张。'))
                self.refresh_all()
            else:
                for name, var in self.var_participants.items():
                    var.set(name in selected)
                if self.var_my_seat.get() not in selected:
                    self.var_my_seat.set(selected[0])
                self.set_status('下一轮人数已调整；本轮已超过第一遍发牌，已保存记录保持原归属。')
                self.compact_panel.render()
        except Exception as error:
            self.fail(error)

    def change_simple_hole(self):
        if self.var_simple_hole.get() and not messagebox.askyesno('启用简便暗牌录入',
                '从下一轮开始，按已确认的固定美式发牌流程录牌。\n\n'
                '录完初始明牌，代表你确认初始发牌已完成，包含那张背面朝上的庄家底牌。'
                '程序会登记未知底牌；开牌时直接输入点数。\n\n'
                '中途加入、漏录或顺序不明时请改用人工核对。非BJ检查仍需按实际情况单独确认。\n\n'
                '确认采用这一录入约定？', parent=self):
            self.var_simple_hole.set(False)
        self.set_status('简便暗牌设置将在下一轮生效；本轮已保存记录不变。')
        self.compact_panel.render()

    @tracked_operation
    def act_card(self, rank: str) -> None:
        try:
            self._ensure_recording_enabled(card=True)
            finished = self.dealer_recording_finished()
            if finished:
                self.set_status(finished + '；请确认本轮完整后结算，或使用改牌纠正记录。')
                return
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴")
            hand_id = self._selected_hand_id(seg)
            automatic_target = (self.ctrl.simple_dealer_route(self.var_target.get(), hand_id)
                                if self.var_mode.get() != '揭示' else None)
            if self.var_mode.get() == "揭示" or automatic_target:
                target = self.ctrl.ledger._find(automatic_target) if automatic_target else self._find_hidden_deal_event(seg, hand_id)
                if target is None:
                    raise TableError("该手牌没有待揭示的暗牌/未知牌")
                self.ctrl.reveal(target.event_id, rank, self._suit())
                if automatic_target:
                    self._sync_from_plan()
                self.set_status(f"暗牌揭示为 {rank}（未重复扣牌，只做揭示转换）")
            else:
                saved_seat = self.var_target.get()
                event = self.ctrl.deal_shown(saved_seat, rank,
                                     hand_id=hand_id, suit=self._suit(), initial_slot_id=self._pending_slot_id)
                self._note_shown(saved_seat, event, rank, self._pending_slot_id)
                self.set_status(f"录入 {saved_seat} <- {rank}" +
                                ('；已按确认流程登记未知底牌' if self.ctrl.ledger.events[-1].seq > event.seq else ''))
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_hidden_card(self) -> None:
        try:
            self._ensure_recording_enabled()
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴")
            event = self.ctrl.deal_hidden(self.var_target.get(),
                                  self._selected_hand_id(seg))
            self._note_hidden(self.var_target.get(), event, self._pending_slot_id)
            self.set_status("已录入一张暗牌（牌面未知，物理已离靴，待揭示）")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_unknown_card(self) -> None:
        try:
            self._ensure_recording_enabled()
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴")
            self.ctrl.deal_unknown(self.var_target.get(),
                                   self._selected_hand_id(seg))
            self.set_status("已登记未知牌面（待核对，完整性转为“待核对”）")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_action(self, action: str) -> None:
        try:
            self._ensure_recording_enabled(action=True)
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴与轮次")
            hand_id = self._selected_hand_id(seg)
            if not hand_id:
                raise TableError("目标座位还没有手牌")
            ev = self.ctrl.player_action(self.var_target.get(), hand_id, action)
            note = f"动作：{action}"
            if action == ACTION_SPLIT:
                note += f"，新手牌 {ev.payload.get('new_hand_id', '')}"
            self._sync_from_plan()
            self.set_status(note)
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_peek_negative(self) -> None:
        try:
            self.ctrl.peek_negative()
            self._sync_from_plan()
            self.set_status("已记录：庄家检查底牌，确认不是 Blackjack")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    def _next_round_options(self):
        return dict(participants=[name for name, var in self.var_participants.items() if var.get()],
                    my_seat=self.var_my_seat.get(), deal_direction=self.var_deal_direction.get(),
                    simple_hole=self.var_simple_hole.get())

    @tracked_operation
    def act_complete_and_next(self, expected_round_id):
        try:
            self.ctrl.complete_and_next_round(expected_round_id, **self._next_round_options())
            if self.var_simple_hole.get():
                self.var_mode.set('新发牌')
            self._sync_from_plan()
            self.var_analysis_target.set(self.var_my_seat.get())
            self.set_status('上轮已结算；已开始下一轮，继续同一牌靴。')
            self.refresh_all()
        except Exception as error:
            self.fail(error)

    @tracked_operation
    def act_end_round(self) -> None:
        try:
            _, results = self.ctrl.end_round()
            if results:
                txt = "\n".join(
                    f"{r['seat']} {r['hand_id'].split('-')[-1]}："
                    f"{r['result']}，净收益 {r['net_units']:+.2f} 单位"
                    for r in results)
            else:
                txt = "本轮没有已参与的玩家手牌。"
            self.set_status("本轮已结算，可“新开一轮”继续同一牌靴")
            self.refresh_all()
            messagebox.showinfo("本轮结算（确定性记账，非 EV）", txt)
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_end_shoe(self) -> None:
        try:
            self.ctrl.end_shoe()
            self.set_status("牌靴已结束；再点“新建牌靴”将开启全新一靴，不与旧靴混合")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_undo(self) -> None:
        try:
            self.ctrl.undo_last()
            self._sync_from_plan()
            self.set_status("已追加撤销事件（原始记录保留，重放后该事件失效）")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_burn(self) -> None:
        try:
            n = simpledialog.askinteger("登记烧牌", "烧牌数量（牌面未知、数量已知）：",
                                        parent=self, minvalue=1, maxvalue=60)
            if n:
                self.ctrl.burn(n)
                self.set_status(f"已登记烧牌 {n} 张（牌面未知，单独计数）")
                self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_gap(self) -> None:
        reason = simpledialog.askstring("标记观察缺口",
                                        "缺口原因（断流/漏牌/未知数量烧牌等）：", parent=self)
        if reason:
            try:
                self.ctrl.mark_gap(reason)
                self.set_status("已标记观察缺口：相应精确分析已停用，旧结果过期")
                self.refresh_all()
            except Exception as e:
                self.fail(e)

    @tracked_operation
    def act_export_json(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".json", initialfile="blackjack_session.json",
            filetypes=[("JSON", "*.json")])
        if not path:
            return
        try:
            export_json(self.ctrl.ledger, path, session_name=self.ctrl.session_name)
            self.set_status(f"已导出 JSON：{path}")
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_export_csv(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", initialfile="blackjack_events.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            export_csv(self.ctrl.ledger, path)
            self.set_status(f"已导出事件 CSV：{path}")
        except Exception as e:
            self.fail(e)

    # ============================================================
    # 刷新
    # ============================================================
    def refresh_all(self) -> None:
        with self._view_frame():
            self._refresh_all()

    def _refresh_all(self) -> None:
        replay = self._view_state()
        seg = replay.current
        self.refresh_hands(seg)
        self.refresh_table(seg)
        self.refresh_actions(seg)
        self.refresh_composition(seg, replay)
        self.refresh_timeline()
        self.refresh_topinfo(seg, replay)
        self.refresh_entry_prompt()
        warning = getattr(self.ctrl, "entry_warning", "")
        if warning:
            self.set_status(warning)
        if hasattr(self, 'compact_panel'):
            self.compact_panel.render()

    def recording_inactive_message(self) -> Optional[str]:
        """Derive the prompt from replay, so undo/recovery cannot retain an ended target."""
        seg = self._current_seg()
        if seg is None:
            return "尚未创建牌靴；请先新建牌靴。"
        if seg.closed:
            return "牌靴已结束；请新建牌靴后再录牌。"
        if seg.table.phase == PHASE_NO_ROUND:
            return "尚未开轮；确认参与座位后点击“新开一轮”。"
        if seg.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
            return f"本轮{seg.table.phase}；点击“新开一轮”继续同一牌靴。"
        return None

    def dealer_recording_finished(self):
        if self.var_target.get() != DEALER or self.var_mode.get() == '揭示':
            return ''
        seg = self._current_seg()
        return dealer_finish_message(seg.table) if seg and not seg.closed else ''

    def refresh_entry_prompt(self) -> None:
        inactive = self.recording_inactive_message()
        if inactive:
            self.var_entry_prompt.set(inactive)
            return
        plan = self.ctrl.entry_plan
        if plan is None:
            self.var_entry_prompt.set("尚未冻结本轮发牌计划。确认参与座位和本人座位后开新一轮。")
            return
        finished = self.dealer_recording_finished()
        if finished and not plan.paused and not plan.input_paused:
            self.var_entry_prompt.set(finished + '；无需再录入庄家牌。\n确认本轮记录完整后结算。')
            return
        recording = self.var_target.get()
        if plan.mode == MODE_CONTINUATION:
            recording += f"／第{self._hand_ordinal(self.var_target.get())}手"
        prompt = plan.prompt(recording, self.var_analysis_target.get())
        if self.var_mode.get() != '揭示' and self.ctrl.simple_hole_active() and self.var_target.get() == DEALER:
            try:
                target = self.ctrl.simple_dealer_route(DEALER, self._selected_hand_id(self._current_seg()))
                if target:
                    lines = prompt.splitlines()
                    lines[0] = '庄家开牌：请输入底牌点数'
                    lines[3] = ('未确认非BJ；可输入实际揭示底牌，或按实际检查结果确认非BJ'
                                if plan.mode == MODE_PEEK_WAIT else '先揭示原底牌；之后输入的牌才是新补牌')
                    prompt = '\n'.join(lines)
            except TableError as error:
                prompt = '需要人工核对：' + str(error) + '\n' + prompt
        self.var_entry_prompt.set(prompt)

    def refresh_topinfo(self, seg, replay) -> None:
        shoe_no = len(replay.segments)
        if seg is None:
            self.var_topinfo.set(
                "牌靴 #0｜轮次 -｜完整性 -｜分析：单手 / 显式两手顺序模板")
            return
        ok, note = seg.shoe.conservation_check()
        self.var_topinfo.set(
            f"锁定 {seg.rules.n_decks}副/{seg.rules.dealer_soft17 or '未知'}｜牌靴 #{shoe_no}｜第 {seg.table.round_no} 轮｜"
            f"阶段 {'牌靴已结束' if seg.closed else seg.table.phase}｜记录：{self._record_status(seg)}｜"
            f"守恒：{'正常' if ok else '异常'}｜"
            f"规则确认：{seg.rules.confirm_status}｜分析：" + (
                "两手同点值DAS" if seg.rules.profile_id == SAME_VALUE_DAS_PROFILE
                else "两手同点值分牌" if seg.rules.profile_id == SAME_VALUE_SPLIT_PROFILE
                else "两手DAS模型" if seg.rules.profile_id == DAS_PROFILE
                else "两手顺序模型" if seg.rules.profile_id == SPLIT_PROFILE
                else "原单手模型"))

    def refresh_hands(self, seg=None) -> None:
        if seg is None:
            seg = self._current_seg()
        values = ["（按顺序行动手）", "（最新一手）"]
        special = self.var_hand.get() if self.var_hand.get() in values else "（最新一手）"
        profile_id = seg.rules.profile_id if seg else None
        if profile_id in ALL_SPLIT_PROFILES and getattr(self, "_hand_profile_id", None) != profile_id:
            special = "（按顺序行动手）"
            self.var_analysis_hand.set(special)
        self._hand_profile_id = profile_id
        selected_id = self._hand_ids.get(self.var_hand.get())
        if self._preferred_recording_hand_id:
            selected_id = self._preferred_recording_hand_id
            self._preferred_recording_hand_id = None
        self._hand_ids = {}
        if seg and (self.var_target.get() == DEALER or self.var_target.get() in seg.table.players):
            seat = seg.table.seat(self.var_target.get())
            for h in seat.hands:
                label = f"{h.hand_id}｜{h.display()}"
                values.append(label)
                self._hand_ids[label] = h.hand_id
        self.cmb_hand.configure(values=values)
        self.var_hand.set(next((label for label, hid in self._hand_ids.items() if hid == selected_id), special))
        self._refresh_analysis_hands(seg, special)

    def _refresh_analysis_hands(self, seg, special: str) -> None:
        analysis_values = ["（按顺序行动手）", "（最新一手）"]
        self._analysis_hand_ids = {}
        analysis_seat = self.var_analysis_target.get()
        if seg and (analysis_seat == DEALER or analysis_seat in seg.table.players):
            seat = seg.table.seat(analysis_seat)
            for h in seat.hands:
                label = f"{h.hand_id}｜{h.display()}"
                analysis_values.append(label)
                self._analysis_hand_ids[label] = h.hand_id
        panel = getattr(self, "analysis_panel", None)
        cmb = getattr(panel, "cmb_analysis_hand", None) if panel else None
        if cmb is not None:
            current = self.var_analysis_hand.get()
            cmb.configure(values=analysis_values)
            if current not in analysis_values:
                self.var_analysis_hand.set(special if special in analysis_values else "（最新一手）")

    def refresh_table(self, seg) -> None:
        if seg is None:
            self.lbl_dealer.configure(text="（尚未创建牌靴）")
            for name in self.seat_labels:
                self.seat_labels[name].configure(text="（空座）")
            return
        d = seg.table.dealer
        self.lbl_dealer.configure(
            text=('\n'.join(h.display() for h in d.hands) or '（本轮未发牌）')
                 + ('\n' + dealer_finish_message(seg.table) if dealer_finish_message(seg.table) else ''))
        for name, lbl in self.seat_labels.items():
            seat = seg.table.players.get(name)
            if seat is None:
                lbl.configure(text="（规则未启用此座位）")
                continue
            if not seat.hands:
                txt = "（空座）"
            else:
                blocks = []
                for h in seat.hands:
                    prefix = "└分牌手 " if h.from_split else "手牌 "
                    blocks.append(prefix + h.display())
                txt = "\n".join(blocks)
            target = "【当前选中】" if self.var_target.get() == name else ""
            lbl.configure(text=f"{target}\n{txt}")

    def refresh_actions(self, seg=None) -> None:
        if seg is None:
            seg = self._current_seg()
        if hasattr(self, "analysis_panel"):
            self.analysis_panel.on_context(seg)
        stopped = bool(self.dealer_recording_finished())
        for button in self.workbench_card_buttons:
            button.state(['disabled'] if stopped else ['!disabled'])
        for button in (self.btn_stand, self.btn_double, self.btn_split, self.btn_surr):
            button.state(["disabled"])
        if seg is None:
            seg = self._current_seg()
        if seg is None:
            self.var_legal.set("尚未创建牌靴")
            return
        hand_id = self._selected_hand_id(seg)
        if not hand_id:
            self.var_legal.set("该座位尚无手牌")
            return
        try:
            states = seg.table.action_states(self.var_target.get(), hand_id)
            lines = list(dict.fromkeys(item.reason for item in states.values() if not item.allowed))[:2]
            self.var_legal.set("\n".join(lines))
            enabled = {self.btn_stand: states[ACTION_STAND].allowed,
                       self.btn_double: states[ACTION_DOUBLE].allowed,
                       self.btn_split: states[ACTION_SPLIT].allowed,
                       self.btn_surr: states[ACTION_SURRENDER].allowed}
            for btn, ok in enabled.items():
                btn.state(["!disabled"] if ok else ["disabled"])
        except TableError as e:
            self.var_legal.set(str(e))

    def refresh_composition(self, seg, replay) -> None:
        self.txt_comp.configure(state=tk.NORMAL)
        self.txt_comp.delete("1.0", tk.END)
        if seg is None:
            self.txt_comp.insert(tk.END, "尚未创建牌靴。")
            self.txt_comp.configure(state=tk.DISABLED)
            return
        shoe = seg.shoe
        phys = shoe.physical_remaining()
        groups = shoe.group_remaining()
        ginit = shoe.group_initial()
        lines = [
            f"{shoe.n_decks} 副牌，总 {shoe.total_cards} 张",
            f"物理剩余：{'未知（存在缺口）' if phys is None else phys}",
            f"已发未知牌：{shoe.unrevealed_out}｜待核对：{shoe.pending_candidates}\n"
            f"烧牌(未知面)：{shoe.burn_unknown}｜T未细分：{shoe.t_bucket_out}",
            "-" * 40,
            "未确认移除的牌面仍包含在下列账面数中：",
            f"小牌2-6：账面 {groups['small']}/{ginit['small']}",
            f"中性7-9：账面 {groups['neutral']}/{ginit['neutral']}",
            f"大牌A/T：账面 {groups['big']}/{ginit['big']}",
            "原牌面账含未细分T、暗牌与烧牌；不等于待发组成。",
            "-" * 40,
        ]
        for r in RANKS:
            lines.append(f"{r:>2}: {shoe.remaining[r]:>3}  ", )
        lines.append("-" * 40)
        ok, note = shoe.conservation_check()
        lines.append(f"完整性：{shoe.integrity_state()}")
        lines.append(f"守恒校验：{note}")
        self.txt_comp.insert(tk.END, "\n".join(lines))
        self.txt_comp.configure(state=tk.DISABLED)

    def refresh_timeline(self) -> None:
        selected = self.lst_timeline.curselection()
        self.lst_timeline.delete(0, tk.END)
        voided = self.ctrl.ledger._voided_ids()
        for ev in self.ctrl.ledger.events:
            p = {k: v for k, v in ev.payload.items()
                 if not k.startswith("_") and k not in ("rules_snapshot",)}
            tag = "（已撤销）" if ev.event_id in voided else ""
            self.lst_timeline.insert(
                tk.END, f"#{ev.seq:<3} {ev.etype:<16} [{ev.source}/{ev.confirm_status}] {tag}{self._brief(p)}")
        if selected and selected[0] < self.lst_timeline.size():
            self.lst_timeline.selection_set(selected[0])
        else:
            self.lst_timeline.see(tk.END)

    @staticmethod
    def _brief(p: dict) -> str:
        keep = {}
        for k in ("seat", "rank", "action", "face_state", "count", "reason",
                  "n_decks", "round_no", "target_etype"):
            if k in p:
                keep[k] = p[k]
        return " ".join(f"{k}={v}" for k, v in keep.items())

    def _find_hidden_deal_event(self, seg, hand_id):
        candidates = [self.ctrl.ledger._find(eid) for eid, info in seg.unresolved.items()
                      if info["seat"] == self.var_target.get() and info["hand_id"] == hand_id
                      and info["round_id"] == seg.round_id]
        selected = self.lst_timeline.curselection()
        if selected:
            ev = self.ctrl.ledger.events[selected[0]]
            if any(e.event_id == ev.event_id for e in candidates):
                return ev
        if len(candidates) > 1:
            raise TableError("此手有多张未知牌，请先在时间线选中要揭示的发牌事件")
        return candidates[0] if candidates else None

    def _record_status(self, seg):
        plan = self.ctrl.entry_plan
        if (plan and plan.slots and plan.round_id == getattr(seg, "round_id", None)
                and (not plan.initial_complete() or plan.unresolved_slots)):
            return f"初始槽未齐 {len(plan.filled_slots)}/{len(plan.slots)}，不能宣称记录完整"
        status = seg.shoe.integrity_state()
        if status == "可分析":
            return "记录无已知缺口（分析资格另核对）"
        return status

    def on_close(self):
        self.window_layout.close()
        if self._key_binder is not None:
            self._key_binder.close()
        self.analysis_panel.close()
        if self.experiment_window is not None and self.experiment_window.winfo_exists():
            self.experiment_window.destroy()
        self.ctrl.close()
        self.destroy()

    # ============================================================
    # 杂项
    # ============================================================
    def set_status(self, msg: str) -> None:
        warnings = [getattr(self.ctrl, name, "") for name in ("entry_warning", "context_warning")]
        self.var_status.set("\n".join([msg, *filter(None, warnings)]))

    def fail(self, e: Exception) -> None:
        committed = self.ctrl.commit_revision > self._operation_start_revision
        message = ("数据已成功提交，但界面未同步。请刷新或重启，不要重复录牌。" if committed
                   else "操作未完成，未提交新的牌面事件。")
        messagebox.showerror("已保存 / 界面错误" if committed else "操作被拒绝", f"{message}\n{type(e).__name__}: {e}")
        self.var_status.set(f"{message} {e}")


def main() -> None:
    app = BlackjackLabApp()
    app.mainloop()


if __name__ == "__main__":
    main()
