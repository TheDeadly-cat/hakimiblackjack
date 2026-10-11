"""UI 编排：预演 -> SQLite事务 -> 发布状态；失败不污染内存或历史。"""
from __future__ import annotations
import copy
import uuid
from contextlib import contextmanager
from pathlib import Path
from ..core.rules import RuleProfile, CONFIRM_VERIFIED
from ..core.cards import is_natural_blackjack, is_ten_value
from ..ledger.events import (CANDIDATE, CONFIRMED, SOURCE_MANUAL, CARD_DEALT,
                             PLAYER_ACTION, PEEK_NEGATIVE, UNDO, CORRECTION, SHOE_CREATED,
                             CARD_REVEALED, ROUND_STARTED, ROUND_ENDED, SHOE_ENDED, FACE_HIDDEN)
from ..ledger.ledger import EventLedger, LedgerError
from ..storage.database import LocalStore
from ..storage.export import import_json, import_csv
from ..storage.analysis_snapshots import AnalysisSnapshots
from ..storage.opening_snapshots import OpeningSnapshots
from ..storage.sidebet_snapshots import SidebetSnapshots
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
        self.opening_store = OpeningSnapshots(str(Path(db_path).resolve()) + '.opening')
        self.sidebet_store = SidebetSnapshots(str(Path(db_path).resolve()) + '.sidebets')
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
        obj.opening_store = OpeningSnapshots(str(Path(db_path).resolve()) + '.opening')
        obj.sidebet_store = SidebetSnapshots(str(Path(db_path).resolve()) + '.sidebets')
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
        previous_key = id(self.ledger), self.context_token
        self._context_revision += 1
        # Retag only this callback's committed content, never a different ledger.
        if getattr(self, '_read_depth', 0):
            current_key = id(self.ledger), self.context_token
            if getattr(self, '_read_key', None) == previous_key:
                self._read_key = current_key
            if getattr(self, '_read_prefix_key', None) == previous_key:
                self._read_prefix_key = current_key
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
        candidate = self._command_candidate()
        event = getattr(candidate, method)(*args, **kwargs)
        if event.event_id not in self.ledger._ids:
            event.source = self.recording_source
        if _evidence is not None:
            event.evidence = _evidence
        self.store.append_validated(candidate)
        self._accept_committed(candidate, [event], _entry_update)
        return event

    def _accept_committed(self, candidate, events, entry_update=None):
        with self.read_frame():
            self._accept_committed_frame(candidate, events, entry_update)

    def _command_candidate(self):
        from ..ledger.command import CommandCandidate
        pending = getattr(self, '_recording_candidate', None)
        if pending is not None:
            # Consume exactly once. This candidate independently replayed the
            # complete baseline before queued-input planning began.
            self._recording_candidate = None
            candidate, ledger, context = pending
            from ..analysis.contracts import canonical
            if (self.ledger is not ledger or self.context_token != context
                    or canonical([event.to_dict() for event in self.ledger.events]).encode('utf-8')
                    != candidate._baseline.content):
                raise LedgerError('录牌输入规划期间基线已变化，请重新核对')
            return candidate
        return CommandCandidate(self.ledger, self.read_prefix())

    @contextmanager
    def _recording_command_frame(self):
        """Plan one queued input from its fresh independently validated candidate.

        Do not reuse externally exposed view state or any earlier command's
        replay. Every appended prefix and the SQL baseline remain validated.
        """
        if getattr(self, '_recording_command_active', False):
            raise RuntimeError('不能嵌套规划后台录牌输入')
        with self.read_frame():
            candidate = self._command_candidate()
            ledger, context = self.ledger, self.context_token
            previous = (getattr(self, '_read_key', None), getattr(self, '_read_snapshot', None),
                        getattr(self, '_read_prefix_key', None), getattr(self, '_read_prefix', None))
            self._recording_command_active = True
            self._recording_candidate = candidate, ledger, context
            try:
                self._read_snapshot = candidate.replay()
                self._read_key = id(ledger), context
                yield
            finally:
                self._recording_candidate = None
                self._recording_command_active = False
                # Failed or unused preparation cannot leak its private view.
                # A successful durable commit already published its new view.
                if self.ledger is ledger and self.context_token == context:
                    (self._read_key, self._read_snapshot,
                     self._read_prefix_key, self._read_prefix) = previous

    def _accept_committed_frame(self, candidate, events, entry_update=None):
        from ..ledger.command import CommandCandidate
        prepared = isinstance(candidate, CommandCandidate)
        # First-pass player adjustment retains its full save_ledger transaction
        # and passes a normal EventLedger. Publish either durable representation
        # before derived plan/view work; never strand a committed legacy batch.
        self.ledger = candidate.published_ledger() if prepared else candidate
        self.commit_revision += len(events)
        if prepared:
            self._read_key = id(self.ledger), self.context_token
            self._read_snapshot = candidate.replay()
            # Bind the already validated, durably committed state to owned full
            # prefix bytes. Analysis views may share this exact current frame.
            from .read_snapshot import PrefixSnapshot
            self._read_prefix = PrefixSnapshot.capture(self.ledger)
            self._read_prefix_key = self._read_key
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

    def publish_recording_receipts(self, receipts, expected_context, expected_ledger, verified_token=None):
        """Publish this process's SQLite-owner receipts on the UI thread.

        Every command has already traversed independent candidate replay and
        SQLite baseline/transaction validation in the owner. Verify the full
        before/after bytes and FIFO chain here; do not execute or save it again.
        The prepared read state is shared only for this callback's painting.
        """
        from .read_snapshot import PrefixSnapshot
        if not getattr(self, '_read_depth', 0):
            raise RuntimeError('录牌回执必须在界面读取帧内发布')
        if self.context_token != expected_context or self.ledger is not expected_ledger:
            raise LedgerError('等待录牌回执时当前会话已改变；请读取已保存记录核对')
        if not receipts or any(r.status != 'committed' for r in receipts):
            raise LedgerError('仅能发布后台已保存的录牌回执')
        prefix = PrefixSnapshot.capture(self.ledger)
        event_ids = []
        for receipt in receipts:
            if (receipt.chain_id != receipts[0].chain_id or receipt.before.session_id != self.session_id
                    or receipt.after.session_id != self.session_id or receipt.before.content != prefix.content):
                raise LedgerError('录牌回执的完整前缀或顺序不符；请读取已保存记录核对')
            prefix = receipt.after
            event_ids.extend(receipt.event_ids)
        last = receipts[-1]
        transport_verified = verified_token is not None and all(r.verified_token is verified_token for r in receipts)
        if ((not transport_verified and PrefixSnapshot.capture(last.ledger).content != prefix.content) or last.state is None
                or len(set(event_ids)) != len(event_ids) or not event_ids
                or tuple(e.event_id for e in last.ledger.events[-len(event_ids):]) != tuple(event_ids)):
            raise LedgerError('录牌回执内容与已保存事件不符；请读取已保存记录核对')
        self.ledger = last.ledger
        self.entry_plan = copy.deepcopy(last.entry_plan)
        self.entry_warning = last.entry_warning
        self.commit_revision += len(event_ids)
        self._read_key = id(self.ledger), self.context_token
        self._read_snapshot = last.state
        self._read_prefix_key, self._read_prefix = self._read_key, last.after
        self._publish_context_change()

    def new_shoe(self, rules: RuleProfile):
        return self._apply("create_shoe", rules)

    def replace_shoe(self, rules: RuleProfile, expected_context):
        """Explicit user switch: preserve the old round, close it without payout, create a fresh shoe."""
        if expected_context != self.context_token:
            raise TableError('记录已变化，请重新核对新建牌靴')
        seg = self.state().current
        if seg is None or seg.closed:
            return self.new_shoe(rules)
        prefix = self.read_prefix()
        prefix_digest = prefix.prefix_digest
        plan = self.entry_plan
        saved_plan = (plan.to_dict() if plan and plan.ledger_digest == prefix_digest
                      and plan.ledger_seq == prefix.through_seq else None)
        candidate = self._command_candidate()
        events = []
        if seg.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
            events.append(candidate.end_round(settle=False, observation_status='unknown',
                                               reason='用户主动换新牌靴；本轮未结算，原记录保留'))
        events.append(candidate.end_shoe())
        created = candidate.create_shoe(rules)
        events.append(created)
        created.evidence = json.dumps({'manual_shoe_change_v1': dict(
            event_ids=[event.event_id for event in events], previous_plan=saved_plan,
            prefix_digest=prefix_digest, prefix_seq=prefix.through_seq)}, ensure_ascii=False)
        # Resolve the command's canonical event objects before assigning provenance.
        events = [candidate._find(event.event_id) for event in events]
        for event in events:
            event.source = self.recording_source
        # The new rules and the complete transition are validated before any write.
        self.store.append_validated(candidate)
        def clear_plan(_event):
            self.entry_plan = None
            self.entry_warning = ''
        self._accept_committed(candidate, events, clear_plan)
        return created

    def _round_options(self, participants, my_seat, deal_direction, simple_hole):
        seg = self.state().current
        if seg is None or seg.closed:
            raise TableError('请先创建或恢复一靴记录')
        if type(simple_hole) is not bool:
            raise ValueError('简便暗牌设置必须明确启用或关闭')
        if simple_hole and not self._simple_rules_confirmed(seg):
            raise TableError('简便暗牌需要已确认的美式底牌及对应检查流程；请先核对桌规或使用手动方式')
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
        candidate = self._command_candidate()
        ended = candidate.end_round(settle=True, observation_status='complete')
        ended.source = self.recording_source
        results = [r for r in candidate.replay().current.settlements if r['round'] == seg.table.round_no]
        started = candidate.start_round(list(ordered))
        started.source = self.recording_source
        if evidence:
            started.evidence = evidence
        # Both commands are preflighted before the single SQLite transaction.
        self.store.append_validated(candidate)
        self._accept_committed(candidate, [ended, started],
                               lambda event: freeze(event) if event.etype == ROUND_STARTED else None)
        return ended, started, results

    def end_round(self):
        event = self._apply("end_round", settle=True, observation_status="complete")
        seg = self.state().current
        return event, [r for r in seg.settlements if r["round"] == seg.table.round_no]

    def correct_recent_visible(self, event_id, rank, reason, expected_context, *, suit=...):
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
                       and plan.ledger_digest == self.read_prefix().prefix_digest
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
        fix = {'rank': rank}
        if suit is not ...:
            fix['suit'] = suit
        return self._apply('correct', event_id, fix, reason, _entry_update=updated)

    def end_round_unsettled(self, reason, observation_status="unknown"):
        if not reason.strip():
            raise ValueError("未结算结束必须记录原因")
        return self._apply("end_round", settle=False, reason=reason, observation_status=observation_status)

    def end_shoe(self):
        return self._apply("end_shoe")

    def deal_shown(self, seat, rank, hand_id=None, suit=None, track_id=None, confirm_status=CONFIRMED,
                   *, initial_slot_id=None, auto_next=None):
        if self._should_add_initial_hole(seat, hand_id, initial_slot_id, confirm_status):
            previous_plan = self.entry_plan.to_dict()
            candidate = self._command_candidate()
            visible = candidate.deal(seat, rank, hand_id=hand_id, suit=suit, track_id=track_id,
                                     confirm_status=confirm_status, source=self.recording_source)
            hidden = candidate.deal(DEALER, hidden=True, source=self.recording_source,
                evidence=json.dumps({'recording_contract': SIMPLE_HOLE_CONTRACT,
                    'basis': '按已确认初始发牌流程自动登记，仅确认未知底牌存在，不表示已观察到牌面',
                    'trigger_event_id': visible.event_id,
                    'automatic_hole_undo_v1': {'previous_plan': previous_plan}}, ensure_ascii=False))
            # Both events are appended in the existing SQLite transaction. No
            # partial hand/hole publication if either insert fails.
            self.store.append_validated(candidate)
            self._accept_committed(candidate, [visible, hidden])
            return visible
        if auto_next is not None and seat == DEALER and confirm_status == CONFIRMED:
            return self._dealer_input('deal', seat, rank, hand_id=hand_id, suit=suit,
                                      track_id=track_id, auto_next=auto_next)
        return self._apply("deal", seat, rank, hand_id=hand_id, suit=suit,
                           track_id=track_id, confirm_status=confirm_status)

    @staticmethod
    def _simple_rules_confirmed(seg):
        # This certifies the physical initial-deal protocol, not the unseen
        # shoe composition. Complete-shoe/burn observations gate EV separately.
        return (seg is not None and seg.rules.confirm_status == CONFIRM_VERIFIED
                and seg.rules.american_hole_card is True
                and seg.rules.check_bj_when in ('before_player_actions_A_T', 'before_player_actions_A'))

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
                and plan.ledger_digest == self.read_prefix().prefix_digest
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

    def recording_dealer_route(self, seat, hand_id=None):
        """Reveal the original BCLC hole, including an observed Ace-peek result."""
        target = self.simple_dealer_route(seat, hand_id)
        if target or seat != DEALER:
            return target
        from ..analysis.split_contracts import BCLC_PROFILE
        plan, seg = self.entry_plan, self.state().current
        if (not plan or not seg or seg.rules.profile_id != BCLC_PROFILE
                or plan.mode not in (MODE_DEALER, MODE_PEEK_WAIT)
                or plan.paused or plan.input_paused or not plan.initial_complete() or plan.unresolved_slots):
            return None
        waiting_peek = plan.mode == MODE_PEEK_WAIT and dealer_up_requires_peek(seg.rules, plan.dealer_up_rank)
        if ((plan.session_id, plan.shoe_id, plan.round_id) != (self.session_id, seg.shoe_id, seg.round_id)
                or plan.ledger_digest != self.read_prefix().prefix_digest or seg.shoe.gap or seg.shoe.pending_candidates
                or (not waiting_peek and next_open_hand(seg.table, plan.participating_seats) is not None)):
            raise TableError('庄家开牌位置需核对；请在工作台选择原暗牌事件')
        holes = [(eid, info) for eid, info in seg.unresolved.items()
                 if info['seat'] == DEALER and info['round_id'] == seg.round_id]
        if not holes:
            return None
        hands, slot = seg.table.dealer.hands, plan.hole_slot()
        if (len(holes) != 1 or holes[0][1]['face_state'] != FACE_HIDDEN or not slot
                or plan.filled_slots.get(slot.slot_id) != holes[0][0] or len(hands) != 1
                or len(hands[0].cards) != 2 or hand_id not in (None, hands[0].hand_id)):
            raise TableError('初始暗牌身份不唯一；请在工作台明确选择要揭示的原事件')
        return holes[0][0]

    def _bclc_ace_bound_hole(self, plan, seg, prefix_seq, prefix_digest):
        """Read-only binding of the user's Ace-result protocol to this round."""
        from ..analysis.split_contracts import BCLC_PROFILE
        if (not plan or not seg or seg.closed or seg.rules.profile_id != BCLC_PROFILE
                or seg.rules.version != 2 or seg.rules.confirm_status != CONFIRM_VERIFIED
                or seg.rules.american_hole_card is not True
                or seg.rules.check_bj_when != 'before_player_actions_A'
                or seg.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS)
                or plan.mode != MODE_PEEK_WAIT or plan.dealer_up_rank != 'A'
                or plan.paused or plan.input_paused or plan.unresolved_slots
                or not plan.initial_complete() or seg.table.dealer_hole_checked_negative
                or (plan.session_id, plan.shoe_id, plan.round_id) !=
                   (self.session_id, seg.shoe_id, seg.round_id)
                or plan.ledger_seq != prefix_seq or plan.ledger_digest != prefix_digest
                or seg.shoe.gap or seg.shoe.pending_candidates):
            return None
        try:
            frozen = RoundEntryPlan.freeze(session_id=plan.session_id, shoe_id=plan.shoe_id,
                round_id=plan.round_id, selected_seats=plan.participating_seats,
                my_seat=plan.my_seat, deal_direction=plan.deal_direction, simple_hole=plan.simple_hole)
            if (plan.slots != frozen.slots or plan.participating_seats != tuple(seg.table.participants)
                    or len(seg.table.dealer.hands) != 1):
                return None
            cards = seg.table.dealer.hands[0].cards
            hole_slot = plan.hole_slot()
            if (len(cards) != 2 or cards[0].rank != 'A' or not cards[1].is_unknown or not hole_slot
                    or plan.filled_slots.get(hole_slot.slot_id) != cards[1].event_id):
                return None
            unresolved = [(eid, info) for eid, info in seg.unresolved.items() if info['round_id'] == seg.round_id]
            if (len(unresolved) != 1 or unresolved[0][0] != cards[1].event_id
                    or unresolved[0][1]['seat'] != DEALER or unresolved[0][1]['face_state'] != FACE_HIDDEN):
                return None
            live_ids = {card.event_id for seat in [seg.table.dealer, *seg.table.players.values()]
                        for hand in seat.hands for card in hand.cards}
            if set(plan.filled_slots.values()) != live_ids or set(plan.observed_card_ids) != live_ids:
                return None
            events = [self.ledger._find(plan.filled_slots[slot.slot_id]) for slot in plan.slots]
            if (events != sorted(events, key=lambda event: event.seq)
                    or any(event.etype != CARD_DEALT or event.seq > prefix_seq
                           or (event.session_id, event.shoe_id, event.round_id) !=
                              (self.session_id, seg.shoe_id, seg.round_id)
                           or event.payload.get('seat') != slot.seat
                           or event.payload.get('face_state') != slot.expected_face
                           for slot, event in zip(plan.slots, events))):
                return None
            return cards[1].event_id
        except (ValueError, KeyError, TypeError, AttributeError, TableError, LedgerError):
            return None

    def bclc_ace_input_ready(self):
        with self.read_frame():
            prefix = self.read_prefix()
            return bool(self._bclc_ace_bound_hole(self.entry_plan, self.state().current,
                                               prefix.through_seq, prefix.prefix_digest))

    def record_bclc_ace_input(self, kind, rank=None, suit=None, action=None, auto_next=None):
        """Save the user's observed Ace result and first player input atomically.

        Per the explicitly chosen recording convention, a ten-valued rank is
        the actually revealed dealer BJ. Other ranks/actions report an actual
        non-BJ notification and belong to the first unfinished player hand.
        """
        from .recording_commands import RecordingInputRejected
        with self.read_frame():
            prefix, seg, plan = self.read_prefix(), self.state().current, self.entry_plan
            hole = self._bclc_ace_bound_hole(plan, seg, prefix.through_seq, prefix.prefix_digest)
            if not hole:
                raise RecordingInputRejected('庄家A录牌位置尚未对齐；请核对已保存的原底牌')
            if kind in ('rank', 'card') and is_ten_value(rank):
                self.reveal(hole, rank, suit, auto_next=auto_next)
                return dict(message=f'已保存实际庄家Blackjack：A＋{rank}（揭示原底牌）',
                            round_changed=self.state().current.round_id != seg.round_id,
                            seat=DEALER, hand_id=seg.table.dealer.hands[0].hand_id)
            if kind not in ('rank', 'card', 'action'):
                raise RecordingInputRejected('庄家A等待实际结果；请录入实际牌或玩家动作')
            if kind in ('rank', 'card') and rank not in ('A', '2', '3', '4', '5', '6', '7', '8', '9'):
                raise RecordingInputRejected('请输入实际已见的点值；不能用未知牌确认非BJ')
            previous_plan = copy.deepcopy(plan.to_dict())
            candidate = self._command_candidate()
            peek = candidate.peek_negative()
            peek.source = self.recording_source
            peek.evidence = json.dumps(dict(recording_contract='bclc-ace-first-player-v1',
                acknowledgement='用户明确录法：此输入表示已实际收到庄家非BJ通知，并录入第一有效玩家操作'), ensure_ascii=False)
            current = candidate.replay().current
            acting = next_open_hand(current.table, plan.participating_seats)
            if not acting or not acting[1]:
                raise RecordingInputRejected('实际非BJ后没有待操作的玩家手；请核对本轮结果')
            seat, hand_id, _ordinal = acting
            if kind == 'action':
                states = current.table.action_states(seat, hand_id)
                if action not in states or not states[action].allowed:
                    reason = states[action].reason if action in states else '未知玩家动作'
                    raise RecordingInputRejected(f'{action} 不被允许：{reason}')
                try:
                    event = candidate.player_action(seat, hand_id, action)
                except TableError as error:
                    raise RecordingInputRejected(str(error)) from error
                message = f'已保存实际非BJ通知及{seat}动作：{action}'
            else:
                event = candidate.deal(seat, rank, hand_id=hand_id, suit=suit, source=self.recording_source)
                message = f'已保存实际非BJ通知及{seat} ← {rank}'
            peek, event = [candidate._find(item.event_id) for item in (peek, event)]
            peek.source = event.source = self.recording_source
            event.evidence = json.dumps(dict(bclc_ace_input_v1=dict(
                peek_event_id=peek.event_id, previous_plan=previous_plan, kind=kind,
                rank=rank if kind in ('rank', 'card') else None,
                action=action if kind == 'action' else None)), ensure_ascii=False)
            # No storage error is an ordinary rejected key. Publish both facts
            # only after the single SQL transaction has durably succeeded.
            self.store.append_validated(candidate)
            self._accept_committed(candidate, [peek, event])
            return dict(message=message, round_changed=False, seat=seat, hand_id=hand_id)

    def deal_hidden(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, hidden=True, hand_id=hand_id, track_id=track_id)

    def deal_unknown(self, seat, hand_id=None, track_id=None):
        return self._apply("deal", seat, None, unknown=True, hand_id=hand_id,
                           track_id=track_id, confirm_status=CANDIDATE)

    def reveal(self, target_event_id, rank, suit=None, *, auto_next=None):
        from .recording_commands import RecordingInputRejected
        with self.read_frame():
            current = self.state().current
            info = current.unresolved.get(target_event_id) if current else None
            if not info or info['round_id'] != current.round_id:
                raise RecordingInputRejected('原暗牌不在当前轮待揭示记录中；请核对录牌位置')
            # Ordinary entry mistakes (notably A+T after a saved non-BJ check)
            # are known before any persistent command starts. Validate only a
            # private copy of the current table, preserving every recorded fact.
            preview = copy.deepcopy(current.table)
            try:
                preview.reveal_card(info['seat'], info['hand_id'], info.get('track_id'),
                                    rank, suit, event_id=target_event_id)
            except (TableError, ValueError) as error:
                raise RecordingInputRejected(str(error)) from error
            # Independent full candidate replay and the SQL transaction remain
            # authoritative. Never turn an exception from either into a key refusal.
            if auto_next is not None and info['seat'] == DEALER:
                return self._dealer_input('reveal', target_event_id, rank, suit=suit, auto_next=auto_next)
            return self._apply("reveal", target_event_id, rank, suit=suit)

    def _dealer_input(self, method, *args, auto_next, **kwargs):
        """One input, one durable transaction, including any automatic round transition."""
        from .automatic_flow import dealer_finish_message
        plan = self.entry_plan
        before = self.state().current
        trusted = bool(plan and before and plan.mode in (MODE_DEALER, MODE_PEEK_WAIT)
                       and not plan.paused and not plan.input_paused and not plan.unresolved_slots
                       and plan.initial_complete()
                       and (plan.session_id, plan.shoe_id, plan.round_id) ==
                           (self.session_id, before.shoe_id, before.round_id)
                       and plan.ledger_digest == self.read_prefix().prefix_digest)
        candidate = self._command_candidate()
        event = getattr(candidate, method)(*args, **kwargs)
        event.source = self.recording_source
        current = candidate.replay().current
        table = current.table
        natural = bool(table.dealer.hands and is_natural_blackjack(table.dealer.hands[0].ranks))
        if (not trusted or not dealer_finish_message(table)
                or not (next_open_hand(table, plan.participating_seats) is None or natural)
                or self.round_completion_problem(current)):
            self.store.append_validated(candidate)
            self._accept_committed(candidate, [event])
            return event
        ordered, evidence, freeze = self._round_options(**auto_next)
        ended = candidate.end_round(settle=True, observation_status='complete')
        started = candidate.start_round(list(ordered))
        event, ended, started = [candidate._find(item.event_id) for item in (event, ended, started)]
        event.source = ended.source = started.source = self.recording_source
        receipt = json.loads(evidence or '{}')
        receipt['automatic_next_v1'] = dict(trigger=event.event_id, ended=ended.event_id,
                                            previous_plan=plan.to_dict())
        started.evidence = json.dumps(receipt, ensure_ascii=False)
        self.store.append_validated(candidate)
        self._accept_committed(candidate, [event, ended, started],
                               lambda item: freeze(item) if item.etype == ROUND_STARTED else None)
        return event

    def player_action(self, seat, hand_id, action, extra=None):
        return self._apply("player_action", seat, hand_id, action, extra)

    def peek_negative(self):
        return self._apply("peek_negative")

    def burn(self, count, note=""):
        return self._apply("burn", count, note)

    def mark_gap(self, reason):
        return self._apply("gap", reason)

    def undo_last(self, reason=""):
        group = self._shoe_undo_group()
        default_reason = '撤回换靴及自动结束的旧轮' if group else '撤回最后录牌及其自动结算、开轮'
        group = group or self._automatic_undo_group()
        if not group:
            group = self._bclc_ace_undo_group()
            if group:
                default_reason = '撤回本次玩家输入及实际非BJ确认'
        if not group:
            group = self._automatic_hole_undo_group()
            if group:
                default_reason = '撤回本次录牌及其自动暗牌'
        if group:
            events, plan = group
            # Undo changes the recorded round, not the user's explicit input pause.
            if plan and self.entry_plan and self.entry_plan.input_paused:
                plan.input_paused = True
                plan.input_pause_reason = self.entry_plan.input_pause_reason
            candidate = self._command_candidate()
            undos = [candidate.undo_last(reason or default_reason) for _ in events]
            undos = [candidate._find(event.event_id) for event in undos]
            for event in undos:
                event.source = self.recording_source
            self.store.append_validated(candidate)
            self.entry_plan = plan
            self.entry_warning = ''
            def restore(_event):
                current = self.state().current
                if plan is None and current and current.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
                    self._pause_entry_recovery('已撤回换靴；旧轮录入位置需核对，不能猜测下一张归属')
            self._accept_committed(candidate, undos, restore)
            return undos[-1]
        return self._apply("undo_last", reason)

    def _bclc_ace_undo_group(self):
        """Verify a complete observed non-BJ/player input before grouped undo."""
        voided = self.ledger._voided_ids()
        active = [event for event in self.ledger.events if event.etype != UNDO and event.event_id not in voided]
        if len(active) < 2:
            return None
        peek, event = active[-2:]
        if (peek.etype != PEEK_NEGATIVE or event.etype not in (CARD_DEALT, PLAYER_ACTION)
                or event.payload.get('seat') == DEALER or peek.seq + 1 != event.seq
                or (peek.session_id, peek.shoe_id, peek.round_id) !=
                   (event.session_id, event.shoe_id, event.round_id)):
            return None
        try:
            receipt = json.loads(event.evidence or '{}')['bclc_ace_input_v1']
            previous = RoundEntryPlan.from_dict(receipt['previous_plan'])
            prefix = [item.to_dict() for item in self.ledger.events if item.seq < peek.seq]
            if (not prefix or receipt['peek_event_id'] != peek.event_id
                    or json.loads(peek.evidence or '{}').get('recording_contract') != 'bclc-ace-first-player-v1'
                    or peek.source != event.source):
                return None
            before = self.ledger.replay(through_seq=prefix[-1]['seq']).current
            if not self._bclc_ace_bound_hole(previous, before, prefix[-1]['seq'], digest(prefix)):
                return None
            plan, current = self.entry_plan, self.state().current
            if (not plan or not current
                    or (plan.session_id, plan.shoe_id, plan.round_id) !=
                       (self.session_id, event.shoe_id, event.round_id)
                    or (current.shoe_id, current.round_id) != (event.shoe_id, event.round_id)
                    or plan.ledger_seq != self.ledger.events[-1].seq
                    or plan.ledger_digest != self.read_prefix().prefix_digest
                    or plan.slots != previous.slots or plan.filled_slots != previous.filled_slots
                    or current.table.dealer_hole_checked_negative is not True):
                return None
            # Find the same first effective player after the observed check,
            # before the operation changes the hand or its legal actions.
            before.table.mark_peek_negative()
            acting = next_open_hand(before.table, previous.participating_seats)
            if not acting or (event.payload.get('seat'), event.payload.get('hand_id')) != acting[:2]:
                return None
            if event.etype == CARD_DEALT:
                if (receipt['kind'] not in ('rank', 'card') or event.payload.get('face_state') != 'shown'
                        or receipt['rank'] != event.payload.get('rank') or is_ten_value(receipt['rank'])
                        or receipt['rank'] not in ('A', '2', '3', '4', '5', '6', '7', '8', '9')
                        or receipt['action'] is not None):
                    return None
                expected_ids = set(previous.observed_card_ids) | {event.event_id}
            else:
                if (receipt['kind'] != 'action' or receipt['rank'] is not None
                        or receipt['action'] != event.payload.get('action')):
                    return None
                expected_ids = set(previous.observed_card_ids)
            if (set(plan.observed_card_ids) != expected_ids or set(self.live_card_event_ids()) != expected_ids):
                return None
            return (event, peek), previous
        except (ValueError, KeyError, TypeError, AttributeError, TableError, LedgerError):
            return None

    def _automatic_hole_undo_group(self):
        """Undo one verified visible input together with its automatic hole.

        Independent/manual hidden cards remain individual events. Old automatic
        receipts can restore only the fully bound initial slots; a missing or
        ambiguous plan never supplies an inferred next seat.
        """
        voided = self.ledger._voided_ids()
        active = [e for e in self.ledger.events if e.etype != UNDO and e.event_id not in voided]
        if len(active) < 2:
            return None
        trigger, hidden = active[-2:]
        if (trigger.etype != CARD_DEALT or trigger.payload.get('face_state') != 'shown'
                or trigger.payload.get('seat') == DEALER or hidden.etype != CARD_DEALT
                or hidden.payload.get('face_state') != FACE_HIDDEN or hidden.payload.get('seat') != DEALER
                or trigger.seq + 1 != hidden.seq
                or (trigger.session_id, trigger.shoe_id, trigger.round_id) !=
                   (hidden.session_id, hidden.shoe_id, hidden.round_id)):
            return None
        try:
            receipt = json.loads(hidden.evidence or '{}')
            plan, seg = self.entry_plan, self.state().current
            if (receipt.get('recording_contract') != SIMPLE_HOLE_CONTRACT
                    or receipt.get('trigger_event_id') != trigger.event_id
                    or not plan or not seg or not plan.simple_hole or not self._simple_rules_confirmed(seg)
                    or plan.paused or plan.unresolved_slots or not plan.initial_complete()
                    or plan.mode not in (MODE_CONTINUATION, MODE_PEEK_WAIT, MODE_DEALER)
                    or (plan.session_id, plan.shoe_id, plan.round_id) !=
                       (self.session_id, hidden.shoe_id, hidden.round_id)
                    or (seg.shoe_id, seg.round_id) != (hidden.shoe_id, hidden.round_id)
                    or plan.ledger_seq != self.ledger.events[-1].seq
                    or plan.ledger_digest != self.read_prefix().prefix_digest):
                return None
            frozen = RoundEntryPlan.freeze(session_id=plan.session_id, shoe_id=plan.shoe_id,
                round_id=plan.round_id, selected_seats=plan.participating_seats,
                my_seat=plan.my_seat, deal_direction=plan.deal_direction, simple_hole=True)
            if plan.slots != frozen.slots or len(plan.slots) < 4:
                return None
            shown_slot, hole_slot = plan.slots[-2:]
            live_ids = set(self.live_card_event_ids())
            if (shown_slot.ordinal != 2 or shown_slot.seat != trigger.payload['seat']
                    or plan.filled_slots.get(shown_slot.slot_id) != trigger.event_id
                    or plan.filled_slots.get(hole_slot.slot_id) != hidden.event_id
                    or set(plan.filled_slots.values()) != live_ids
                    or set(plan.observed_card_ids) != live_ids):
                return None
            slot_events = [self.ledger._find(plan.filled_slots[slot.slot_id]) for slot in plan.slots]
            if (slot_events != sorted(slot_events, key=lambda item: item.seq)
                    or any(event.etype != CARD_DEALT or event.round_id != hidden.round_id
                           or event.payload.get('seat') != slot.seat
                           or event.payload.get('face_state') != slot.expected_face
                           for slot, event in zip(plan.slots, slot_events))):
                return None
            prefix = [e.to_dict() for e in self.ledger.events if e.seq < trigger.seq]
            if not prefix:
                return None
            prefix_digest = digest(prefix)
            saved = receipt.get('automatic_hole_undo_v1')
            if saved is not None:
                previous = RoundEntryPlan.from_dict(saved['previous_plan'])
            else:
                # Legacy receipts have an explicit trigger but no plan snapshot.
                # Rebuild only their verified complete initial-slot prefix.
                previous = frozen
                for slot, event in zip(plan.slots[:-2], slot_events[:-2]):
                    previous.mark_filled(slot.slot_id, event.event_id, event.payload.get('rank'), slot.expected_face)
                previous.cursor_slot_id = shown_slot.slot_id
                previous.dealer_up_rank = plan.dealer_up_rank
                if previous.last_saved:
                    current_cards = [card for seat in [seg.table.dealer, *seg.table.players.values()]
                                     for hand in seat.hands for card in hand.cards]
                    card = next(card for card in current_cards if card.event_id == previous.last_saved.event_id)
                    previous.last_saved.rank = card.rank
                previous.ledger_seq, previous.ledger_digest = prefix[-1]['seq'], prefix_digest
            expected_filled = {sid: eid for sid, eid in plan.filled_slots.items()
                               if eid not in (trigger.event_id, hidden.event_id)}
            if (previous.mode != MODE_INITIAL or previous.paused or previous.input_paused
                    or not previous.simple_hole or previous.unresolved_slots
                    or (previous.session_id, previous.shoe_id, previous.round_id) !=
                       (plan.session_id, plan.shoe_id, plan.round_id)
                    or previous.slots != plan.slots or previous.participating_seats != plan.participating_seats
                    or (previous.my_seat, previous.deal_direction) != (plan.my_seat, plan.deal_direction)
                    or previous.filled_slots != expected_filled
                    or set(previous.observed_card_ids) != set(expected_filled.values())
                    or previous.unfilled_ids() != (shown_slot.slot_id, hole_slot.slot_id)
                    or previous.cursor_slot_id != shown_slot.slot_id
                    or previous.ledger_seq != prefix[-1]['seq'] or previous.ledger_digest != prefix_digest):
                return None
            return (hidden, trigger), previous
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration, TableError, LedgerError):
            return None

    def _shoe_undo_group(self):
        voided = self.ledger._voided_ids()
        active = [e for e in self.ledger.events if e.etype != UNDO and e.event_id not in voided]
        if not active or active[-1].etype != SHOE_CREATED:
            return None
        try:
            receipt = json.loads(active[-1].evidence or '{}')['manual_shoe_change_v1']
            ids = receipt['event_ids']
            if not isinstance(ids, list) or len(ids) not in (2, 3):
                return None
            events = active[-len(ids):]
            expected = [ROUND_ENDED, SHOE_ENDED, SHOE_CREATED] if len(ids) == 3 else [SHOE_ENDED, SHOE_CREATED]
            prefix = [e.to_dict() for e in self.ledger.events if e.seq < events[0].seq]
            if ([e.event_id for e in events] != ids or [e.etype for e in events] != expected
                    or any(a.seq + 1 != b.seq for a, b in zip(events, events[1:]))
                    or not prefix or receipt['prefix_seq'] != prefix[-1]['seq']
                    or receipt['prefix_digest'] != digest(prefix)
                    or len(events) == 3 and (events[0].payload.get('settle') is not False
                        or events[0].payload.get('observation_status') != 'unknown'
                        or events[0].shoe_id != events[1].shoe_id)):
                return None
            saved = receipt['previous_plan']
            plan = RoundEntryPlan.from_dict(saved) if saved is not None else None
            if plan and (plan.session_id != self.session_id or plan.shoe_id != events[-2].shoe_id
                         or plan.ledger_seq != prefix[-1]['seq'] or plan.ledger_digest != digest(prefix)):
                return None
            return tuple(reversed(events)), plan
        except (ValueError, KeyError, TypeError, AttributeError, IndexError):
            return None

    def _automatic_undo_group(self):
        voided = self.ledger._voided_ids()
        active = [e for e in self.ledger.events if e.etype != UNDO and e.event_id not in voided]
        if len(active) < 3 or active[-1].etype != ROUND_STARTED:
            return None
        trigger, ended, started = active[-3:]
        try:
            receipt = json.loads(started.evidence or '{}')['automatic_next_v1']
            plan = RoundEntryPlan.from_dict(receipt['previous_plan'])
            prefix = [e.to_dict() for e in self.ledger.events if e.seq < trigger.seq]
            if (trigger.etype not in (CARD_DEALT, CARD_REVEALED) or trigger.payload.get('seat') != DEALER
                    or ended.etype != ROUND_ENDED or not ended.payload.get('settle')
                    or ended.payload.get('observation_status') != 'complete'
                    or receipt['trigger'] != trigger.event_id or receipt['ended'] != ended.event_id
                    or (trigger.seq + 1, ended.seq + 1) != (ended.seq, started.seq)
                    or (plan.session_id, plan.shoe_id, plan.round_id) !=
                        (self.session_id, trigger.shoe_id, trigger.round_id)
                    or ended.round_id != trigger.round_id or started.shoe_id != trigger.shoe_id
                    or not prefix or plan.ledger_seq != prefix[-1]['seq'] or plan.ledger_digest != digest(prefix)
                    or plan.mode not in (MODE_DEALER, MODE_PEEK_WAIT) or plan.paused or plan.input_paused):
                return None
            return (started, ended, trigger), plan
        except (ValueError, KeyError, TypeError, AttributeError):
            return None

    def correct(self, event_id, payload_fix, reason):
        if not reason.strip():
            raise ValueError("纠错必须填写依据或原因")
        return self._apply("correct", event_id, payload_fix, reason)

    def confirm_complete_unburned_shoe(self, reason, expected_context):
        """Append the user's missing shoe observations without shifting cards."""
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('请记录完整新靴与零烧牌的实际确认依据')
        with self.read_frame():
            if self.context_token != expected_context:
                raise LedgerError('确认期间记录已变化，请核对当前牌靴')
            seg, plan, prefix = self.state().current, self.entry_plan, self.read_prefix()
            if seg is None or seg.closed or seg.rules.n_decks != 8:
                raise LedgerError('仅能确认当前有效的8副牌靴')
            event = next(e for e in self.ledger.events
                         if e.etype == SHOE_CREATED and e.shoe_id == seg.shoe_id)
            rules = copy.deepcopy(seg.rules)
            rules.start_from_new_shoe = True
            rules.burn_cards_known = True
            rules.initial_burn_count = 0
            trusted = bool(plan and not plan.paused and not plan.input_paused
                           and not plan.unresolved_slots and plan.mode not in (MODE_UNALIGNED, MODE_MANUAL)
                           and (plan.session_id, plan.shoe_id, plan.round_id) ==
                               (self.session_id, seg.shoe_id, seg.round_id)
                           and (plan.ledger_seq, plan.ledger_digest) ==
                               (prefix.through_seq, prefix.prefix_digest)
                           and set(plan.observed_card_ids) == set(self.live_card_event_ids()))
            saved_plan = copy.deepcopy(plan) if trusted else None
            def updated(correction):
                if saved_plan is None:
                    self._sync_entry_event(correction)
                else:
                    self.entry_plan = saved_plan
            return self._apply('correct', event.event_id,
                               {'rules_snapshot': rules.to_json()}, reason,
                               _entry_update=updated)

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

    def current_decision_input(self, seat, hand_id=None):
        """Recording UI only; historical prefix analysis remains independently available."""
        from ..analysis.contracts import InputUnavailable, INAPPLICABLE
        plan = self.entry_plan
        if plan and plan.mode == MODE_INITIAL and not plan.initial_complete():
            raise InputUnavailable('INITIAL_DEAL', '初始牌未录齐，暂停EV计算', INAPPLICABLE)
        from ..analysis.information import build_prepared_input
        snapshot=self.read_prefix()
        key = snapshot.session_id, snapshot.prefix_digest
        if getattr(self, '_analysis_prefix_key', None) != key:
            with self.read_frame():
                try:
                    current = self.state().current
                except (ValueError, LedgerError) as error:
                    raise InputUnavailable('INVALID_EVENTS', f'事件校验失败，不能用于分析：{error}') from error
                validated = self.read_prefix()
                # A view must not replace the identity after replay. Comparing
                # full content also detects in-place changes without a new seq.
                from .read_snapshot import PrefixSnapshot
                actual = PrefixSnapshot.capture(self.ledger)
                if snapshot.content != validated.content or snapshot.content != actual.content:
                    raise LedgerError('分析前缀在已验证视图中发生变化，请刷新核对')
                prepared = (snapshot.through_seq, snapshot, current)
            self._analysis_prefix_key, self._analysis_prepared = key, prepared
            self._analysis_inputs = {}
        current = self._analysis_prepared[2]
        hands = current.table.players[seat].hands if current and seat in current.table.players else []
        total_21 = len(hands) == 1 and not hands[0].from_split and hands[0].total()[0] == 21
        target = seat, hand_id
        if target not in self._analysis_inputs:
            try:
                self._analysis_inputs[target] = build_prepared_input(
                    self.session_id, seat, hand_id, *self._analysis_prepared, other_players_stand=True)
            except InputUnavailable:
                if total_21:
                    raise InputUnavailable('TOTAL_21', '已达21点，已自动跳过玩家行动', INAPPLICABLE) from None
                raise
        if total_21:
            from ..analysis.ace_prepeek_contracts import AcePrepeekInput
            if not isinstance(self._analysis_inputs[target], AcePrepeekInput):
                raise InputUnavailable('TOTAL_21', '已达21点，已自动跳过玩家行动', INAPPLICABLE)
        return self._analysis_inputs[target]

    def recompute_input(self, saved):
        original = saved["result"]["input"]
        ledger = self.store.load_ledger(original["session_id"], original["through_seq"])
        if not self.analysis_store.matches_prefix(saved, ledger):
            raise LedgerError("原分析关联的事件前缀摘要不匹配，拒绝复算")
        from ..analysis.seat_scenario import MODEL
        conditional = json.loads(original['information_json']).get('seat_scenario', {}).get('model') == MODEL
        rebuilt = build_input(ledger, original["seat"], original["hand_id"], original["through_seq"],
                              other_players_stand=conditional)
        from ..analysis.ace_prepeek_contracts import ACE_PREPEEK_INPUT_SCHEMA
        if original.get('schema') == ACE_PREPEEK_INPUT_SCHEMA and rebuilt.schema != ACE_PREPEEK_INPUT_SCHEMA:
            raise LedgerError('原检查前评估不能按检查后的信息身份复算')
        return rebuilt

    def export_diagnostic(self, session_id, path):
        data = self.store.diagnose_session(session_id)
        return atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8"))

    def state(self):
        if getattr(self, '_read_depth', 0):
            key = id(self.ledger), self.context_token
            if getattr(self, '_read_key', None) != key:
                self._read_snapshot = self.ledger.replay()
                self._read_key = key
                # A prefix-first reader may already own these exact full bytes
                # in this frame. Do not serialize them again after replay.
                self.read_prefix()
            return self._read_snapshot
        return self.ledger.replay()

    def read_prefix(self):
        from .read_snapshot import PrefixSnapshot
        if not getattr(self,'_read_depth',0):return PrefixSnapshot.capture(self.ledger)
        key=id(self.ledger),self.context_token
        if getattr(self,'_read_prefix_key',None)!=key:
            self._read_prefix=PrefixSnapshot.capture(self.ledger)
            self._read_prefix_key=key
        return self._read_prefix

    @contextmanager
    def read_frame(self):
        """Share read-only replay within one UI callback; commits invalidate by identity.

        No snapshot survives a callback, and command validation still uses the
        ledger's independent replay. Callers must not mutate this view state.
        """
        self._read_depth = getattr(self, '_read_depth', 0) + 1
        try:
            yield
        finally:
            self._read_depth -= 1
            if not self._read_depth:
                self._read_key = self._read_snapshot = None
                self._read_prefix_key = self._read_prefix = None

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
        round_id = self._current_round_id()
        return [ev.event_id for ev in self.ledger.events
                if ev.etype == CARD_DEALT and ev.event_id not in voided
                and ev.round_id == round_id]

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
                    if (seg.table.split_hand_closed(hand)
                            or hand.from_split and seg.rules.split_deal_order == 'both_second_cards_first'):
                        self._advance_entry_hand()
        elif event.etype == PLAYER_ACTION:
            if (payload["action"] == ACTION_SPLIT and plan.slots) or plan.mode == MODE_CONTINUATION:
                self._advance_entry_hand()
        elif event.etype == PEEK_NEGATIVE and plan.mode == MODE_PEEK_WAIT:
            self._advance_entry_hand()
        elif event.etype == CARD_REVEALED and payload['seat'] == DEALER:
            if plan.mode == MODE_PEEK_WAIT:
                dealer = seg.table.dealer.hands[0]
                if is_natural_blackjack(dealer.ranks):
                    plan.enter_dealer_phase()
                else:
                    self._advance_entry_hand()
        elif event.etype == CARD_REVEALED and plan.mode == MODE_CONTINUATION:
            self._advance_entry_hand()
        elif event.etype == UNDO:
            target = payload.get("target_event_id")
            if self.ledger._find(target).etype == CORRECTION and plan.mode != MODE_UNALIGNED:
                dealer = seg.table.dealer.hands
                plan.dealer_up_rank = dealer[0].cards[0].rank if dealer and dealer[0].cards else None
                if plan.last_saved:
                    for seat in [seg.table.dealer, *seg.table.players.values()]:
                        for hand in seat.hands:
                            for card in hand.cards:
                                if card.event_id == plan.last_saved.event_id:
                                    plan.last_saved.rank = card.rank
                if plan.initial_complete() and plan.mode == MODE_PEEK_WAIT and (
                        not dealer_up_requires_peek(seg.rules, plan.dealer_up_rank)
                        or seg.table.dealer_hole_checked_negative or seg.table._dealer_revealed()):
                    self._advance_entry_hand()
            reopened = plan.undo_event(target)
            plan.observed_card_ids = self.live_card_event_ids()
            plan.reconcile(plan.observed_card_ids)
            if reopened is None and plan.mode == MODE_CONTINUATION:
                self._advance_entry_hand()
            elif reopened is None and plan.mode == "dealer_phase":
                self._advance_entry_hand()
            from ..analysis.split_contracts import BCLC_PROFILE
            restores_bclc_peek = seg.rules.profile_id == BCLC_PROFILE and seg.rules.version == 2
            if ((plan.simple_hole or restores_bclc_peek) and plan.initial_complete()
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
        plan.ledger_digest = self.read_prefix().prefix_digest
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
                    or plan.ledger_digest != self.read_prefix().prefix_digest):
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
