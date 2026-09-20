"""UI 编排：预演 -> SQLite事务 -> 发布状态；失败不污染内存或历史。"""
from __future__ import annotations
import copy
import uuid
from pathlib import Path
from ..core.rules import RuleProfile
from ..ledger.events import (CANDIDATE, CONFIRMED, SOURCE_MANUAL, CARD_DEALT,
                             PLAYER_ACTION, PEEK_NEGATIVE, UNDO, CORRECTION, SHOE_CREATED)
from ..ledger.ledger import EventLedger, LedgerError
from ..storage.database import LocalStore
from ..storage.export import import_json, import_csv
from ..storage.analysis_snapshots import AnalysisSnapshots
from ..analysis.information import build_input
from ..storage.safe_files import atomic_write
from .deal_entry import (RoundEntryPlan, MODE_INITIAL, MODE_MANUAL, MODE_CONTINUATION,
                        MODE_PEEK_WAIT, MODE_UNALIGNED, dealer_up_requires_peek,
                        next_open_hand, participating_in_order)
from ..analysis.contracts import digest
from ..core.table import ACTION_SPLIT, TableError
import json


class SessionController:
    def __init__(self, db_path: str | Path, recording_source=SOURCE_MANUAL):
        self.store = LocalStore(db_path)
        self.commit_revision = 0
        self._context_revision = 0
        self._context_listeners = []
        self.recording_source = recording_source
        self.analysis_store = AnalysisSnapshots(str(Path(db_path).resolve()) + ".analysis")
        self.session_id = uuid.uuid4().hex
        self.session_name = "手动录牌会话"
        self.ledger = EventLedger(self.session_id)
        self.ledger.start_session().source = recording_source
        self.entry_plan = None
        try:
            self.store.save_ledger(self.ledger)
        except Exception:
            self.store.close()
            raise

    @classmethod
    def recover(cls, db_path, session_id):
        obj = cls.__new__(cls)
        obj.store = LocalStore(db_path)
        obj.commit_revision = 0
        obj._context_revision = 0
        obj._context_listeners = []
        obj.recording_source = SOURCE_MANUAL
        obj.analysis_store = AnalysisSnapshots(str(Path(db_path).resolve()) + ".analysis")
        obj.entry_plan = None
        try:
            obj.load_session(session_id)
        except Exception:
            obj.store.close()
            raise
        return obj

    def load_session(self, session_id):
        candidate = self.store.load_ledger(session_id)
        if not candidate.events:
            raise LedgerError("此会话没有可恢复事件")
        self.ledger = candidate
        self.session_id = session_id
        self.session_name = next((s["name"] for s in self.store.list_sessions() if s["session_id"] == session_id), "恢复会话")
        self._load_entry_plan()
        self._publish_context_change()

    @property
    def context_token(self):
        """Read authoritative current identity without depending on any view cache."""
        return (self.session_id, self._context_revision, self.commit_revision,
                self.ledger.events[-1].event_id if self.ledger.events else None)

    def add_context_listener(self, listener):
        self._context_listeners.append(listener)

    def remove_context_listener(self, listener):
        self._context_listeners.remove(listener)

    def _publish_context_change(self):
        self._context_revision += 1
        self.context_warning = ""
        # The durable commit and in-memory publication have already succeeded.
        # Notification errors must never roll back or repeat that commit.
        for listener in tuple(self._context_listeners):
            try:
                listener()
            except Exception as error:
                self.context_warning = f"记录已保存；视图通知失败，请刷新，不要重复录入：{error}"

    def list_recoverable(self):
        return self.store.list_sessions()

    def _apply(self, method, *args, _entry_update=None, **kwargs):
        candidate = copy.deepcopy(self.ledger)
        event = getattr(candidate, method)(*args, **kwargs)
        if event.event_id not in self.ledger._ids:
            event.source = self.recording_source
        self.store.save_event(event)
        self.ledger = candidate
        self.commit_revision += 1
        # A durable event always gets its receipt, even when the derived sidecar
        # or a view fails. No listener can interrupt the plan synchronization.
        try:
            if _entry_update:
                _entry_update(event)
            else:
                self._sync_entry_event(event)
            self.save_entry_plan()
        except Exception as error:
            self._pause_entry_recovery(f"记录已保存，发牌位置未能保存；不要重复录入：{error}")
        self._publish_context_change()
        return event

    def new_shoe(self, rules: RuleProfile):
        return self._apply("create_shoe", rules)

    def start_round(self, participants=None, *, my_seat=None, deal_direction="forward"):
        seg = self.state().current
        selected = participants if participants is not None else seg.table.players
        ordered = participating_in_order(selected, deal_direction)
        seat = my_seat or ordered[0]
        if seat not in ordered:
            raise ValueError("本人座位必须是本轮参与座位之一")
        if deal_direction not in ("forward", "reverse"):
            raise ValueError("未知发牌方向")
        def freeze(_event):
            seg = self.state().current
            self.entry_plan = RoundEntryPlan.freeze(
                session_id=self.session_id, shoe_id=seg.shoe_id, round_id=seg.round_id,
                selected_seats=seg.table.participants, my_seat=seat,
                deal_direction=deal_direction)
            self.entry_warning = ""
        return self._apply("start_round", list(ordered), _entry_update=freeze)

    def end_round(self):
        event = self._apply("end_round", settle=True, observation_status="complete")
        seg = self.state().current
        return event, [r for r in seg.settlements if r["round"] == seg.table.round_no]

    def end_round_unsettled(self, reason, observation_status="unknown"):
        if not reason.strip():
            raise ValueError("未结算结束必须记录原因")
        return self._apply("end_round", settle=False, reason=reason, observation_status=observation_status)

    def end_shoe(self):
        return self._apply("end_shoe")

    def deal_shown(self, seat, rank, hand_id=None, suit=None, track_id=None, confirm_status=CONFIRMED):
        return self._apply("deal", seat, rank, hand_id=hand_id, suit=suit,
                           track_id=track_id, confirm_status=confirm_status)

    def deal_hidden(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, hidden=True, hand_id=hand_id, track_id=track_id)

    def deal_unknown(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, unknown=True, hand_id=hand_id,
                           track_id=track_id, confirm_status=CANDIDATE)

    def reveal(self, target_event_id, rank, suit=None):
        return self._apply("reveal", target_event_id, rank, suit=suit)

    def player_action(self, seat, hand_id, action, extra=None):
        return self._apply("player_action", seat, hand_id, action, extra)

    def peek_negative(self):
        return self._apply("peek_negative")

    def burn(self, count, note=""):
        return self._apply("burn", count, note)

    def mark_gap(self, reason):
        return self._apply("gap", reason)

    def undo_last(self, reason=""):
        return self._apply("undo_last", reason)

    def correct(self, event_id, payload_fix, reason):
        if not reason.strip():
            raise ValueError("纠错必须填写依据或原因")
        return self._apply("correct", event_id, payload_fix, reason)

    def import_file(self, path):
        candidate = import_csv(path) if Path(path).suffix.lower() == ".csv" else import_json(path)
        self.store.save_ledger(candidate)  # 全部验证后原子写入；不覆盖同ID不同内容
        self.ledger = candidate
        self.session_id = candidate.session_id
        self.session_name = "导入会话"
        self.commit_revision += 1
        self._load_entry_plan()
        self._publish_context_change()
        return candidate

    def analysis_input(self, seat, hand_id=None, through_seq=None, *, other_players_stand=False):
        return build_input(self.ledger, seat, hand_id, through_seq, other_players_stand=other_players_stand)

    def recompute_input(self, saved):
        original = saved["result"]["input"]
        ledger = self.store.load_ledger(original["session_id"], original["through_seq"])
        if not self.analysis_store.matches_prefix(saved, ledger):
            raise LedgerError("原分析关联的事件前缀摘要不匹配，拒绝复算")
        from ..analysis.seat_scenario import MODEL
        conditional = json.loads(original['information_json']).get('seat_scenario', {}).get('model') == MODEL
        return build_input(ledger, original["seat"], original["hand_id"], original["through_seq"],
                           other_players_stand=conditional)

    def export_diagnostic(self, session_id, path):
        data = self.store.diagnose_session(session_id)
        return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))

    def state(self):
        return self.ledger.replay()

    def current_rules(self):
        seg = self.state().current
        return seg.rules if seg else None

    def shoe_count(self):
        return len(self.state().segments)

    def _entry_plan_dir(self):
        root = Path(self.store.db_path).resolve()
        return root.with_name(root.name + ".deal_plans")

    def _current_round_id(self):
        seg = self.state().current
        return getattr(seg, "round_id", None) if seg else None

    def _entry_plan_path(self, round_id=None):
        rid = round_id or self._current_round_id() or "unaligned"
        return self._entry_plan_dir() / f"{self.session_id}.{rid}.json"

    def live_card_event_ids(self):
        from ..ledger.events import CARD_DEALT
        voided = self.ledger._voided_ids()
        return [ev.event_id for ev in self.ledger.events
                if ev.etype == CARD_DEALT and ev.event_id not in voided
                and ev.round_id == self._current_round_id()]

    def _pause_entry_recovery(self, reason):
        seg = self.state().current
        old = self.entry_plan
        self.entry_plan = RoundEntryPlan.unaligned(
            session_id=self.session_id, shoe_id=getattr(seg, "shoe_id", ""),
            round_id=getattr(seg, "round_id", ""), my_seat=old.my_seat if old else "玩家1")
        if old and old.session_id == self.session_id and old.round_id == getattr(seg, "round_id", None):
            self.entry_plan.last_saved = old.last_saved
        self.entry_plan.pause_reason = reason
        self.entry_warning = reason

    def _next_entry_hand(self):
        plan = self.entry_plan
        return next_open_hand(self.state().current.table, plan.participating_seats)

    def _advance_entry_hand(self):
        target = self._next_entry_hand()
        if target is None:
            self.entry_plan.enter_dealer_phase()
        else:
            self.entry_plan.enter_continuation(*target)

    def _sync_entry_event(self, event):
        plan = self.entry_plan
        if event.etype == SHOE_CREATED:
            self.entry_plan = None
            self.entry_warning = ""
            return
        if plan is None:
            return
        seg = self.state().current
        if (plan.session_id, plan.shoe_id, plan.round_id) != (self.session_id, seg.shoe_id, seg.round_id):
            self._pause_entry_recovery("账本上下文已改变，请核对录入位置")
            return
        if plan.mode == MODE_UNALIGNED:
            return
        payload = event.payload
        if event.etype == CARD_DEALT:
            seat = payload["seat"]
            hand = seg.table.seat(seat).get_hand(payload["hand_id"])
            ordinal = seg.table.seat(seat).hands.index(hand) + 1
            slot = plan.slot()
            if (plan.mode in (MODE_INITIAL, MODE_MANUAL) and slot
                    and slot.slot_id not in plan.filled_slots and slot.seat == seat
                    and slot.expected_face == payload["face_state"]
                    and not hand.from_split and len(hand.cards) == slot.ordinal):
                plan.mark_filled(slot.slot_id, event.event_id, payload.get("rank"), payload["face_state"])
                if plan.initial_complete():
                    plan.mode, plan.paused = MODE_INITIAL, False
                plan.advance_after_initial_success(lambda up: dealer_up_requires_peek(seg.rules, up))
                if plan.mode == MODE_CONTINUATION:
                    self._advance_entry_hand()
            else:
                plan.record_continuation_card(seat, event.event_id, payload.get("rank"), ordinal)
                plan.last_saved.ordinal = len(hand.cards)
                if plan.mode == MODE_INITIAL:
                    plan.pause_manual("显式录入与初始槽位不同；请人工核对", slot.slot_id if slot else None)
                elif plan.mode == MODE_CONTINUATION:
                    plan.continuation_hand_id = hand.hand_id
                    if hand.is_closed or hand.from_split and seg.table.split_hand_closed(hand):
                        self._advance_entry_hand()
        elif event.etype == PLAYER_ACTION:
            if (payload["action"] == ACTION_SPLIT and plan.slots) or plan.mode == MODE_CONTINUATION:
                self._advance_entry_hand()
        elif event.etype == PEEK_NEGATIVE and plan.mode == MODE_PEEK_WAIT:
            self._advance_entry_hand()
        elif event.etype == UNDO:
            target = payload.get("target_event_id")
            reopened = plan.undo_event(target)
            plan.observed_card_ids = self.live_card_event_ids()
            plan.reconcile(plan.observed_card_ids)
            if reopened is None and plan.mode == MODE_CONTINUATION:
                self._advance_entry_hand()
            elif reopened is None and plan.mode == "dealer_phase":
                self._advance_entry_hand()
        elif event.etype == CORRECTION:
            self._pause_entry_recovery("已保存纠错；请核对当前阶段与录入位置")

    def save_entry_plan(self):
        try:
            self._write_entry_plan()
        except Exception as error:
            self._pause_entry_recovery(f"发牌位置保存失败；录入已暂停，请核对已保存记录：{error}")
            raise

    def _write_entry_plan(self):
        if self.entry_plan is None:
            return
        # Certify the entire append-only prefix, including corrections and undo,
        # rather than guessing from card counts or the last filled slot.
        plan = self.entry_plan
        plan.ledger_seq = self.ledger.events[-1].seq
        plan.ledger_digest = digest(self.ledger.to_list())
        if plan.mode != MODE_UNALIGNED:
            plan.reconcile(self.live_card_event_ids())
        payload = json.dumps(plan.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        path = self._entry_plan_path(plan.round_id or "unaligned")
        if getattr(self, "_preserve_plan_path", None) == path and path.exists():
            atomic_write(path.with_suffix(f".preserved-{uuid.uuid4().hex}.json"), path.read_bytes())
            self._preserve_plan_path = None
        atomic_write(path,
                     payload.encode("utf-8"))

    def _load_entry_plan(self):
        self.entry_plan = None
        self.entry_warning = ""
        self._preserve_plan_path = None
        seg = self.state().current
        shoe_id = getattr(seg, "shoe_id", "") if seg else ""
        round_id = getattr(seg, "round_id", "") if seg else ""
        path = self._entry_plan_path(round_id or None)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            plan = RoundEntryPlan.from_dict(data)
            if (plan.session_id, plan.shoe_id, plan.round_id) != (self.session_id, shoe_id, round_id):
                raise ValueError("计划与会话、牌靴或轮次不匹配")
            if (plan.ledger_seq != self.ledger.events[-1].seq
                    or plan.ledger_digest != digest(self.ledger.to_list())):
                raise ValueError("账本与计划的事件前缀不同步；已保存牌不能重录")
            if plan.participating_seats and plan.participating_seats != tuple(seg.table.participants):
                raise ValueError("冻结参与顺序与账本不同")
            if plan.mode != MODE_UNALIGNED and set(plan.observed_card_ids) != set(self.live_card_event_ids()):
                raise ValueError("计划的已处理发牌事件集合与账本不同")
            for slot_id, event_id in plan.filled_slots.items():
                slot, event = plan.slot(slot_id), self.ledger._find(event_id)
                prior_deals = [e for e in self.ledger.events
                               if e.event_id in plan.observed_card_ids
                               and e.seq <= event.seq and e.payload.get("seat") == slot.seat]
                if (event.etype != CARD_DEALT or event.round_id != round_id
                        or event.payload.get("seat") != slot.seat
                        or event.payload.get("face_state") != slot.expected_face
                        or len(prior_deals) != slot.ordinal):
                    raise ValueError("发牌槽与对应账本事件的归属不符")
            if plan.continuation_hand_id:
                seat = seg.table.seat(plan.continuation_seat)
                hand = seat.get_hand(plan.continuation_hand_id)
                if seat.hands.index(hand) + 1 != plan.continuation_hand_ordinal:
                    raise ValueError("录入焦点的手牌序号不符")
            plan.reconcile(self.live_card_event_ids())
            self.entry_plan = plan
        except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError,
                TableError, LedgerError) as error:
            self._preserve_plan_path = path
            self._pause_entry_recovery(f"账本已恢复；发牌计划需核对：{error}")

    def close(self):
        self.store.close()
