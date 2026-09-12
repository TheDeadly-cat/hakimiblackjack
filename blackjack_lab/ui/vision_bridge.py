# -*- coding: utf-8 -*-
"""识牌确认桥：候选经人工确认后才走控制器入账。识别器本身不写 SQLite。"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from ..core.cards import RANKS, TEN_BUCKET, UNKNOWN, VALID_RECORD_RANKS
from ..ledger.events import CONFIRMED, SOURCE_VISION_CONFIRMED
from ..vision.contracts import (
    REVIEW_PENDING, CardObservation, RecognitionResult,
)
from .controller import SessionController

OP_NEW = "new"
OP_REVEAL = "reveal"
OP_CORRECT = "correction"
OP_REJECT = "reject"
OPS = (OP_NEW, OP_REVEAL, OP_CORRECT, OP_REJECT)

FACE_SHOWN_LEDGER = "shown"
FACE_UNKNOWN_LEDGER = "unknown"
FACE_HIDDEN_LEDGER = "hidden"


class VisionBridgeError(ValueError):
    """确认协议拒绝；不是识别成功。"""


@dataclass
class ConfirmDecision:
    observation_id: str
    operation: str
    seat: Optional[str] = None
    hand_id: Optional[str] = None
    confirmed_rank: Optional[str] = None
    face_state: str = FACE_SHOWN_LEDGER
    target_event_id: Optional[str] = None
    suit: Optional[str] = None
    reason: str = "识牌工作区人工核对"


@dataclass
class CommitResult:
    status: str
    request_id: str
    observation_id: str
    already_saved: bool
    event: Any = None
    message: str = ""


def _sha(payload: Dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def capture_bind_context(ctrl: SessionController) -> Dict[str, Optional[str]]:
    seg = ctrl.state().current
    return {
        "session_id": ctrl.session_id,
        "shoe_id": seg.shoe_id if seg and not seg.closed else None,
        "round_id": seg.round_id if seg and not seg.closed else None,
    }


class VisionReviewSession:
    """一张图片的核对工作区。未确认候选不扣牌。"""

    def __init__(self, ctrl: SessionController, result: RecognitionResult,
                 evidence_root: Optional[Path] = None):
        self.ctrl = ctrl
        self.result = result
        self.evidence_root = Path(evidence_root) if evidence_root else None
        self.bound = capture_bind_context(ctrl)
        self.commits: Dict[str, CommitResult] = {}
        self.rejected: Dict[str, ConfirmDecision] = {}
        self._by_id = {o.observation_id: o for o in result.observations}
        self.invalidated_reason: Optional[str] = None

    def invalidate(self, reason: str) -> None:
        """Withdraw candidates after a model/source/ROI change, even via old references."""
        self.invalidated_reason = reason or "识牌配置已改变"

    def _require_active(self) -> None:
        if self.invalidated_reason is not None:
            raise VisionBridgeError(f"旧候选已撤回：{self.invalidated_reason}。请重新识别并核对。")

    def register_observation(self, obs) -> None:
        """录像逐帧更新同一观察身份；已确认的 id 保持，避免跳转后当成新牌。"""
        self._require_active()
        existing = self._by_id.get(obs.observation_id)
        self._by_id[obs.observation_id] = obs
        if existing is None:
            self.result.observations.append(obs)
            return
        for index, current in enumerate(self.result.observations):
            if current.observation_id == obs.observation_id:
                self.result.observations[index] = obs
                return

    def present_frame(self, result: RecognitionResult) -> None:
        """Only current-frame crops may be confirmed; committed identities persist."""
        self._require_active()
        if (result.model_id != self.result.model_id
                or result.model_digest != self.result.model_digest
                or result.layout_profile_id != self.result.layout_profile_id):
            self.invalidate("帧的模型或区域已改变")
            self._require_active()
        self.result = result
        self._by_id = {obs.observation_id: obs for obs in result.observations}

    def has_pending(self) -> bool:
        if self.invalidated_reason is not None:
            return False
        done = set(self.commits) | set(self.rejected)
        return any(oid not in done for oid in self._by_id)

    def has_committed(self) -> bool:
        return any(c.status in {"committed", "duplicate"} and c.event for c in self.commits.values())

    def review_status(self) -> str:
        if self.invalidated_reason is not None:
            return "旧识牌候选已撤回"
        if self.has_pending():
            return REVIEW_PENDING
        if self.rejected and not self.has_committed():
            return "候选已拒绝，未入账"
        return "识牌候选已处理"

    def rebind_current_round(self) -> None:
        self._require_active()
        if self.has_committed():
            raise VisionBridgeError("已有入账记录，不能把旧任务改绑到新轮；请重新打开图片")
        self.bound = capture_bind_context(self.ctrl)

    def current_context_matches(self) -> bool:
        now = capture_bind_context(self.ctrl)
        return now == self.bound

    def request_id(self, decision: ConfirmDecision) -> str:
        return _sha({
            "session_id": self.bound["session_id"],
            "shoe_id": self.bound["shoe_id"],
            "round_id": self.bound["round_id"],
            "observation_id": decision.observation_id,
            "operation": decision.operation,
            "seat": decision.seat,
            "hand_id": decision.hand_id,
            "confirmed_rank": decision.confirmed_rank,
            "face_state": decision.face_state,
            "target_event_id": decision.target_event_id,
            "suit": decision.suit,
        })

    def _require_fresh_context(self) -> None:
        if not self.current_context_matches():
            raise VisionBridgeError(
                "录牌上下文已变化（换会话/换靴/换轮）。旧候选不能写入新上下文，请重新绑定或重新打开图片。")
        if not self.bound["shoe_id"] or not self.bound["round_id"]:
            raise VisionBridgeError("当前没有可写入的牌靴与轮次。请先开靴开轮，再绑定当前轮。")
        if self.ctrl.session_id != self.bound["session_id"]:
            raise VisionBridgeError("会话已切换，旧识牌任务已过期")

    def _observation(self, observation_id: str) -> CardObservation:
        try:
            return self._by_id[observation_id]
        except KeyError as exc:
            raise VisionBridgeError("未知观察，不能入账") from exc

    def _validate_decision(self, decision: ConfirmDecision, obs: CardObservation) -> None:
        if decision.operation not in OPS:
            raise VisionBridgeError(f"不支持的确认操作: {decision.operation}")
        if decision.operation == OP_REJECT:
            return
        if not decision.seat:
            raise VisionBridgeError("入账必须人工指定座位，不能只靠检测框排序")
        if decision.operation in (OP_REVEAL, OP_CORRECT) and not decision.target_event_id:
            raise VisionBridgeError("揭示或纠错必须指定原发牌事件，不能当新牌再扣一次")
        if decision.operation == OP_NEW and decision.face_state == FACE_SHOWN_LEDGER:
            if decision.confirmed_rank not in RANKS + (TEN_BUCKET,):
                raise VisionBridgeError("可见新牌必须由人工确认 13 种牌面或 T，不能按模型分数自动扣牌")
        if decision.operation == OP_NEW and decision.face_state == FACE_UNKNOWN_LEDGER:
            if decision.confirmed_rank not in (None, UNKNOWN):
                raise VisionBridgeError("确认看不清时不得附带猜测牌面")
        if decision.operation == OP_NEW and decision.face_state == FACE_HIDDEN_LEDGER:
            if decision.confirmed_rank not in (None, UNKNOWN):
                raise VisionBridgeError("暗牌入账不得携带未揭示牌面")
        if decision.operation in (OP_REVEAL, OP_CORRECT):
            if decision.confirmed_rank not in VALID_RECORD_RANKS or decision.confirmed_rank == UNKNOWN:
                raise VisionBridgeError("揭示/纠错需要明确牌面")
        if decision.confirmed_rank == "T" and obs.accepted_rank() in ("10", "J", "Q", "K"):
            # 允许人工把无法细分的十点记成 T，但必须是人的决定。
            pass

    def _evidence_ref(self, decision: ConfirmDecision, request_id: str,
                      obs: CardObservation) -> str:
        model_ranks = [c.as_dict() for c in obs.rank_candidates]
        record = {
            "observation_id": obs.observation_id,
            "asset_sha256": obs.asset_sha256,
            "model_id": obs.model_id,
            "model_digest": obs.model_digest,
            "model_rank_unaltered": model_ranks,
            "model_accepted_rank": obs.accepted_rank(),
            "model_face_state": obs.face_state_candidate,
            "human_rank": decision.confirmed_rank,
            "human_face_state": decision.face_state,
            "operation": decision.operation,
            "seat": decision.seat,
            "hand_id": decision.hand_id,
            "request_id": request_id,
            "bound": self.bound,
            "captured_at": obs.captured_at,
            "clock_unknown": obs.captured_at is None,
            "recognized_at_not_used_as_observed_at": True,
            "source_declaration": obs.source_declaration,
            "crop_relpath": obs.crop_relpath,
            "score_is_calibrated_probability": False,
        }
        if self.evidence_root is not None:
            folder = self.evidence_root / "confirmations"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{request_id}.json"
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True),
                            encoding="utf-8")
            return str(path.relative_to(self.evidence_root)).replace("\\", "/")
        return f"vision-obs:{obs.observation_id}/req:{request_id}"

    def _observed_at(self, obs: CardObservation) -> Optional[float]:
        # 有真实采集时钟才写入账本 observed_at；禁止用模型运行时间冒充。
        if obs.captured_at is None:
            return None
        return float(obs.captured_at)

    def confirm(self, decision: ConfirmDecision) -> CommitResult:
        self._require_active()
        if decision.operation not in OPS:
            raise VisionBridgeError(f"不支持的确认操作: {decision.operation}")
        obs = self._observation(decision.observation_id)
        self._validate_decision(decision, obs)
        if decision.operation == OP_REJECT:
            if decision.observation_id in self.commits and self.commits[decision.observation_id].event:
                raise VisionBridgeError("该观察已入账，不能用拒绝掩盖；如需撤销请走账本撤销")
            self.rejected[decision.observation_id] = decision
            result = CommitResult(
                status="rejected", request_id=self.request_id(decision),
                observation_id=decision.observation_id, already_saved=False,
                message="已拒绝该候选，未写入账本",
            )
            return result

        self._require_fresh_context()
        request_id = self.request_id(decision)
        previous = self.commits.get(decision.observation_id)
        if previous and previous.request_id == request_id and previous.event is not None:
            return CommitResult(
                status="duplicate", request_id=request_id,
                observation_id=decision.observation_id, already_saved=True,
                event=previous.event, message="相同确认请求已提交，未再次扣牌",
            )

        track_id = f"vision:{obs.observation_id}"
        evidence = self._evidence_ref(decision, request_id, obs)
        observed_at = self._observed_at(obs)
        source = SOURCE_VISION_CONFIRMED
        common = dict(
            hand_id=decision.hand_id, track_id=track_id, evidence=evidence,
            source=source, event_id=request_id, observed_at=observed_at,
        )
        revision_before = self.ctrl.commit_revision
        if decision.operation == OP_NEW and decision.face_state == FACE_SHOWN_LEDGER:
            event = self.ctrl.deal_shown(
                decision.seat, decision.confirmed_rank, suit=decision.suit, **common)
        elif decision.operation == OP_NEW and decision.face_state == FACE_UNKNOWN_LEDGER:
            event = self.ctrl.deal_unknown(
                decision.seat, confirm_status=CONFIRMED, **common)
        elif decision.operation == OP_NEW and decision.face_state == FACE_HIDDEN_LEDGER:
            event = self.ctrl.deal_hidden(decision.seat, **common)
        elif decision.operation == OP_REVEAL:
            event = self.ctrl.reveal(
                decision.target_event_id, decision.confirmed_rank, suit=decision.suit,
                evidence=evidence, source=source, event_id=request_id,
                observed_at=observed_at)
        elif decision.operation == OP_CORRECT:
            payload = {"rank": decision.confirmed_rank}
            if decision.suit is not None:
                payload["suit"] = decision.suit
            if decision.face_state == FACE_SHOWN_LEDGER:
                payload["face_state"] = "shown"
            event = self.ctrl.correct(
                decision.target_event_id, payload, decision.reason or "识牌核对纠正",
                source=source, evidence=evidence, observed_at=observed_at,
                correction_id=request_id)
        else:
            raise VisionBridgeError("无法执行该确认操作")

        already = self.ctrl.commit_revision == revision_before
        commit = CommitResult(
            status="duplicate" if already else "committed",
            request_id=request_id,
            observation_id=decision.observation_id,
            already_saved=already,
            event=event,
            message="已按人工确认写入账本" if not already else "相同确认请求已提交，未再次扣牌",
        )
        self.commits[decision.observation_id] = commit
        self.rejected.pop(decision.observation_id, None)
        return commit
