# -*- coding: utf-8 -*-
"""analysis：信息状态与组合统计（V0.1 只提供确定性组成展示）。

概率与期望净收益引擎属于 V0.2，本版本不输出任何 EV/胜率数值，
界面在该区域显示"尚未交付"与原因，不填随机占位数（开发大纲 4-D/5）。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

from ..core.shoe import ShoeState

GROUP_NAME = {"small": "小牌 2~6", "neutral": "中性牌 7~9", "big": "大牌 A/10/J/Q/K"}


@dataclass
class CompositionView:
    """某一时点、仅由当时已知信息得到的牌靴组成视图（非概率预测）。"""
    n_decks: int
    total_cards: int
    physical_remaining: Optional[int]
    remaining: Dict[str, int]
    groups_initial: Dict[str, int]
    groups_remaining: Dict[str, object]
    integrity_state: str
    gap: bool
    unrevealed_out: int
    burn_unknown: int
    t_bucket_out: int

    @classmethod
    def from_shoe(cls, shoe: ShoeState) -> "CompositionView":
        return cls(
            n_decks=shoe.n_decks,
            total_cards=shoe.total_cards,
            physical_remaining=shoe.physical_remaining(),
            remaining=dict(shoe.remaining),
            groups_initial=shoe.group_initial(),
            groups_remaining=shoe.group_remaining(),
            integrity_state=shoe.integrity_state(),
            gap=shoe.gap,
            unrevealed_out=shoe.unrevealed_out,
            burn_unknown=shoe.burn_unknown,
            t_bucket_out=shoe.t_bucket_out,
        )

    def big_small_ratio_note(self) -> str:
        """大纲 4-E：'7:3' 默认仅指大小牌之间比例，中性牌独立。"""
        g = self.groups_remaining
        small, big = g["small"], g["big"]
        total_sb = small + big
        if total_sb == 0:
            return "大小牌均已发完"
        return (f"剩余 大:小 = {big}:{small}"
                f"（大牌占大小牌合计 {big/total_sb:.1%}，"
                f"中性牌 {g['neutral']} 张独立计算）")
