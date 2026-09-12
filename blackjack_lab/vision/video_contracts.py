# -*- coding: utf-8 -*-
"""V0.3b 旁观录像契约。识别器仍只产生观察，不写账本。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from .contracts import FACE_BACK, FACE_SHOWN, FACE_UNREADABLE

MODE_OBSERVER = "observer"

SHOE_START_FULL = "full_new_shoe"
SHOE_START_MID = "mid_shoe"
SHOE_START_UNCERTAIN = "uncertain"
SHOE_STARTS = (SHOE_START_FULL, SHOE_START_MID, SHOE_START_UNCERTAIN)

SHOE_START_LABELS = {
    SHOE_START_FULL: "从明确的新靴开始且观察完整",
    SHOE_START_MID: "从牌靴中途开始",
    SHOE_START_UNCERTAIN: "起始条件不确定",
}

VIDEO_SUFFIXES = (".mp4", ".mkv", ".avi", ".mov", ".webm")
# NVIDIA 桌面录像常见为数 GB；只读打开，不把原文件拷进仓库。
MAX_VIDEO_BYTES = 16 * 1024 * 1024 * 1024
MAX_VIDEO_SIDE = 4096


@dataclass(frozen=True)
class ShoeStartIntent:
    kind: str
    start_from_new_shoe: Optional[bool]
    n_decks: Optional[int]
    burn_cards_known: Optional[bool]
    initial_burn_count: Optional[int]
    analysis_ready: bool
    note: str

    def as_dict(self) -> Dict[str, object]:
        return {
            "kind": self.kind,
            "label": SHOE_START_LABELS[self.kind],
            "start_from_new_shoe": self.start_from_new_shoe,
            "n_decks": self.n_decks,
            "burn_cards_known": self.burn_cards_known,
            "initial_burn_count": self.initial_burn_count,
            "analysis_ready": self.analysis_ready,
            "note": self.note,
        }


def shoe_start_intent(kind: str) -> ShoeStartIntent:
    if kind not in SHOE_STARTS:
        raise ValueError(f"未知开靴声明: {kind}")
    shared = dict(
        n_decks=None, burn_cards_known=None, initial_burn_count=None, analysis_ready=False)
    if kind == SHOE_START_FULL:
        return ShoeStartIntent(
            kind=kind, start_from_new_shoe=True, **shared,
            note="即使从新靴开录，副数与烧牌数仍须人工确认，不得自动补默认值")
    if kind == SHOE_START_MID:
        return ShoeStartIntent(
            kind=kind, start_from_new_shoe=False, **shared,
            note="中途开始：可记已见图，不能把剩余组成当成完整新靴")
    return ShoeStartIntent(
        kind=kind, start_from_new_shoe=None, **shared,
        note="起始条件不确定：精确分析按现契约暂停，直到人工确认开靴条件")


@dataclass
class TemporalCard:
    """同一物理牌在录像时间轴上的可见性。后面揭牌不得改写此前时点的未知。"""
    observation_id: str
    initial_face: str
    first_seen_ms: int
    revealed_ms: Optional[int] = None
    confirmed_rank: Optional[str] = None
    confirmed_at: Optional[float] = None

    def visible_rank_at(self, video_time_ms: int) -> Optional[str]:
        if video_time_ms < self.first_seen_ms:
            return None
        if self.initial_face in (FACE_BACK, FACE_UNREADABLE):
            if self.revealed_ms is None or video_time_ms < self.revealed_ms:
                return None
        return self.confirmed_rank


def later_reveal_must_not_rewrite_prior(
        card: TemporalCard, decision_ms: int, later_reveal_ms: int, later_rank: str) -> Optional[str]:
    """复算 decision_ms 时，即使后来知道 later_rank，当时仍按未知处理。"""
    probe = TemporalCard(
        observation_id=card.observation_id,
        initial_face=card.initial_face,
        first_seen_ms=card.first_seen_ms,
        revealed_ms=later_reveal_ms,
        confirmed_rank=later_rank,
    )
    return probe.visible_rank_at(decision_ms)


@dataclass
class Detection:
    bbox: Dict[str, int]
    region_id: str
    seat_hint: Optional[str] = None
    hand_hint: Optional[str] = None
    rank_hint: Optional[str] = None
    face_state: str = FACE_SHOWN
    crop_sha256: str = ""
    notes: Tuple[str, ...] = ()


@dataclass
class PhysicalTrack:
    observation_id: str
    visual_track_id: str
    first_seen_frame: int
    first_seen_ms: int
    last_seen_frame: int
    last_seen_ms: int
    region_id: str
    seat_hint: Optional[str]
    hand_hint: Optional[str]
    bbox: Dict[str, int]
    face_state: str
    rank_hint: Optional[str]
    committed: bool = False
    rejected: bool = False
    moved: bool = False
    occluded: bool = False
    history: list = field(default_factory=list)

    def may_write_ledger(self) -> bool:
        return not self.committed and not self.rejected
