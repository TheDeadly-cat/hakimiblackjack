"""UI 编排：预演 -> SQLite事务 -> 发布状态；失败不污染内存或历史。"""
from __future__ import annotations
import copy
import uuid
from pathlib import Path
from ..core.rules import RuleProfile, CONFIRM_VERIFIED
from ..core.cards import is_natural_blackjack
from ..ledger.events import (CANDIDATE, CONFIRMED, SOURCE_MANUAL, CARD_DEALT,
                             PLAYER_ACTION, PEEK_NEGATIVE, UNDO, CORRECTION, SHOE_CREATED,
                             CARD_REVEALED, ROUND_STARTED, FACE_HIDDEN)
from ..ledger.ledger import EventLedger, LedgerError
from ..storage.database import LocalStore
from ..storage.export import import_json, import_csv
from ..storage.analysis_snapshots import AnalysisSnapshots
from ..analysis.information import build_input
from ..storage.safe_files import atomic_write
from .deal_entry import (RoundEntryPlan, MODE_INITIAL, MODE_MANUAL, MODE_CONTINUATION,
                        MODE_PEEK_WAIT, MODE_UNALIGNED, MODE_DEALER, SIMPLE_HOLE_CONTRACT, dealer_up_requires_peek,
                        next_open_hand, participating_in_order)
