"""Human-owned drafts. Capture and predictions have no ledger write authority."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
import json
import hashlib
from pathlib import Path
import time
from uuid import uuid4

from ..core.cards import RANKS
from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS
from ..ledger.events import CARD_DEALT, CARD_REVEALED, CONFIRMED
from ..storage.safe_files import atomic_write


class DraftError(ValueError):
    pass


@dataclass
class CardDraft:
    draft_id: str
    bound: dict
    seat: str
    hand_id: str
    rank: str | None = None
    face: str = "shown"
    operation: str = "new"
    target_id: str | None = None
    reason: str = ""
    status: str = "pending"
    blocked: str = ""
    source_epoch: str | None = None
    observed_at: float = field(default_factory=time.time)
    arrival_ns: int = field(default_factory=time.perf_counter_ns)
    original: dict = field(default_factory=dict)
    source_image: str | None = None
    crop_image: str | None = None
    event_id: str | None = None
    target_revision: tuple | None = None


class AssistedRecording:
    def __init__(self, ctrl, evidence_root=None, *, capacity=64):
        self.ctrl = ctrl
        self.root = Path(evidence_root or (str(ctrl.store.db_path) + ".assisted"))
        self.root.mkdir(parents=True, exist_ok=True)
        self.capacity = capacity
        self.items: dict[str, CardDraft] = {}
        self.selected_id = None
        self.seat, self.hand_id = "玩家1", None
        self.epoch = None
        self.source_guard = None
        self.manual_evidence = None
        self.source_issue = "未连接自动提示；可手动补牌"
        self.seen = set()
        self.overflow = 0
        self.actions = []
        self._cache_token = None
        self._cached_state = None
        self._restore()
        ctrl.add_context_listener(self.context_changed)

    def _restore(self):
        """Recover draft receipts, never infer a successful ledger commit."""
        entries = []
        for path in self.root.glob("*.json"):
            if path.name == "panel-settings.json":
                continue
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("schema") == "assisted-review-action-1":
                entries.append(record)
        for record in sorted(entries, key=lambda r: r["time"]):
            saved = record.get("draft")
            if not saved or saved["bound"]["session_id"] != self.ctrl.session_id:
                continue
            d = CardDraft(**saved)
            if d.target_revision is not None:
                d.target_revision = tuple(d.target_revision)
            if record["action"] == "discard-draft":
                d.status = "discarded"
            committed = next((e for e in self.ctrl.ledger.events if e.event_id == "assist-" + d.draft_id), None)
            if committed:
                d.status, d.event_id = "committed", committed.event_id
            if d.status == "pending":
                if d.source_epoch is not None:
                    d.blocked = "恢复的来源候选仅供回看；重新连接采集后生成新候选"
                elif d.bound != self.binding():
                    d.blocked = "恢复草稿不属于当前轮次"
            self.items[d.draft_id] = d

    def close(self):
        self.ctrl.remove_context_listener(self.context_changed)

    def binding(self):
        seg = self._state().current
        bound = {"session_id": self.ctrl.session_id,
                 "shoe_id": seg.shoe_id if seg and not seg.closed else None,
                 "round_id": seg.round_id if seg and not seg.closed else None}
        bound["active"] = bool(seg and not seg.closed and seg.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS))
        return bound

    def _state(self):
        if self._cache_token != self.ctrl.context_token:
            self._cached_state = self.ctrl.state()
            self._cache_token = self.ctrl.context_token
        return self._cached_state

    def table_for_round(self, round_id=None):
        replay = self._state()
        if round_id is None:
            return replay.current.table if replay.current else None
        return next((s.table if s.round_id == round_id else s.round_tables.get(round_id)
                     for s in replay.segments if s.round_id == round_id or round_id in s.round_tables), None)

    def context_changed(self):
        bound = self.binding()
        for draft in self.items.values():
            if draft.status == "pending" and draft.bound != bound:
                draft.blocked = "会话、牌靴或轮次已改变；请回看原图，旧草稿不能转入新轮"
        if self.hand_id and not any(self.hand_id == h.hand_id for h in self.hands(self.seat)):
            self.hand_id = None

    def hands(self, seat, round_id=None):
        table = self.table_for_round(round_id)
        return table.seat(seat).hands if table and (seat == "庄家" or seat in table.players) else []

    def target_hand(self, seat, hand_id=None, round_id=None):
        hands = self.hands(seat, round_id)
        if hand_id:
            if hands and hand_id not in {h.hand_id for h in hands}:
                raise DraftError("所选手牌已改变，请重新选择")
            return hand_id
        if len(hands) > 1:
            raise DraftError("已分牌，请明确选择哪一手")
        return hands[0].hand_id if hands else f"{round_id or self.binding()['round_id']}:{seat}:initial"

    @property
    def selected(self):
        return self.items.get(self.selected_id)

    @property
    def pending(self):
        return [d for d in self.items.values() if d.status == "pending"]

    def _record(self, action, draft=None, **extra):
        entry = {"schema": "assisted-review-action-1", "action": action,
                 "time": time.time(), "monotonic_ns": time.perf_counter_ns(),
                 "draft": asdict(draft) if draft else None, "human_training_approval": False, **extra}
        path = self.root / (uuid4().hex + ".json")
        atomic_write(path, json.dumps(entry, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))
        self.actions.append(entry)
        return str(path)

    def manual(self, *, seat=None, hand_id=None):
        bound = self.binding()
        if not bound["active"]:
            raise DraftError("请先在主工作台新建牌靴并开轮")
        if sum(d.source_epoch is None and not d.blocked for d in self.pending) >= 16:
            raise DraftError("已有 16 张手动草稿，请先处理或撤回其中一张")
        seat = seat or self.seat
        hand_id = hand_id if hand_id is not None else (self.hand_id if seat == self.seat else None)
        draft = CardDraft(uuid4().hex, bound, seat, self.target_hand(seat, hand_id))
        if self.manual_evidence is not None and not self.source_problem():
            source_image, metadata = self.manual_evidence(draft.draft_id)
            draft.source_image = source_image
            draft.original = {"manual_capture": metadata}
        self._record("manual-draft", draft)
        self.items[draft.draft_id] = draft
        self.selected_id = draft.draft_id
        return draft

    def connect(self, epoch, guard):
        for d in self.pending:
            if d.source_epoch is not None and d.source_epoch != epoch:
                d.blocked = "采集来源已改变，旧候选仅供回看"
        self.epoch, self.source_guard = epoch, guard
        self.source_issue = "等待来源画面"
        self.seen.clear()
        self._record("source-connected", epoch=epoch, bound=self.binding())

    def source_problem(self):
        if self.source_guard is None:
            return self.source_issue
        try:
            return self.source_guard() or ""
        except Exception as exc:
            return "来源验证失败：" + str(exc)

    def enqueue(self, *, key, rank, original, source_image, crop_image,
                observed_at=None, arrival_ns=None):
        # A tracking key suppresses repeat proposals only. It is never a
        # physical-card identity, and equal ranks never imply equal keys.
        scoped = (self.epoch, key)
        if scoped in self.seen:
            return None
        if sum(d.source_epoch is not None and not d.blocked for d in self.pending) >= self.capacity or len(self.seen) >= 20000:
            if not self.overflow:
                self._record("queue-overflow", bound=self.binding(), requires_observation_check=True)
            self.overflow += 1
            return None
        if self.source_problem():
            return None
        bound = self.binding()
        if not bound["active"]:
            return None
        # Seat is a visible, remembered human target; never an inferred fact.
        hand = self.target_hand(self.seat, self.hand_id)
        draft = CardDraft(uuid4().hex, bound, self.seat, hand,
            rank=rank if rank in RANKS else None, source_epoch=self.epoch,
            original=deepcopy(original), source_image=str(source_image), crop_image=str(crop_image),
            observed_at=observed_at if observed_at is not None else time.time(),
            arrival_ns=arrival_ns if arrival_ns is not None else time.perf_counter_ns())
        # PNG file identity complements raw pixel identity in capture metadata.
        draft.original["evidence_sha256"] = {
            "source_image": hashlib.sha256(Path(source_image).read_bytes()).hexdigest(),
            "crop_image": hashlib.sha256(Path(crop_image).read_bytes()).hexdigest(),
        }
        self._record("candidate-draft", draft)
        self.seen.add(scoped)
        self.items[draft.draft_id] = draft
        # Incoming work must not replace the currently displayed draft.
        if self.selected_id is None:
            self.selected_id = draft.draft_id
        return draft

    def select(self, draft_id):
        if draft_id not in self.items:
            raise DraftError("草稿不存在")
        self.selected_id = draft_id
        self._record("select", self.selected)

    def next(self):
        ids = [d.draft_id for d in self.pending if not d.blocked]
        if not ids:
            self.selected_id = None
        else:
            index = ids.index(self.selected_id) + 1 if self.selected_id in ids else 0
            self.selected_id = ids[index % len(ids)]
        self._record("next-or-skip", self.selected)

    def edit(self, **changes):
        d = self.selected
        if d is None or d.status != "pending" or d.blocked:
            raise DraftError("请先选择一张待核对草稿，或点击补牌")
        if set(changes) - {"rank", "face", "seat", "hand_id", "operation", "target_id", "reason"}:
            raise DraftError("不允许改写草稿来源或身份")
        for k, v in changes.items():
            setattr(d, k, v)

    def check(self, draft_id):
        d = self.items[draft_id]
        if d is not self.selected:
            raise DraftError("显示的候选已改变，请重新核对")
        if d.status != "pending":
            raise DraftError("这张草稿已经处理；没有再次入账")
        if d.blocked or d.bound != self.binding() or (not d.bound["active"] and d.operation not in ("correct", "move", "withdraw")):
            raise DraftError(d.blocked or "轮次已改变，请重新核对")
        if d.source_epoch is not None:
            problem = self.source_problem()
            if d.source_epoch != self.epoch or problem:
                raise DraftError(problem or "来源已改变")
        if d.target_revision is not None and d.target_revision != self.ctrl.context_token:
            raise DraftError("打开历史记录后账本有变化，请重新打开再纠错")
        if d.source_epoch is not None:
            for key, digest in d.original["evidence_sha256"].items():
                path = Path(getattr(d, key))
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise DraftError("原图证据缺失或被修改，不能确认此候选")
        return d

    def records(self, round_id=None):
        """Effective cards including post-split ownership and corrected history."""
        table = self.table_for_round(round_id)
        if table is None:
            return []
        out = []
        for seat in [table.dealer, *table.players.values()]:
            for hand in seat.hands:
                for card in hand.cards:
                    ev = self.ctrl.ledger._find(card.event_id)
                    source_image = next((d.source_image for d in self.items.values()
                                         if d.event_id == ev.event_id), None)
                    if source_image is None and ev.evidence:
                        try:
                            saved = json.loads(Path(ev.evidence).read_text(encoding="utf-8"))
                            source_image = saved.get("draft", {}).get("source_image") or saved.get("source_image")
                        except (OSError, ValueError, AttributeError):
                            pass
                    out.append({"event_id": ev.event_id, "seq": ev.seq, "seat": seat.name,
                        "round_id": ev.round_id, "shoe_id": ev.shoe_id, "round_no": table.round_no,
                        "hand_id": hand.hand_id, "rank": card.rank, "evidence": ev.evidence,
                        "face": self.ctrl.ledger.effective_payload(ev.event_id)["face_state"],
                        "source_image": source_image})
        return sorted(out, key=lambda r: r["seq"])

    def history(self, event_id):
        event = self.ctrl.ledger._find(event_id)
        target = next((r for r in self.records(event.round_id) if r["event_id"] == event_id), None)
        if target is None:
            raise DraftError("请选择仍有效的一张已记录牌")
        if sum(d.source_epoch is None and not d.blocked for d in self.pending) >= 16:
            raise DraftError("已有 16 张手动草稿，请先处理或撤回其中一张")
        d = CardDraft(uuid4().hex, self.binding(), target["seat"], target["hand_id"])
        d.operation = "reveal" if target["rank"] not in RANKS and event.round_id == d.bound["round_id"] and d.bound["active"] else "correct"
        d.target_id = event_id
        d.seat, d.hand_id = target["seat"], target["hand_id"]
        d.rank = target["rank"] if target["rank"] in RANKS else None
        d.face = "shown" if d.rank else target["face"]
        d.target_revision = self.ctrl.context_token
        d.original = {"record_before": deepcopy(target)}
        d.source_image = target["source_image"]
        self._record("history-draft", d)
        self.items[d.draft_id] = d
        self.selected_id = d.draft_id
        return d

    def confirm(self, draft_id):
        d = self.check(draft_id)
        if d.operation in ("correct", "move", "withdraw") and not d.reason.strip():
            raise DraftError("纠错或撤回重复牌需要填写依据/原因")
        if d.operation not in ("new", "reveal", "correct", "move", "withdraw", "link", "reject"):
            raise DraftError("未知操作")
        if "record_before" in d.original and d.operation in ("new", "reject"):
            raise DraftError("这是已记牌的修改草稿；新增牌请使用补牌，删除重复牌请用撤回重复牌")
        if d.operation in ("new", "correct", "reveal"):
            if d.face not in ("shown", "unknown", "hidden"):
                raise DraftError("请选择可见牌、未知牌或规则暗牌")
            if (d.face == "shown" or d.operation == "reveal") and d.rank not in RANKS:
                raise DraftError("请输入牌级，或明确记录一张未知牌")
        target = None
        if d.operation in ("link", "reveal", "correct", "move", "withdraw"):
            old = d.original.get("record_before")
            if old and d.target_id != old["event_id"]:
                raise DraftError("历史修改必须绑定刚打开的那张牌，不能换到另一张")
            targets = self.records(old["round_id"] if old else None)
            target = next((r for r in targets if r["event_id"] == d.target_id), None)
            if target is None:
                raise DraftError("目标发牌已撤回或不属于当前轮，请重新选择")
        evidence = self._record("confirmation-intent", d)
        event_id = "assist-" + d.draft_id
        meta = dict(event_id=event_id, evidence=evidence, observed_at=d.observed_at,
                    source="辅助工作台人工确认")
        try:
            if d.operation == "reject":
                event = None
            elif d.operation == "link":
                event = self.ctrl.ledger._find(d.target_id)
            elif d.operation == "new":
                common = dict(hand_id=d.hand_id, **meta)
                if d.face == "shown":
                    event = self.ctrl.deal_shown(d.seat, d.rank, **common)
                elif d.face == "hidden":
                    event = self.ctrl.deal_hidden(d.seat, **common)
                else:
                    event = self.ctrl.deal_unknown(d.seat, confirm_status=CONFIRMED, **common)
            elif d.operation == "reveal":
                event = self.ctrl.reveal(d.target_id, d.rank, **meta)
            else:
                if d.operation == "withdraw":
                    payload = {"withdrawn": True}
                    correction_target = d.target_id
                elif d.operation == "move":
                    payload = {"seat": d.seat, "hand_id": d.hand_id}
                    correction_target = d.target_id
                else:
                    correction_target = d.target_id
                    # Correct the reveal when a previously unknown card is now
                    # known, preserving what was unknown before that reveal.
                    reveals = [e for e in self.ctrl.ledger.events if e.etype == CARD_REVEALED
                        and e.payload["target_event_id"] == d.target_id and not self.ctrl.ledger.is_voided(e.event_id)]
                    if reveals:
                        if (d.seat, d.hand_id) != (target["seat"], target["hand_id"]) or d.face != "shown":
                            raise DraftError("已揭示牌请分开处理：在主工作台修正发牌归属，或在这里修正揭示牌级")
                        correction_target = reveals[-1].event_id
                        payload = {"rank": d.rank}
                    else:
                        payload = {"rank": d.rank if d.face == "shown" else None, "face_state": d.face}
                        # Unchanged current post-split ownership must not rewrite
                        # the original deal's pre-split hand identity.
                        if (d.seat, d.hand_id) != (target["seat"], target["hand_id"]):
                            payload.update(seat=d.seat, hand_id=d.hand_id)
                event = self.ctrl.correct(correction_target, payload, d.reason,
                    correction_id=event_id, evidence=evidence, observed_at=d.observed_at,
                    source="辅助工作台人工纠错")
        except Exception:
            # A listener may fail after SQLite succeeded. Never offer a retry
            # as a new deal: resolve by the immutable draft request id.
            event = next((e for e in self.ctrl.ledger.events if e.event_id == event_id), None)
            if event is None:
                raise
        d.event_id = event.event_id if event else None
        d.status = {"link": "linked", "reject": "rejected"}.get(d.operation, "committed")
        self._record("confirmation-result", d, ledger_written=d.operation not in ("link", "reject"))
        return event

    def discard(self):
        if self.selected and self.selected.status == "pending":
            self._record("discard-draft", self.selected)
            self.selected.status = "discarded"

    def lag_seconds(self):
        candidates = [d for d in self.pending if d.source_epoch is not None and not d.blocked]
        return max(0, (time.perf_counter_ns() - min(d.arrival_ns for d in candidates)) / 1e9) if candidates else 0
