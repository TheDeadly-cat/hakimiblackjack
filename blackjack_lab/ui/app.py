# -*- coding: utf-8 -*-
"""Hakimi Blackjack Lab —— V0.2a 手动录牌与单手分析工作台（Tkinter）。

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
    ACTION_DOUBLE, ACTION_SPLIT, ACTION_STAND, ACTION_SURRENDER, DEALER,
    TableError, player_seat_name,
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
from ..analysis.contracts import research_rules

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


class BlackjackLabApp(tk.Tk):
    def __init__(self, db_path: str | Path = DEFAULT_DB, recording_source=SOURCE_MANUAL):
        super().__init__()
        self.recording_source = recording_source
        self.title(f"Hakimi Blackjack Lab V{__version__} 手动记录工作台（本地离线）")
        self.geometry("1360x900")
        self.minsize(1180, 800)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        ttk.Style(self).configure("Card.TButton", font=("Consolas", 12), padding=2)

        self._ask_recover_if_any(db_path)
        self._operation_start_revision = self.ctrl.commit_revision
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        # 变量
        self.var_decks = tk.IntVar(value=6)
        self.var_s17 = tk.StringVar(value="未知")
        self.var_bjp = tk.StringVar(value="3:2")
        self.var_split_match = tk.StringVar(value="same_rank")
        self.var_das = tk.StringVar(value="未知")
        self.var_surrender = tk.StringVar(value="不支持")
        self.var_confirm = tk.StringVar(value=CONFIRM_UNKNOWN)
        self.var_target = tk.StringVar(value=player_seat_name(1))
        self.var_hand = tk.StringVar(value="（最新一手）")
        self.var_mode = tk.StringVar(value="新发牌")
        self.var_suit = tk.StringVar(value="未知")
        self.var_status = tk.StringVar(value="就绪：请先选择牌副数并新建牌靴")
        self.rule_details = {}
        self.var_participants = {name: tk.BooleanVar(value=name == "玩家1") for name in SEAT_NAMES[1:]}
        self._hand_ids = {}

        self._build_top()
        self._build_body()
        self._build_bottom()
        self._bind_keys()
        self.refresh_all()

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
        bar = ttk.LabelFrame(self, text="下一牌靴设置（当前锁定快照见状态行）")
        bar.grid(row=0, column=0, sticky="ew", padx=6, pady=4)

        ttk.Label(bar, text=f"V0.2a 手动录牌 · {self.recording_source}").grid(
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
        ttk.Button(bar, text="载入研究模板（新靴用）",
                   command=self.act_research_template).grid(row=0, column=4, padx=6)

        self.var_topinfo = tk.StringVar()
        ttk.Label(bar, textvariable=self.var_topinfo, foreground="#1a3c6e"
                  ).grid(row=2, column=0, columnspan=5, sticky="w", padx=4)

    def _build_body(self) -> None:
        body = ttk.Frame(self)
        body.grid(row=1, column=0, sticky="nsew", padx=4)

        # ---------- 左侧：录牌 ----------
        left = ttk.LabelFrame(body, text="左侧：手动录牌")
        left.pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)

        seat_box = ttk.LabelFrame(left, text="目标座位")
        seat_box.pack(fill=tk.X, padx=4, pady=3)
        for i, name in enumerate(SEAT_NAMES):
            ttk.Radiobutton(seat_box, text=name, value=name,
                            variable=self.var_target,
                            command=self.refresh_all).grid(row=i // 4, column=i % 4, sticky="w", padx=2)
        participants = ttk.LabelFrame(left, text="下一轮参与座位（空座不勾选）")
        participants.pack(fill=tk.X, padx=4, pady=3)
        for i, name in enumerate(SEAT_NAMES[1:]):
            ttk.Checkbutton(participants, text=name, variable=self.var_participants[name]).grid(
                row=i // 4, column=i % 4, sticky="w")

        hand_box = ttk.LabelFrame(left, text="目标手牌（分牌后选择）")
        hand_box.pack(fill=tk.X, padx=4, pady=3)
        self.cmb_hand = ttk.Combobox(hand_box, textvariable=self.var_hand,
                                     values=["（最新一手）"], state="readonly")
        self.cmb_hand.pack(fill=tk.X, padx=4, pady=2)
        self.cmb_hand.bind("<<ComboboxSelected>>", lambda e: self.refresh_actions())

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
        for i, r in enumerate(CARD_BUTTONS):
            ttk.Button(card_box, text=r, width=3, style="Card.TButton",
                       command=lambda x=r: self.act_card(x)
                       ).grid(row=i // 5, column=i % 5, padx=2, pady=2)
        ttk.Button(card_box, text="T 未细分", width=8,
                   command=lambda: self.act_card(TEN_BUCKET)
                   ).grid(row=3, column=0, columnspan=2, sticky="we", padx=2)
        ttk.Button(card_box, text="未知牌面", width=8,
                   command=self.act_unknown_card
                   ).grid(row=3, column=2, columnspan=3, sticky="we", padx=2)
        ttk.Button(card_box, text="暗牌（先不揭示）",
                   command=self.act_hidden_card
                   ).grid(row=4, column=0, columnspan=5, sticky="we", padx=2, pady=2)

        ttk.Label(left, text="快捷键：0=10｜T未细分｜Ctrl+Z撤销",
                  foreground="#555").pack(anchor="w", padx=6, pady=3)

        # ---------- 中部：牌桌 ----------
        mid = ttk.LabelFrame(body, text="中部：牌桌（庄家 + 7 座位）")
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4, pady=2)
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
            "V0.2a：概率 / 单手EV / 快照复盘\n"
            "分牌EV / 识别 / 捕获尚未支持"),
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
        self.analysis_tabs.add(self.analysis_panel, text="概率 / 单手EV")
        comp_box = ttk.Frame(self.analysis_tabs)
        self.analysis_tabs.add(comp_box, text="牌靴组成")
        self.txt_comp = tk.Text(comp_box, width=38, height=16, wrap=tk.WORD,
                                state=tk.DISABLED, font=("Consolas", 9))
        comp_scroll = ttk.Scrollbar(comp_box, command=self.txt_comp.yview)
        self.txt_comp.configure(yscrollcommand=comp_scroll.set)
        comp_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_comp.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=2, pady=2)

    def _build_bottom(self) -> None:
        bottom = ttk.LabelFrame(self, text="底部：轮次操作 / 事件时间线 / 纠错")
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

    def _bind_keys(self) -> None:
        keymap = {"a": "A", "2": "2", "3": "3", "4": "4", "5": "5",
                  "6": "6", "7": "7", "8": "8", "9": "9", "0": "10",
                  "j": "J", "q": "Q", "k": "K", "t": TEN_BUCKET}
        for k, rank in keymap.items():
            self.bind(f"<Key-{k}>", lambda e, r=rank: self._shortcut(e, lambda: self.act_card(r)))
        self.bind("<Key-x>", lambda e: self._shortcut(e, self.act_hidden_card))
        self.bind("<Key-g>", lambda e: self._shortcut(e, lambda: self.act_action(ACTION_STAND)))
        self.bind("<Key-b>", lambda e: self._shortcut(e, lambda: self.act_action(ACTION_DOUBLE)))
        self.bind("<Key-v>", lambda e: self._shortcut(e, lambda: self.act_action(ACTION_SPLIT)))
        self.bind("<Control-z>", lambda e: self._shortcut(e, self.act_undo))

    def _shortcut(self, event, action):
        if event.widget.winfo_toplevel() != self or event.widget.winfo_class() in ("Entry", "TEntry", "TCombobox", "Text", "Spinbox", "TSpinbox"):
            return
        action()
        return "break"

    # ============================================================
    # 动作
    # ============================================================
    @tracked_operation
    def act_research_template(self):
        rules = research_rules(self.var_decks.get())
        self.var_s17.set("S17")
        self.var_bjp.set("3:2")
        self.var_split_match.set("same_rank 同牌面")
        self.var_das.set("禁止")
        self.var_surrender.set("late")
        self.var_confirm.set(CONFIRM_VERIFIED)
        excluded = {"n_decks", "dealer_soft17", "blackjack_payout", "split_match", "double_after_split", "surrender", "confirm_status"}
        self.rule_details = {k: v for k, v in asdict(rules).items() if k not in excluded}
        self.set_status("已载入自建研究模板（非平台桌规）：完整新靴、零烧牌、S17/3:2/美式检查。请新建牌靴使用；当前规则快照不变。")

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
            ("start_from_new_shoe", "是否从新牌靴开始记录", ["未知", "是", "否"]),
            ("burn_cards_known", "是否已知烧牌数量（含零张）", ["未知", "是", "否"]),
            ("initial_burn_count", "初始烧牌数量（空为未知）", None),
            ("cut_shuffle_note", "切牌 / 洗牌约定", None), ("remark", "备注 / 素材来源", None),
        ]
        profile = self._build_rules()
        enum_maps = {
            "shoe_model": {"有限不放回": "finite_no_replacement", "每轮重置（未支持）": "per_round_reset", "未知": "unknown"},
            "dealer_bj_extra_bet_rule": {"未知": None, "全部注损失": "all_bets_lost", "仅原注（未支持）": "original_bets_only"},
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

        ttk.Label(win, text="记录规则与分析支持范围分开：本版分析仅支持明确的单手研究模板；未知字段不自动套用。", wraplength=800).grid(row=11, column=0, columnspan=4, padx=8, pady=8)
        ttk.Button(win, text="保存表单", command=save).grid(row=12, column=1, pady=8)
        ttk.Button(win, text="取消", command=win.destroy).grid(row=12, column=2, pady=8)

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

    def _current_seg(self):
        return self.ctrl.state().current

    def _selected_hand_id(self, seg) -> Optional[str]:
        seat_name = self.var_target.get()
        if seat_name != DEALER and seat_name not in seg.table.players:
            return None
        seat = seg.table.seat(seat_name)
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
            self.ctrl.start_round(players)
            self.set_status("新一轮开始（牌靴不重置，继续沿用上一靴剩余牌）")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_card(self, rank: str) -> None:
        try:
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴")
            hand_id = self._selected_hand_id(seg)
            if self.var_mode.get() == "揭示":
                target = self._find_hidden_deal_event(seg, hand_id)
                if target is None:
                    raise TableError("该手牌没有待揭示的暗牌/未知牌")
                self.ctrl.reveal(target.event_id, rank, self._suit())
                self.set_status(f"暗牌揭示为 {rank}（未重复扣牌，只做揭示转换）")
            else:
                self.ctrl.deal_shown(self.var_target.get(), rank,
                                     hand_id=hand_id, suit=self._suit())
                self.set_status(f"录入 {self.var_target.get()} <- {rank}")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_hidden_card(self) -> None:
        try:
            seg = self._current_seg()
            if seg is None:
                raise LedgerError("请先新建牌靴")
            self.ctrl.deal_hidden(self.var_target.get(),
                                  self._selected_hand_id(seg))
            self.set_status("已录入一张暗牌（牌面未知，物理已离靴，待揭示）")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_unknown_card(self) -> None:
        try:
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
            self.set_status(note)
            self.refresh_all()
        except Exception as e:
            self.fail(e)

    @tracked_operation
    def act_peek_negative(self) -> None:
        try:
            self.ctrl.peek_negative()
            self.set_status("已记录：庄家检查底牌，确认不是 Blackjack")
            self.refresh_all()
        except Exception as e:
            self.fail(e)

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
            messagebox.showinfo("本轮结算（确定性记账，非 EV）", txt)
            self.set_status("本轮已结算，可“新开一轮”继续同一牌靴")
            self.refresh_all()
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
        replay = self.ctrl.state()
        seg = replay.current
        self.refresh_hands(seg)
        self.refresh_table(seg)
        self.refresh_actions(seg)
        self.refresh_composition(seg, replay)
        self.refresh_timeline()
        self.refresh_topinfo(seg, replay)

    def refresh_topinfo(self, seg, replay) -> None:
        shoe_no = len(replay.segments)
        if seg is None:
            self.var_topinfo.set(
                f"牌靴 #0｜轮次 -｜完整性 -｜分析引擎：{ENGINE_VERSION}（V0.2a单手分析）")
            return
        ok, note = seg.shoe.conservation_check()
        self.var_topinfo.set(
            f"锁定 {seg.rules.n_decks}副/{seg.rules.dealer_soft17 or '未知'}｜牌靴 #{shoe_no}｜第 {seg.table.round_no} 轮｜"
            f"阶段 {'牌靴已结束' if seg.closed else seg.table.phase}｜记录：{self._record_status(seg)}｜"
            f"守恒：{'正常' if ok else '异常'}｜"
            f"规则确认：{seg.rules.confirm_status}｜分析：V0.2a受限单手模型")

    def refresh_hands(self, seg=None) -> None:
        if seg is None:
            seg = self._current_seg()
        values = ["（最新一手）"]
        selected_id = self._hand_ids.get(self.var_hand.get())
        self._hand_ids = {}
        if seg and (self.var_target.get() == DEALER or self.var_target.get() in seg.table.players):
            seat = seg.table.seat(self.var_target.get())
            for h in seat.hands:
                label = f"{h.hand_id}｜{h.display()}"
                values.append(label)
                self._hand_ids[label] = h.hand_id
        self.cmb_hand.configure(values=values)
        self.var_hand.set(next((label for label, hid in self._hand_ids.items() if hid == selected_id), "（最新一手）"))

    def refresh_table(self, seg) -> None:
        if seg is None:
            self.lbl_dealer.configure(text="（尚未创建牌靴）")
            for name in self.seat_labels:
                self.seat_labels[name].configure(text="（空座）")
            return
        d = seg.table.dealer
        self.lbl_dealer.configure(
            text="\n".join(h.display() for h in d.hands) or "（本轮未发牌）")
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

    @staticmethod
    def _record_status(seg):
        status = seg.shoe.integrity_state()
        if status == "可分析":
            return "记录无已知缺口（分析资格另核对）"
        return status

    def on_close(self):
        self.analysis_panel.close()
        self.ctrl.close()
        self.destroy()

    # ============================================================
    # 杂项
    # ============================================================
    def set_status(self, msg: str) -> None:
        self.var_status.set(msg)

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
