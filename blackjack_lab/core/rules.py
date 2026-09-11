# -*- coding: utf-8 -*-
"""规则档案 RuleProfile 与规则能力矩阵（开发大纲 2.2 / 4-A）。

要点：
- 规则档案是版本化快照，牌靴开始即锁定；改牌副数/规则 = 新牌靴或规则纠错分支；
- "界面能填一个选项" 不等于 "引擎已正确支持"，每项能力在 CAPABILITY_MATRIX 标注
  已验证 / 实验性 / 未支持；未支持的组合必须拒绝计算而不是静默给出结果。
"""
from __future__ import annotations

import copy
import json
from dataclasses import asdict, dataclass, field
from typing import Optional, Tuple

SUPPORTED_DECKS = (6, 7, 8)

# 能力状态
VERIFIED = "已验证"
EXPERIMENTAL = "实验性"
UNSUPPORTED = "未支持"

# 牌靴模型
FINITE_NO_REPLACEMENT = "finite_no_replacement"   # 有限不放回（首个概率版本唯一承诺）
PER_ROUND_RESET = "per_round_reset"
SHOE_UNKNOWN = "unknown"

CONFIRM_UNKNOWN = "未确认"
CONFIRM_VERIFIED = "已确认"


@dataclass
class RuleProfile:
    """桌规档案。未知项保留 None / 'unknown'，严禁取平均值或默认八副。"""

    profile_id: str = "profile-default"
    version: int = 1
    vendor: Optional[str] = None              # 厂商
    game_name: Optional[str] = None           # 游戏名
    table_id: Optional[str] = None            # 桌标识
    rule_source: Optional[str] = None         # 规则来源
    verify_date: Optional[str] = None         # 核对日期
    confirm_status: str = CONFIRM_UNKNOWN     # 未确认 / 已确认

    n_decks: int = 6
    shoe_model: str = FINITE_NO_REPLACEMENT

    dealer_soft17: Optional[str] = None       # 'S17' 停牌 / 'H17' 补牌 / None 未知
    blackjack_payout: Optional[Tuple[int, int]] = (3, 2)  # 净收益倍率 3:2；6:5 显式配置
    american_hole_card: Optional[bool] = None  # 美式底牌机制
    check_bj_when: Optional[str] = None        # 何时检查 Blackjack
    dealer_bj_extra_bet_rule: Optional[str] = None  # 庄家 BJ 时追加注结算

    double_on_totals: Optional[Tuple[int, ...]] = None  # 可加倍点数；None=任意两张
    double_after_split: Optional[bool] = None
    split_match: str = "same_rank"            # same_rank 相同牌面 / same_value 相同点值
    max_split_hands: int = 4
    resplit_aces: Optional[bool] = False
    split_ace_hit_once: Optional[bool] = True
    split_deal_order: Optional[str] = None    # Legacy snapshots retain unknown order.

    surrender: Optional[str] = None           # None 不支持 / 'early' / 'late'
    n_seats: int = 7                          # 玩家座位 1~7，庄家固定 1 位

    burn_cards_known: Optional[bool] = None   # 烧牌数量是否已知
    initial_burn_count: Optional[int] = None  # 新靴初始烧牌数，未知不得默认为零
    start_from_new_shoe: Optional[bool] = None
    cut_shuffle_note: Optional[str] = None

    remark: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("规则档案ID不能为空")
        if type(self.n_decks) is not int or self.n_decks not in SUPPORTED_DECKS:
            raise ValueError(f"牌副数仅支持 6/7/8，收到: {self.n_decks!r}")
        if type(self.n_seats) is not int or not 1 <= self.n_seats <= 7:
            raise ValueError("玩家座位数必须在 1~7 之间")
        if self.split_match not in ("same_rank", "same_value"):
            raise ValueError("split_match 只能是 same_rank / same_value")
        if isinstance(self.blackjack_payout, list):
            self.blackjack_payout = tuple(self.blackjack_payout)
        if isinstance(self.double_on_totals, list):
            self.double_on_totals = tuple(self.double_on_totals)
        if type(self.version) is not int or self.version < 1:
            raise ValueError("规则版本必须为正整数")
        if type(self.max_split_hands) is not int or not 1 <= self.max_split_hands <= 8:
            raise ValueError("最大手数必须为 1~8 的整数")
        if self.blackjack_payout is not None and (
            not isinstance(self.blackjack_payout, tuple) or len(self.blackjack_payout) != 2
            or any(type(n) is not int or n <= 0 for n in self.blackjack_payout)
        ):
            raise ValueError("BJ 赔付必须为两个正整数或未知")
        if self.double_on_totals is not None and (
            not isinstance(self.double_on_totals, tuple)
            or any(type(n) is not int or not 2 <= n <= 21 for n in self.double_on_totals)
        ):
            raise ValueError("加倍点数必须为 2~21 的整数列表")
        for name, allowed in {
            "dealer_soft17": (None, "S17", "H17"),
            "shoe_model": (FINITE_NO_REPLACEMENT, PER_ROUND_RESET, SHOE_UNKNOWN),
            "confirm_status": (CONFIRM_UNKNOWN, CONFIRM_VERIFIED),
            "surrender": (None, "early", "late"),
            "dealer_bj_extra_bet_rule": (None, "all_bets_lost", "original_bets_only"),
            "split_deal_order": (None, "sequential_complete_first", "both_second_cards_first"),
        }.items():
            if getattr(self, name) not in allowed:
                raise ValueError(f"无效规则字段 {name}: {getattr(self, name)!r}")
        for name in ("american_hole_card", "double_after_split", "resplit_aces",
                     "split_ace_hit_once", "burn_cards_known", "start_from_new_shoe"):
            if getattr(self, name) is not None and type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} 必须为是/否/未知")
        if self.initial_burn_count is not None and (type(self.initial_burn_count) is not int or not 0 <= self.initial_burn_count <= self.n_decks * 52):
            raise ValueError("初始烧牌数必须为合法非负整数或未知")
        if self.initial_burn_count is not None and self.burn_cards_known is not True:
            raise ValueError("填写初始烧牌数时必须明确已知烧牌数量")

    # ---- 快照序列化（锁定后随牌靴一起保存）----
    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "RuleProfile":
        data = json.loads(text)
        return cls(**data)

    def snapshot(self) -> "RuleProfile":
        return copy.deepcopy(self)