from ..analysis.contracts import digest
from ..core.table import ACTION_SPLIT, TableError, DEALER, PHASE_DEALING, PHASE_IN_PROGRESS
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

    def _apply(self, method, *args, _entry_update=None, _evidence=None, **kwargs):
        candidate = copy.deepcopy(self.ledger)
        event = getattr(candidate, method)(*args, **kwargs)
        if event.event_id not in self.ledger._ids:
            event.source = self.recording_source
        if _evidence is not None:
            event.evidence = _evidence
        self.store.save_event(event)
        self._accept_committed(candidate, [event], _entry_update)
        return event

    def _accept_committed(self, candidate, events, entry_update=None):
        self.ledger = candidate
        self.commit_revision += len(events)
        # A durable event always gets its receipt, even when the derived sidecar
        # or a view fails. No listener can interrupt the plan synchronization.
        try:
            for event in events:
                if entry_update:
                    entry_update(event)
                else:
                    self._sync_entry_event(event)
            self.save_entry_plan()
        except Exception as error:
            self._pause_entry_recovery(f"记录已保存，发牌位置未能保存；不要重复录入：{error}")
        self._publish_context_change()

    def new_shoe(self, rules: RuleProfile):
        return self._apply("create_shoe", rules)

    def _round_options(self, participants, my_seat, deal_direction, simple_hole):
        seg = self.state().current
        if seg is None or seg.closed:
            raise TableError('请先创建或恢复一靴记录')
        if type(simple_hole) is not bool:
            raise ValueError('简便暗牌设置必须明确启用或关闭')
        if simple_hole and not self._simple_rules_confirmed(seg):
            raise TableError('简便暗牌需要已确认的美式底牌、A/十点决策前检查和完整新牌靴；请先核对桌规或使用手动方式')
        if deal_direction not in ('forward', 'reverse'):
            raise ValueError('未知发牌方向')
        ordered = participating_in_order(participants if participants is not None else seg.table.players, deal_direction)
        if not ordered:
            raise ValueError('至少选择一位参与玩家')
        seat = my_seat or ordered[0]
        if seat not in ordered:
            raise ValueError('本人座位必须是本轮参与座位之一')
        evidence = json.dumps({'recording_contract': SIMPLE_HOLE_CONTRACT,
            'acknowledgement': '录完初始明牌即确认本轮初始发牌完成，包含已发但未知的庄家底牌'},
            ensure_ascii=False) if simple_hole else None
        def freeze(_event):
            seg = self.state().current
            self.entry_plan = RoundEntryPlan.freeze(
                session_id=self.session_id, shoe_id=seg.shoe_id, round_id=seg.round_id,
                selected_seats=seg.table.participants, my_seat=seat,
                deal_direction=deal_direction, simple_hole=simple_hole)
            self.entry_warning = ''
        return ordered, evidence, freeze

    def start_round(self, participants=None, *, my_seat=None, deal_direction='forward', simple_hole=False):
        ordered, evidence, freeze = self._round_options(participants, my_seat, deal_direction, simple_hole)
        return self._apply('start_round', list(ordered), _entry_update=freeze, _evidence=evidence)

    def round_completion_problem(self, seg=None):
        """Read-only settlement preflight. No events, plan changes or confirmations."""
        seg = seg or self.state().current
        if seg is None or seg.closed or seg.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
            return '没有可结算的当前轮'
        if seg.shoe.gap or seg.shoe.pending_candidates:
            return '观察缺口或待确认牌尚未核对，请到记录核对入口处理'
        missing = seg.table.missing_observations()
        if missing:
            labels = {'DEALER_INITIAL_MISSING': '庄家初始牌未录齐', 'PLAYER_INITIAL_MISSING': '玩家初始牌未录齐',
                      'PLAYER_DRAW_PENDING': '玩家尚有待补牌', 'DEALER_DRAW_PENDING': '庄家仍需补牌'}
            return '；'.join(dict.fromkeys(f"{m['seat']}：{labels[m['code']]}" for m in missing))
        try:
            copy.deepcopy(seg.table).settle()
        except (TableError, ValueError) as error:
            return str(error)
        return ''

    def complete_and_next_round(self, expected_round_id, participants=None, *, my_seat=None,
                                deal_direction='forward', simple_hole=False):
        seg = self.state().current
        if seg is None or not expected_round_id or seg.round_id != expected_round_id:
            raise TableError('当前轮已变化；本次收尾未重复提交，请核对当前轮')
        problem = self.round_completion_problem(seg)
        if problem:
            raise TableError(problem)
        ordered, evidence, freeze = self._round_options(participants, my_seat, deal_direction, simple_hole)
        candidate = copy.deepcopy(self.ledger)
        ended = candidate.end_round(settle=True, observation_status='complete')
        ended.source = self.recording_source
        results = [r for r in candidate.replay().current.settlements if r['round'] == seg.table.round_no]
        started = candidate.start_round(list(ordered))
        started.source = self.recording_source
        if evidence:
            started.evidence = evidence
        # Both commands are preflighted before the single SQLite transaction.
        self.store.save_ledger(candidate)
        self._accept_committed(candidate, [ended, started],
                               lambda event: freeze(event) if event.etype == ROUND_STARTED else None)
        return ended, started, results

    def end_round(self):
        event = self._apply("end_round", settle=True, observation_status="complete")
        seg = self.state().current
        return event, [r for r in seg.settlements if r["round"] == seg.table.round_no]

    def correct_recent_visible(self, event_id, rank, reason, expected_context):
        from .recent_entry import recent_visible
        if expected_context != self.context_token:
            raise TableError('记录已变化，请关闭改牌区后重新选择最近牌')
        recent = recent_visible(self)
        if recent is None or recent.event_id != event_id:
            raise TableError('最近可见牌已变化，请重新选择')
        if not reason.strip():
            raise ValueError('请填写纠错原因')
        plan, seg = self.entry_plan, self.state().current
        trusted = bool(plan and plan.mode not in (MODE_UNALIGNED, MODE_MANUAL)
                       and not plan.paused and not plan.unresolved_slots
                       and (plan.session_id, plan.shoe_id, plan.round_id) == (self.session_id, seg.shoe_id, seg.round_id)
                       and plan.ledger_digest == digest(self.ledger.to_list())
                       and set(plan.observed_card_ids) == set(self.live_card_event_ids()))
        saved_plan = copy.deepcopy(plan) if trusted else None
        def updated(event):
            if saved_plan is None:
                self._sync_entry_event(event)
                return
            self.entry_plan = saved_plan
            current = self.state().current
            dealer = current.table.dealer.hands
            if dealer and dealer[0].cards:
                saved_plan.dealer_up_rank = dealer[0].cards[0].rank
            if saved_plan.last_saved and saved_plan.last_saved.event_id == event_id:
                saved_plan.last_saved.rank = rank
            if saved_plan.initial_complete():
                if (dealer_up_requires_peek(current.rules, saved_plan.dealer_up_rank)
                        and not current.table.dealer_hole_checked_negative and not current.table._dealer_revealed()):
                    saved_plan.mode = MODE_PEEK_WAIT
                    saved_plan.continuation_seat = DEALER
                    saved_plan.pause_reason = '等待实际庄家检查结果；未写入确认非BJ'
                else:
                    self._advance_entry_hand()
        # The existing append/replay path validates shoe, splits, terminal
        # actions, peek facts and every later event before saving a correction.
        return self._apply('correct', event_id, {'rank': rank}, reason, _entry_update=updated)

    def end_round_unsettled(self, reason, observation_status="unknown"):
        if not reason.strip():
            raise ValueError("未结算结束必须记录原因")
        return self._apply("end_round", settle=False, reason=reason, observation_status=observation_status)

    def end_shoe(self):
        return self._apply("end_shoe")

    def deal_shown(self, seat, rank, hand_id=None, suit=None, track_id=None, confirm_status=CONFIRMED,
                   *, initial_slot_id=None):
        if self._should_add_initial_hole(seat, hand_id, initial_slot_id, confirm_status):
            candidate = copy.deepcopy(self.ledger)
            visible = candidate.deal(seat, rank, hand_id=hand_id, suit=suit, track_id=track_id,
                                     confirm_status=confirm_status, source=self.recording_source)
            hidden = candidate.deal(DEALER, hidden=True, source=self.recording_source,
                evidence=json.dumps({'recording_contract': SIMPLE_HOLE_CONTRACT,
                    'basis': '按已确认初始发牌流程自动登记，仅确认未知底牌存在，不表示已观察到牌面',
                    'trigger_event_id': visible.event_id}, ensure_ascii=False))
            # Both events are appended in the existing SQLite transaction. No
            # partial hand/hole publication if either insert fails.
            self.store.save_ledger(candidate)
            self._accept_committed(candidate, [visible, hidden])
            return visible
        return self._apply("deal", seat, rank, hand_id=hand_id, suit=suit,
                           track_id=track_id, confirm_status=confirm_status)

    @staticmethod
    def _simple_rules_confirmed(seg):
        return (seg is not None and seg.rules.confirm_status == CONFIRM_VERIFIED
                and seg.rules.american_hole_card is True and seg.rules.start_from_new_shoe is True
                and seg.rules.check_bj_when == 'before_player_actions_A_T')

    def simple_hole_active(self):
        plan = self.entry_plan
        # A hole-only cursor after undo or an untrusted initial sequence needs
        # an explicit manual confirmation; redraw/recovery never fills it.
        return bool(plan and plan.simple_hole and not plan.paused
                    and plan.mode not in (MODE_MANUAL, MODE_UNALIGNED)
                    and not (plan.mode == MODE_INITIAL and plan.slot()
                             and plan.slot().expected_face == FACE_HIDDEN))

    def _trusted_simple_plan(self):
        plan, seg = self.entry_plan, self.state().current
        return (self.simple_hole_active() and not plan.input_paused and self._simple_rules_confirmed(seg) and not seg.closed
                and (plan.session_id, plan.shoe_id, plan.round_id) == (self.session_id, seg.shoe_id, seg.round_id)
                and plan.ledger_digest == digest(self.ledger.to_list())
                and plan.ledger_seq == self.ledger.events[-1].seq
                and not plan.unresolved_slots and not seg.shoe.gap and not seg.shoe.pending_candidates)

    def _should_add_initial_hole(self, seat, hand_id, slot_id, confirm_status):
        plan = self.entry_plan
        if not plan or not plan.simple_hole or not self._trusted_simple_plan():
            return False
        slot, hole = plan.slot(), plan.hole_slot()
        slot_id = slot_id or (slot.slot_id if slot else None)
        if (plan.mode != MODE_INITIAL or slot is None or hole is None or slot.slot_id != slot_id
                or slot.seat != seat or slot.expected_face != 'shown' or slot.ordinal != 2
                or plan.unfilled_ids() != (slot_id, hole.slot_id) or confirm_status != CONFIRMED):
            return False
        seg = self.state().current
        dealer = seg.table.dealer.hands
        hands = seg.table.seat(seat).hands
        return (len(dealer) == 1 and len(dealer[0].cards) == 1 and not dealer[0].hidden_cards
                and len(hands) == 1 and len(hands[0].cards) == 1 and not hands[0].hidden_cards
                and (hand_id is None or hand_id == hands[0].hand_id)
                and not any(info['round_id'] == seg.round_id for info in seg.unresolved.values()))

    def simple_dealer_route(self, seat, hand_id=None):
        """Return the unique original hole event to reveal, or None for a new card."""
        if seat != DEALER or not self.simple_hole_active():
            return None
        plan, seg = self.entry_plan, self.state().current
        if plan.mode == MODE_INITIAL:
            return None  # The explicitly selected upcard slot is still ordinary input.
        if (not self._trusted_simple_plan() or not plan.initial_complete()
                or seg.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS)):
            raise TableError('庄家录入位置或观察记录需核对；请转人工方式明确选择发牌或揭示')
        waiting_peek = plan.mode == MODE_PEEK_WAIT and dealer_up_requires_peek(seg.rules, plan.dealer_up_rank)
        dealer_bj = seg.table.dealer.hands and is_natural_blackjack(seg.table.dealer.hands[0].ranks)
        if not waiting_peek and not dealer_bj and next_open_hand(seg.table, plan.participating_seats) is not None:
            raise TableError('尚未轮到庄家开牌；提前开牌请在工作台人工选择揭示')
        hands = seg.table.dealer.hands
        if len(hands) != 1 or hand_id not in (None, hands[0].hand_id):
            raise TableError('庄家手牌身份需核对，请转人工录入')
        unknown = [(eid, info) for eid, info in seg.unresolved.items()
                   if info['seat'] == DEALER and info['round_id'] == seg.round_id]
        if unknown:
            if (len(unknown) != 1 or unknown[0][1]['face_state'] != FACE_HIDDEN
                    or unknown[0][1]['hand_id'] != hands[0].hand_id or len(hands[0].cards) != 2):
                raise TableError('庄家未知牌存在歧义；请在工作台选中原事件并明确选择揭示')
            return unknown[0][0]
        if waiting_peek or len(hands[0].cards) < 2:
            raise TableError('庄家底牌状态需核对，请转人工录入')
        return None

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
        elif event.etype == CARD_REVEALED and plan.simple_hole and payload['seat'] == DEALER:
            if plan.mode == MODE_PEEK_WAIT:
                dealer = seg.table.dealer.hands[0]
                if is_natural_blackjack(dealer.ranks):
                    plan.enter_dealer_phase()
                else:
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
            if (plan.simple_hole and plan.initial_complete()
                    and dealer_up_requires_peek(seg.rules, plan.dealer_up_rank)
                    and not seg.table.dealer_hole_checked_negative and not seg.table._dealer_revealed()):
                plan.mode = MODE_PEEK_WAIT
                plan.continuation_seat = DEALER
                plan.pause_reason = '等待实际庄家检查结果；未写入确认非BJ'
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
            if plan.simple_hole:
                start = next((e for e in self.ledger.events if e.etype == ROUND_STARTED and e.round_id == round_id), None)
                if (start is None or json.loads(start.evidence or '{}').get('recording_contract') != SIMPLE_HOLE_CONTRACT
                        or not self._simple_rules_confirmed(seg)):
                    raise ValueError('简便暗牌缺少本轮已确认流程依据')
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
