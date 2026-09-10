# -*- coding: utf-8 -*-
"""事件定义（开发大纲 3.1/3.2）。

事件是账本的唯一写入单位，只追加、不删除、不改写；
撤销与纠错通过追加 UNDO / CORRECTION 事件实现，重放时生效。
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

# ---- 事件类型 ----
SESSION_STARTED = "SESSION_STARTED"
SHOE_CREATED = "SHOE_CREATED"            # 新牌靴，锁定牌副数与规则快照
ROUND_STARTED = "ROUND_STARTED"
CARD_DEALT = "CARD_DEALT"                # 发牌（含 hidden 暗牌 / unknown 未识别）
CARD_REVEALED = "CARD_REVEALED"          # 暗牌或未识别牌事后揭示
PLAYER_ACTION = "PLAYER_ACTION"          # 补/停/加倍/分牌/投降
PEEK_NEGATIVE = "PEEK_NEGATIVE"          # 庄家检查底牌且不是 BJ
BURN_CARDS = "BURN_CARDS"                # 数量已知、牌面未知的烧牌
OBSERVATION_GAP = "OBSERVATION_GAP"      # 观察缺口（信息不完整）
ROUND_ENDED = "ROUND_ENDED"
SHOE_ENDED = "SHOE_ENDED"
UNDO = "UNDO"                            # 撤销：令目标事件在重放时失效
CORRECTION = "CORRECTION"                # 纠错：重放时替换目标事件负载

# 来源
SOURCE_MANUAL = "手动录入"
SOURCE_IMPORT = "导入"
SOURCE_RECOVERY = "崩溃恢复"
SOURCE_SIMULATOR = "自建模拟器"
SOURCE_LICENSED_VIDEO = "授权离线素材"
SOURCE_LICENSED_CAPTURE = "授权窗口捕获"

# 确认状态
CONFIRMED = "已确认"
CANDIDATE = "候选待确认"

# 牌面可见状态
FACE_SHOWN = "shown"      # 当时即可见
FACE_HIDDEN = "hidden"    # 规则性隐藏（庄家底牌，稍后揭示）
FACE_UNKNOWN = "unknown"  # 观察失败，牌面未知（待核对）

_ALL_TYPES = {
    SESSION_STARTED, SHOE_CREATED, ROUND_STARTED, CARD_DEALT, CARD_REVEALED,
    PLAYER_ACTION, PEEK_NEGATIVE, BURN_CARDS, OBSERVATION_GAP, ROUND_ENDED,
    SHOE_ENDED, UNDO, CORRECTION,
}


def new_event_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Event:
    etype: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=new_event_id)
    seq: int = -1                     # 单调递增序号，由账本分配
    event_time: float = field(default_factory=time.time)   # 记录时间
    observed_at: Optional[float] = None                    # 实际观察时间
    session_id: Optional[str] = None
    shoe_id: Optional[str] = None
    round_id: Optional[str] = None
    source: str = SOURCE_MANUAL
    confirm_status: str = CONFIRMED
    evidence: Optional[str] = None    # 证据裁片引用（V0.3+ 使用）
    rule_version: Optional[str] = None

    def __post_init__(self) -> None:
        if self.etype not in _ALL_TYPES:
            raise ValueError(f"未知事件类型: {self.etype}")
        if self.observed_at is None:
            self.observed_at = self.event_time

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id, "seq": self.seq, "etype": self.etype,
            "payload": self.payload, "event_time": self.event_time,
            "observed_at": self.observed_at, "session_id": self.session_id,
            "shoe_id": self.shoe_id, "round_id": self.round_id,
            "source": self.source, "confirm_status": self.confirm_status,
            "evidence": self.evidence, "rule_version": self.rule_version,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Event":
        return cls(
            etype=d["etype"], payload=d.get("payload", {}),
            event_id=d["event_id"], seq=d.get("seq", -1),
            event_time=d.get("event_time", time.time()),
            observed_at=d.get("observed_at"), session_id=d.get("session_id"),
            shoe_id=d.get("shoe_id"), round_id=d.get("round_id"),
            source=d.get("source", SOURCE_MANUAL),
            confirm_status=d.get("confirm_status", CONFIRMED),
            evidence=d.get("evidence"), rule_version=d.get("rule_version"),
        )


# ---- 便捷构造函数，统一必填字段校验 ----
def card_dealt(seat: str, rank: Optional[str], *, hidden: bool = False,
               unknown: bool = False, hand_id: Optional[str] = None,
               suit: Optional[str] = None, track_id: Optional[str] = None,
               confirm_status: str = CONFIRMED, source: str = SOURCE_MANUAL,
               evidence: Optional[str] = None) -> Event:
    if hidden and unknown:
        raise ValueError("一张牌不能同时是规则性暗牌与观察未知牌")
    face = FACE_HIDDEN if hidden else (FACE_UNKNOWN if unknown else FACE_SHOWN)
    if face == FACE_SHOWN and not rank:
        raise ValueError("可见发牌事件必须带牌面")
    return Event(CARD_DEALT, {
        "seat": seat, "rank": rank, "suit": suit, "track_id": track_id,
        "hand_id": hand_id, "face_state": face,
    }, source=source, confirm_status=confirm_status, evidence=evidence,
       )  # session/shoe/round 由账本补


def card_revealed(target_event_id: str, seat: str, hand_id: str,
                  rank: str, track_id: Optional[str] = None,
                  suit: Optional[str] = None) -> Event:
    return Event(CARD_REVEALED, {
        "target_event_id": target_event_id, "seat": seat, "hand_id": hand_id,
        "rank": rank, "track_id": track_id, "suit": suit,
    })
