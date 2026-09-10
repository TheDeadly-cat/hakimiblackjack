# -*- coding: utf-8 -*-
"""牌靴状态（开发大纲 2.1 / 3.3 / 4-A）。

核心约束：
- 仅支持 6/7/8 副，标准牌无大小王，每个原始牌面初始 4×牌副数 张；
- 已确认牌面、10 点未细分（T 桶）、已发未揭示（如庄家底牌）、
  已知数量的烧牌、观察缺口 五类集合严格分开管理；
- 任何扣减不得产生负数；总数守恒可随时校验；
- 出现观察缺口（断流/漏牌/未知数量移除）后状态转为"信息不完整"，
  牌靴级精确分析必须暂停（V0.1 只记录与提示，不做分析）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .cards import (
    BIG_RANKS, NEUTRAL_RANKS, RANKS, SMALL_RANKS, TEN_BUCKET, TEN_RANKS,
)
from .rules import SUPPORTED_DECKS

# 三档信息状态
STATE_ANALYZABLE = "可分析"
STATE_PENDING = "待核对"
STATE_INCOMPLETE = "信息不完整"


@dataclass
class ShoeState:
    n_decks: int
    # 13 种原始牌面的剩余张数（T 桶与未知牌不从这里按具体牌面扣）
    remaining: Dict[str, int] = field(default_factory=dict)
    # 已确认移除的各原始牌面张数
    exact_out: Dict[str, int] = field(default_factory=dict)
    # 已知是 10 点但未细分 10/J/Q/K 的张数
    t_bucket_out: int = 0
    # 已物理发出、但牌面尚未揭示的张数（如庄家底牌）
    unrevealed_out: int = 0
    # 已知数量、牌面未知的烧牌张数
    burn_unknown: int = 0
    # 观察缺口：可能存在未知数量的移除，守恒只能给不等式
    gap: bool = False
    gap_notes: list = field(default_factory=list)
    # 待核对的候选牌（识别候选在 V0.3 才会产生，手动录入也可先挂候选）
    pending_candidates: int = 0

    def __post_init__(self) -> None:
        if type(self.n_decks) is not int or self.n_decks not in SUPPORTED_DECKS:
            raise ValueError(f"牌副数仅支持 6/7/8: {self.n_decks!r}")
        if not self.remaining:
            per_rank = 4 * self.n_decks
            self.remaining = {r: per_rank for r in RANKS}
            self.exact_out = {r: 0 for r in RANKS}

    # ---------- 基本数量 ----------
    @property
    def total_cards(self) -> int:
        return 52 * self.n_decks

    def exact_remaining_sum(self) -> int:
        """剩余原始牌面张数之和（含 T 桶/未揭示牌尚未细分的不确定性）。"""
        return sum(self.remaining.values())

    def physical_remaining(self) -> Optional[int]:
        """物理上仍待发出的牌数；存在观察缺口时返回 None（无法确定）。"""
        if self.gap:
            return None
        return (
            self.total_cards
            - sum(self.exact_out.values())
            - self.t_bucket_out
            - self.unrevealed_out
            - self.burn_unknown
        )

    # ---------- 扣减操作（只进不退；撤销靠重放）----------
    def remove_known(self, rank: str) -> None:
        """扣掉一张已确认牌面的牌；rank='T' 表示 10 点未细分。"""
        self._require_capacity(1)
        if rank in TEN_RANKS and sum(self.remaining[r] for r in TEN_RANKS) <= self.t_bucket_out:
            raise ConsistencyError("10点组容量已由未细分牌占用")
        if rank == TEN_BUCKET:
            group_left = sum(self.remaining[r] for r in TEN_RANKS) - self.t_bucket_out
            if group_left <= 0:
                raise ConsistencyError("10/J/Q/K 组已无可扣的牌")
            self.t_bucket_out += 1
            return
        if rank not in self.remaining:
            raise ConsistencyError(f"非法牌面: {rank!r}")
        if self.remaining[rank] <= 0:
            raise ConsistencyError(f"牌面 {rank} 剩余为 0，不能继续扣减（防止负数）")
        self.remaining[rank] -= 1
        self.exact_out[rank] += 1

    def place_unrevealed(self) -> None:
        """记录一张已物理发出但牌面未知的牌（典型：庄家底牌先放后翻）。"""
        self._require_capacity(1)
        self.unrevealed_out += 1

    def reveal_unrevealed(self, rank: str) -> None:
        """一张此前已发出的未揭示牌现在亮出牌面。"""
        if self.unrevealed_out <= 0:
            raise ConsistencyError("没有待揭示的未揭示牌")
        # 先检查可供，再落账
        if rank == TEN_BUCKET:
            group_left = sum(self.remaining[r] for r in TEN_RANKS) - self.t_bucket_out
            if group_left <= 0:
                raise ConsistencyError("10/J/Q/K 组已无牌，无法揭示为 10 点牌")
        else:
            if rank not in self.remaining or self.remaining[rank] <= 0:
                raise ConsistencyError(f"牌面 {rank} 剩余为 0，揭示结果与牌靴组成矛盾")
        self.unrevealed_out -= 1
        try:
            self.remove_known(rank)
        except Exception:
            self.unrevealed_out += 1
            raise

    def burn_known_count(self, count: int = 1) -> None:
        """烧牌：数量已知、牌面未知（大纲 3.3 第 2 类）。"""
        if type(count) is not int or count <= 0:
            raise ValueError("烧牌数量必须为正整数")
        self._require_capacity(count)
        self.burn_unknown += count

    def _require_capacity(self, count: int) -> None:
        # 缺口使真实剩余未知，但已说明移除仍不可超过牌靴总容量。
        accounted = sum(self.exact_out.values()) + self.t_bucket_out + self.unrevealed_out + self.burn_unknown
        if accounted + count > self.total_cards:
            raise ConsistencyError("声明的移除超过牌靴容量，拒绝入账")

    def mark_gap(self, note: str = "") -> None:
        """观察缺口：断流/漏牌/未知数量烧牌（大纲 3.3 第 3 类）。

        缺口之后不得凭概率补牌，牌靴级精确分析暂停。
        """
        self.gap = True
        self.gap_notes.append(note)

    def add_pending_candidate(self, delta: int = 1) -> None:
        self.pending_candidates += delta
        if self.pending_candidates < 0:
            self.pending_candidates = 0

    # ---------- 派生分组展示（不替代原始牌面）----------
    def group_remaining(self) -> Dict[str, Optional[int]]:
        """小牌 2~6 / 中性 7~9 / 大牌 A,10,J,Q,K 的剩余张数。"""
        small = sum(self.remaining[r] for r in SMALL_RANKS)
        neutral = sum(self.remaining[r] for r in NEUTRAL_RANKS)
        big = sum(self.remaining[r] for r in BIG_RANKS) - self.t_bucket_out
        return {"small": small, "neutral": neutral, "big": big}

    def group_initial(self) -> Dict[str, int]:
        per_rank = 4 * self.n_decks
        return {
            "small": 5 * per_rank,       # 2~6 五种
            "neutral": 3 * per_rank,     # 7~9 三种
            "big": 5 * per_rank,         # A + 10/J/Q/K 五种
        }

    # ---------- 守恒校验 ----------
    def conservation_check(self) -> Tuple[bool, str]:
        """返回 (是否守恒, 说明)。存在观察缺口时只给不等式结论。"""
        total = self.total_cards
        exact_out = sum(self.exact_out.values())
        # 1) 每种牌面：初始 = 剩余 + 已确认移出
        for r in RANKS:
            if self.remaining[r] + self.exact_out[r] != 4 * self.n_decks:
                return False, f"牌面 {r} 不守恒：剩余+移出 != 初始"
            if self.remaining[r] < 0 or self.exact_out[r] < 0:
                return False, f"牌面 {r} 出现负数"
        # 2) T 桶不能超过 10 点组容量
        if self.t_bucket_out > sum(self.remaining[r] for r in TEN_RANKS):
            return False, "10 点未细分数量超过 10/J/Q/K 总容量"
        accounted = exact_out + self.t_bucket_out + self.unrevealed_out + self.burn_unknown
        if self.gap:
            ok = accounted <= total
            return ok, ("存在观察缺口，仅能验证已入账移除不超过总牌数；"
                        f"已说明移除 {accounted}/{total}")
        if accounted > total:
            return False, f"已移除 {accounted} 超过总牌数 {total}"
        phys = total - accounted
        # 未揭示牌与烧牌不从 13 牌面账扣减，物理剩余需一并减去
        expect_phys = (self.exact_remaining_sum() - self.t_bucket_out
                       - self.unrevealed_out - self.burn_unknown)
        if phys != expect_phys:
            return False, f"物理剩余 {phys} 与牌面账 {expect_phys} 不一致"
        return True, f"守恒正常：已说明移除 {accounted}/{total}，物理剩余 {phys}"

    # ---------- 三档状态 ----------
    def integrity_state(self) -> str:
        if self.gap:
            return STATE_INCOMPLETE
        if self.pending_candidates > 0 or self.unrevealed_out > 0:
            # 未揭示底牌是规则性隐藏，属正常；候选牌才是待核对
            return STATE_PENDING if self.pending_candidates > 0 else STATE_ANALYZABLE
        return STATE_ANALYZABLE


class ConsistencyError(Exception):
    """牌靴一致性错误：负数、超扣、组成矛盾等，必须拒绝入账。"""
