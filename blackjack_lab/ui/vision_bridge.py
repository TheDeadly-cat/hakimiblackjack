# -*- coding: utf-8 -*-
"""识牌确认桥：候选经人工确认后才走控制器入账。识别器本身不写 SQLite。"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

from ..core.cards import RANKS, TEN_BUCKET, UNKNOWN, VALID_RECORD_RANKS
from ..ledger.events import CARD_DEALT, CARD_REVEALED, CORRECTION, CONFIRMED, SOURCE_VISION_CONFIRMED
from ..vision.contracts import (
    REVIEW_PENDING, CardObservation, RecognitionResult,
)
from .controller import SessionController

OP_NEW = "new"
OP_REVEAL = "reveal"
OP_CORRECT = "correction"
OP_REJECT = "reject"
OP_LINK = "link_existing"
OPS = (OP_NEW, OP_REVEAL, OP_CORRECT, OP_REJECT, OP_LINK)

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
        self.links: Dict[str, CommitResult] = {}
        self._link_history: Dict[str, str] = {}
        self._by_id = {o.observation_id: o for o in result.observations}
        self.invalidated_reason: Optional[str] = None
        for obs in result.observations:self._restore_link(obs)

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
        previous_links,previous_history = dict(self.links),dict(self._link_history)
        try:
            for obs in result.observations:
                if obs.observation_id not in self.links:self._restore_link(obs)
        except VisionBridgeError:
            self.links,self._link_history = previous_links,previous_history
            raise
        self.result = result
        self._by_id = {obs.observation_id: obs for obs in result.observations}

    def has_pending(self) -> bool:
        if self.invalidated_reason is not None:
            return False
        try:targets = {t['event_id'] for t in self.existing_card_targets()} if self.links else set()
        except VisionBridgeError:targets = set()
        occupied = Counter(self._dealt_target(c.event) for oid,c in {**self.links,**self.commits}.items()
            if oid in self._by_id and c.event is not None)
        valid_links = {oid for oid,link in self.links.items()
            if link.event.event_id in targets and occupied[link.event.event_id]==1}
        done = set(self.commits) | set(self.rejected) | valid_links
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
        if self.links and not self.has_committed():return '已人工关联已有牌，未新增发牌'
        return "识牌候选已处理"

    def rebind_current_round(self) -> None:
        self._require_active()
        if self.has_committed() or self.links:
            raise VisionBridgeError("已有入账或人工关联，不能把旧任务改绑到新轮；请重新打开图片")
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
        if decision.operation == OP_LINK:
            if not decision.target_event_id:
                raise VisionBridgeError('请明确选择已有的发牌记录；不能按牌面猜测身份。')
            if any(value is not None for value in (decision.confirmed_rank,decision.suit,decision.seat,decision.hand_id)):
                raise VisionBridgeError('关联只确认同一张牌，不改变牌级、花色或手牌归属。')
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
            "target_event_id": decision.target_event_id,
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

    def existing_card_targets(self):
        """Current replayed cards, including current location after a split."""
        self._require_active()
        self._require_fresh_context()
        table = self.ctrl.state().current.table
        events = {e.event_id:e for e in self.ctrl.ledger.events if e.etype == CARD_DEALT
            and e.session_id == self.bound['session_id'] and e.shoe_id == self.bound['shoe_id']
            and e.round_id == self.bound['round_id'] and e.confirm_status == CONFIRMED}
        targets = []
        for seat in [table.dealer, *table.players.values()]:
            for hand in seat.hands:
                for card in hand.cards:
                    event = events.get(card.event_id)
                    if event is not None:
                        targets.append({'event_id':event.event_id,'seq':event.seq,'seat':seat.name,
                            'hand_id':hand.hand_id,'rank':card.rank,'suit':card.suit})
        return sorted(targets,key=lambda target:target['seq'])

    def _dealt_target(self, event):
        events = {e.event_id:e for e in self.ctrl.ledger.events}
        visited = set()
        while event is not None and event.event_id not in visited:
            visited.add(event.event_id)
            if event.etype == CARD_DEALT:return event.event_id
            if event.etype not in (CARD_REVEALED,CORRECTION):return None
            event = events.get(event.payload.get('target_event_id'))
        return None

    def _save_identity_action(self, action, decision, obs, target):
        record = {'schema':'manual-observation-link-1','action':action,'bound':dict(self.bound),
            'observation':obs.as_dict(),'target':target,'writes_ledger':False,
            'previous_request_id':self._link_history.get(obs.observation_id),
            'reason':decision.reason,'automatic_identity_claim':False}
        request_id = _sha(record)
        if self.evidence_root is not None:
            folder = self.evidence_root/'identity-links';folder.mkdir(parents=True,exist_ok=True)
            path = folder/f'{request_id}.json'
            if path.exists():
                if json.loads(path.read_text(encoding='utf-8')) != record:
                    raise VisionBridgeError('已有人工关联凭据不一致，未覆盖该文件。')
            else:
                with path.open('x',encoding='utf-8') as handle:
                    json.dump(record,handle,ensure_ascii=False,sort_keys=True,indent=2)
            head = self._link_head(obs.observation_id)
            temporary = head.with_name(head.name+'.tmp-'+uuid4().hex)
            temporary.write_text(json.dumps({'request_id':request_id}),encoding='utf-8')
            os.replace(temporary,head)
        self._link_history[obs.observation_id] = request_id
        return request_id

    def _link_head(self, observation_id):
        key = _sha({'bound':self.bound,'observation_id':observation_id})
        return self.evidence_root/'identity-links'/f'current-{key}.json'

    def _restore_link(self, obs):
        if self.evidence_root is None:return
        head = self._link_head(obs.observation_id)
        if not head.exists():return
        try:
            request_id = json.loads(head.read_text(encoding='utf-8'))['request_id']
            if not isinstance(request_id,str) or len(request_id)!=32 or any(c not in '0123456789abcdef' for c in request_id):
                raise ValueError('invalid request identity')
            record = json.loads((head.parent/f'{request_id}.json').read_text(encoding='utf-8'))
            if (_sha(record)!=request_id or record['schema']!='manual-observation-link-1'
                    or record['bound']!=self.bound or record['observation']['observation_id']!=obs.observation_id
                    or record['writes_ledger'] is not False or record['action'] not in ('link','unlink-and-reject')):
                raise ValueError('link evidence mismatch')
            self._link_history[obs.observation_id] = request_id
            if record['action']=='unlink-and-reject':return
            event = next(e for e in self.ctrl.ledger.events if e.event_id==record['target']['event_id'])
            if (event.etype!=CARD_DEALT or event.confirm_status!=CONFIRMED or event.session_id!=self.bound['session_id']
                    or event.shoe_id!=self.bound['shoe_id'] or event.round_id!=self.bound['round_id']):
                raise ValueError('link target context mismatch')
            self.links[obs.observation_id] = CommitResult('linked',request_id,obs.observation_id,True,event,
                '已恢复此前人工关联；未新增发牌。')
        except (OSError,ValueError,KeyError,TypeError,StopIteration) as exc:
            raise VisionBridgeError('既有人工关联凭据不完整，不能假装没有关联并再次扣牌。') from exc

    def _link_existing(self, decision, obs):
        targets = {t['event_id']:t for t in self.existing_card_targets()}
        target = targets.get(decision.target_event_id)
        if target is None:
            raise VisionBridgeError('目标不是本轮仍有效的已确认发牌记录，请刷新后选择。')
        previous_commit = self.commits.get(obs.observation_id)
        if previous_commit and self._dealt_target(previous_commit.event) != target['event_id']:
            raise VisionBridgeError('此观察已经写入另一条记录，不能通过重新关联掩盖；请走账本纠错或撤销。')
        for oid,commit in {**self.links,**self.commits}.items():
            if oid != obs.observation_id and oid in self._by_id and self._dealt_target(commit.event) == target['event_id']:
                raise VisionBridgeError('本帧另一个候选已对应此牌，不能把两个候选合成同一条发牌记录。')
        previous = self.links.get(obs.observation_id)
        if previous and previous.event.event_id == target['event_id']:
            return CommitResult('linked',previous.request_id,obs.observation_id,True,previous.event,
                '该人工关联已保存，未再次扣牌。')
        request_id = self._save_identity_action('link',decision,obs,target)
        event = next(e for e in self.ctrl.ledger.events if e.event_id == target['event_id'])
        result = CommitResult('linked',request_id,obs.observation_id,False,event,
            f"已关联已有发牌 #{target['seq']}（{target['seat']} / {target['rank']}）；未新增发牌、未改牌面。")
        self.links[obs.observation_id] = result
        self.rejected.pop(obs.observation_id,None)
        return result

    def confirm(self, decision: ConfirmDecision) -> CommitResult:
        self._require_active()
        if decision.operation not in OPS:
            raise VisionBridgeError(f"不支持的确认操作: {decision.operation}")
        obs = self._observation(decision.observation_id)
        self._validate_decision(decision, obs)
        if decision.operation == OP_LINK:
            return self._link_existing(decision,obs)
        if decision.operation == OP_REJECT:
            if decision.observation_id in self.commits and self.commits[decision.observation_id].event:
                raise VisionBridgeError("该观察已入账，不能用拒绝掩盖；如需撤销请走账本撤销")
            linked = self.links.get(obs.observation_id)
            if linked is not None:
                self._save_identity_action('unlink-and-reject',decision,obs,{'event_id':linked.event.event_id})
                self.links.pop(obs.observation_id)
            self.rejected[decision.observation_id] = decision
            result = CommitResult(
                status="rejected", request_id=self.request_id(decision),
                observation_id=decision.observation_id, already_saved=False,
                message="已拒绝该候选并撤回人工关联；原账本记录保留。" if linked else "已拒绝该候选，未写入账本",
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
        linked = self.links.get(obs.observation_id)
        if linked is not None:
            if decision.operation == OP_NEW:
                raise VisionBridgeError('该观察已关联已有牌，不能再次作为新牌扣除；关联错误时先撤回关联。')
            if decision.target_event_id != linked.event.event_id:
                raise VisionBridgeError('揭示或纠错必须针对该观察已关联的原发牌记录。')
        if previous and decision.operation == OP_NEW and previous.event.etype != CARD_DEALT:
            raise VisionBridgeError('该观察已经用于揭示或纠错，不能随后当作新发牌重复扣除。')

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
