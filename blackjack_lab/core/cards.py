# -*- coding: utf-8 -*-
"""牌面基础对象。

设计约束（开发大纲 2.1 / 9）：
- 标准牌不含大小王，保留 13 种原始牌面 A,2,...,10,J,Q,K；
- A 按手牌状态计算 1 或 11；
- 计数分组（小/大/中性、T 点值桶）只是派生展示，不能代替原始牌面；
- 当无法区分 10/J/Q/K 时，允许记录为 T（"10 点牌未细分"），严禁捏造具体牌面；
- 完全无法识别时记录为 UNKNOWN（未知牌面），且允许未知花色。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple

# 13 种原始牌面（无大小王）
RANKS: Tuple[str, ...] = (
    "A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K",
)
# 四种花色；未知花色用 None，不猜测
SUITS: Tuple[str, ...] = ("S", "H", "D", "C")
SUIT_NAME = {"S": "黑桃", "H": "红桃", "D": "方块", "C": "梅花"}

# 特殊记录值
TEN_BUCKET = "T"       # 已知是 10 点，但无法区分 10/J/Q/K
UNKNOWN = "?"          # 牌面完全未知

TEN_RANKS: Tuple[str, ...] = ("10", "J", "Q", "K")
SMALL_RANKS: Tuple[str, ...] = ("2", "3", "4", "5", "6")
NEUTRAL_RANKS: Tuple[str, ...] = ("7", "8", "9")
BIG_RANKS: Tuple[str, ...] = ("A", "10", "J", "Q", "K")

VALID_RECORD_RANKS = RANKS + (TEN_BUCKET, UNKNOWN)


def rank_value(rank: str) -> int:
    """返回牌面的硬点值（A 按 11 计，软硬由 hand_total 统一处理）。"""
    if rank == "A":
        return 11
    if rank in TEN_RANKS or rank == TEN_BUCKET:
        return 10
    if rank in ("2", "3", "4", "5", "6", "7", "8", "9"):
        return int(rank)
    raise ValueError(f"未知牌面无法计算点值: {rank!r}")


def is_ten_value(rank: str) -> bool:
    return rank in TEN_RANKS or rank == TEN_BUCKET


@dataclass(frozen=True)
class Card:
    """一张被观察到的牌。

    rank 取 RANKS / TEN_BUCKET / UNKNOWN；suit 为 None 表示未知花色；
    track_id 是物理牌实例的观察追踪标识（同一发牌事件在多帧中只扣一次）。
    """
    rank: str
    suit: Optional[str] = None
    track_id: Optional[str] = None
    event_id: Optional[str] = None  # 发牌事件身份；不以牌面/花色/列表位置替代

    def __post_init__(self) -> None:
        if self.rank not in VALID_RECORD_RANKS:
            raise ValueError(f"非法牌面: {self.rank!r}")
        if self.suit is not None and self.suit not in SUITS:
            raise ValueError(f"非法花色: {self.suit!r}")

    @property
    def is_unknown(self) -> bool:
        return self.rank == UNKNOWN

    @property
    def is_ten_bucket(self) -> bool:
        return self.rank == TEN_BUCKET

    def display(self) -> str:
        """中文展示文本。"""
        if self.rank == UNKNOWN:
            base = "未知牌"
        elif self.rank == TEN_BUCKET:
            base = "10点(未细分)"
        else:
            base = self.rank
        if self.suit:
            return f"{SUIT_NAME[self.suit]}{base}"
        return base


def hand_total(ranks: Iterable[str]) -> Tuple[Optional[int], bool]:
    """计算一手牌的最佳点数。

    返回 (total, is_soft)：
    - 任何一张为 UNKNOWN 或空手牌时返回 (None, False)，表示总点数不可知；
    - A 先按 11 计，超过 21 且手中还有按 11 计的 A 时逐张降为 1；
    - is_soft 表示最终仍有一张 A 按 11 计算（软牌）；
    - T（10 点未细分）按 10 点参与计算。
    """
    known = list(ranks)
    if not known or UNKNOWN in known:
        return None, False
    total = 0
    aces = 0
    for r in known:
        if r == "A":
            aces += 1
            total += 11
        else:
            total += rank_value(r)
    soft = aces > 0
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    # 仍有 A 按 11 计 → 软牌
    is_soft = aces > 0
    return total, is_soft


def is_natural_blackjack(ranks: List[str]) -> bool:
    """天然 Blackjack：恰好两张牌，且为一张 A + 一张 10 点牌。

    三张及以上凑成的 21、分 A 后补出的 21 都不是天然 Blackjack。
    T（10 点未细分）在仅有两张牌时按 10 点牌处理（记录层判定，
    最终结算仍可按规则要求人工细分）。
    """
    if len(ranks) != 2:
        return False
    has_a = "A" in ranks
    has_t = any(is_ten_value(r) for r in ranks)
    return has_a and has_t


def ranks_group(rank: str) -> str:
    """派生计数分组：small/neutral/big/unknown。仅用于展示与统计。"""
    if rank in SMALL_RANKS:
        return "small"
    if rank in NEUTRAL_RANKS:
        return "neutral"
    if rank in BIG_RANKS:
        return "big"
    if rank == TEN_BUCKET:
        return "big"  # T 桶确定属于大牌组，但不细分原始牌面
    return "unknown"