# 规则能力矩阵：记录能力与V0.2a已验收分析模板分别声明。
CAPABILITY_MATRIX = {
    "6/7/8副初始化与牌数守恒": (VERIFIED, "core.shoe，三种牌副数均有单元测试"),
    "有限不放回牌靴记录": (VERIFIED, "事件账本逐张扣减，幂等且可重放"),
    "庄家+7座位/分牌归属记录": (VERIFIED, "core.table 支持父子手牌与最多分牌手数限制"),
    "撤销/追加纠错/重放": (VERIFIED, "ledger 追加纠错事件，不删除审计链"),
    "未知牌面/10点未细分/烧牌/观察缺口": (VERIFIED, "三类未知信息分开管理"),
    "SQLite保存与崩溃恢复/JSON、CSV导出": (VERIFIED, "storage 模块"),
    "手动录牌中文薄界面": (VERIFIED, "Tkinter 本地窗口，不启动网络服务"),
    "每轮重置牌靴模型": (UNSUPPORTED, "可保留规则字段，创建牌靴时拒绝未支持模型"),
    "目标下一张点值分布/补牌爆牌/庄家终局": (VERIFIED, "V0.2a研究模板，未知底牌条件化与非BJ检查"),
    "下一轮开局天然BJ概率/开局优势": (UNSUPPORTED, "与当前手牌EV分开，后续实验模块实现"),
    "确定性结算": (VERIFIED, "已录终局、双方牌面完整；BJ追加注仅验证全部注损失；非EV"),
    "历史时点/人工纠错/导入恢复": (VERIFIED, "前缀重放不使用后续揭示或纠错；数据库事务写入"),
    "规则纠错分支": (UNSUPPORTED, "改规则请新建牌靴，不修改已锁定快照"),
    "单手停/补/加倍/晚投降EV及净收益分布": (VERIFIED, "S17/3:2/美式检查/零烧牌/单玩家未分牌；补牌后按可见信息继续补或停"),
    "单玩家两手顺序分牌合计EV与收益分布": (EXPERIMENTAL, "V0.2b1显式模板：共享牌靴/庄家、无再分/无DAS/分A一张；性能与验收见本版回执"),
    "单玩家两手DAS合计EV与收益分布": (EXPERIMENTAL, "V0.2b2显式DAS模板：共享牌靴、无再分、非A分手一次DAS、分A一张；旧b1快照不改写"),
    "再分/DAS/多玩家整轮净收益分布": (UNSUPPORTED, "保留录牌；两手DAS仅走显式模板；旧四手模板不截断为两手，缺能力时仅部分比较"),
    "分析快照/请求取消/超时/过期/历史复算": (VERIFIED, "独立请求进程、事件前缀身份、不可覆盖的JSON旁路快照"),
    "本地牌面识别/屏幕捕获": (UNSUPPORTED, "V0.3/V0.4，本版仍为手动录入"),
    "实盘平台适配（含 Stake）": (UNSUPPORTED, "须单独核对平台条款与授权，默认禁用"),
}


def capability_report() -> str:
    """生成可读的能力矩阵文本（供界面与文档使用）。"""
    lines = ["规则能力矩阵（V0.2b1）", "=" * 36]
    for name, (status, note) in CAPABILITY_MATRIX.items():
        lines.append(f"[{status}] {name} —— {note}")
    return "\n".join(lines)
