"""Adjust the first deal pass using the existing append-only undo/replay contract."""
import copy
import json
import uuid
from dataclasses import dataclass

from ..analysis.contracts import digest
from ..core.table import DEALER, PHASE_DEALING, TableError
from ..ledger.events import CARD_DEALT, CORRECTION, FACE_SHOWN, ROUND_STARTED, UNDO
from .deal_entry import (MODE_INITIAL, MODE_CONTINUATION, RoundEntryPlan,
                         SIMPLE_HOLE_CONTRACT, build_initial_slots,
                         dealer_up_requires_peek, next_open_hand, participating_in_order)


def first_pass_open(ctrl, segment):
    plan = ctrl.entry_plan
    return bool(segment and not segment.closed and segment.table.phase == PHASE_DEALING
                and plan and plan.mode == MODE_INITIAL and not plan.paused
                and not plan.unresolved_slots and plan.participating_seats
                and set(plan.filled_slots) <= {s.slot_id for s in plan.slots[:len(plan.participating_seats) + 1]})


@dataclass(frozen=True)
class PlayerCountPreview:
    context: tuple
    plan_digest: str
    participants: tuple
    my_seat: str
    cards: tuple
    moved: tuple
    add_hole: bool


def preview_players(ctrl, participants):
    segment = ctrl.state().current
    plan = ctrl.entry_plan
    if not first_pass_open(ctrl, segment):
        raise TableError('本轮已超过第一遍发牌；人数设置将用于下一轮。')
    if (plan.ledger_seq != ctrl.ledger.events[-1].seq
            or plan.ledger_digest != digest(ctrl.ledger.to_list())):
        raise TableError('发牌计划与已存记录不同步，请先核对录牌位置。')
    ordered = participating_in_order(participants, plan.deal_direction)
    if not ordered or any(s not in segment.table.players for s in ordered):
        raise TableError('本轮需选择1至7位有效玩家。')
    if ordered == plan.participating_seats:
        raise TableError('本轮人数没有变化。')
    voided = ctrl.ledger._voided_ids()
    active = [e for e in ctrl.ledger.events if e.event_id not in voided and e.etype != UNDO]
    start = next(e for e in reversed(active) if e.etype == ROUND_STARTED)
    current = [e for e in active if e.seq >= start.seq]
    deals = [e for e in current if e.etype == CARD_DEALT]
    allowed_ids = {e.event_id for e in deals}
    if (start.round_id != segment.round_id
            or any(e.etype not in (ROUND_STARTED, CARD_DEALT, CORRECTION) for e in current)
            or any(e.etype == CORRECTION and e.payload['target_event_id'] not in allowed_ids for e in current)
            or allowed_ids != set(plan.filled_slots.values())
            or allowed_ids != set(plan.observed_card_ids)):
        raise TableError('本轮已有其他操作或未对齐记录，不能按第一遍顺序自动调整。')
    visible = {c.event_id: c for seat in [segment.table.dealer, *segment.table.players.values()]
               for hand in seat.hands for c in hand.cards}
    old_slots = plan.slots[:len(deals)]
    if any(plan.filled_slots.get(s.slot_id) != e.event_id or e.payload['face_state'] != FACE_SHOWN
           or e.payload['seat'] != s.seat
           for s, e in zip(old_slots, deals)):
        raise TableError('已录牌没有按连续初始顺序排列，请先核对。')
    slots = build_initial_slots(ordered)
    if len(deals) > len(slots) - 1:
        raise TableError('已录牌超过调整后的初始牌数，请先核对多出的牌。')
    cards = tuple((e.event_id, visible[e.event_id].rank, visible[e.event_id].suit, s.seat)
                  for e, s in zip(deals, slots))
    moved = tuple((i + 1, rank, ctrl.ledger._find(eid).payload['seat'], seat)
                  for i, (eid, rank, _suit, seat) in enumerate(cards)
                  if ctrl.ledger._find(eid).payload['seat'] != seat)
    return PlayerCountPreview(ctrl.context_token, digest(plan.to_dict()), ordered,
                              plan.my_seat if plan.my_seat in ordered else ordered[0],
                              cards, moved, plan.simple_hole and len(cards) == len(slots) - 1)


def apply_players(ctrl, preview):
    if (ctrl.context_token != preview.context or ctrl.entry_plan is None
            or digest(ctrl.entry_plan.to_dict()) != preview.plan_digest):
        raise TableError('记录或录牌位置已变化，请重新调整人数。')
    fresh = preview_players(ctrl, preview.participants)
    if fresh != preview:
        raise TableError('人数调整预览已变化，请重新核对。')
    old_plan = ctrl.entry_plan
    candidate = copy.deepcopy(ctrl.ledger)
    voided = candidate._voided_ids()
    start = next(e for e in reversed(candidate.events)
                 if e.etype == ROUND_STARTED and e.event_id not in voided)
    commands = [e for e in candidate.events if e.seq >= start.seq
                and e.etype != UNDO and e.event_id not in voided]
    revision = uuid.uuid4().hex
    reason = f'第一遍调整人数 {len(old_plan.participating_seats)}→{len(preview.participants)}；修订 {revision}'
    for old in reversed(commands):
        undo = candidate.undo_last(reason)
        if undo.payload['target_event_id'] != old.event_id:
            raise TableError('人数调整的撤销顺序不一致，请重新核对。')
        undo.source = ctrl.recording_source
    new_start = candidate.start_round(list(preview.participants))
    new_start.source = ctrl.recording_source
    evidence = json.loads(start.evidence or '{}')
    evidence['participant_revision'] = dict(id=revision, replaces=start.event_id, reason=reason)
    new_start.evidence = json.dumps(evidence, ensure_ascii=False)
    plan = RoundEntryPlan.freeze(session_id=ctrl.session_id, shoe_id=new_start.shoe_id,
        round_id=new_start.round_id, selected_seats=preview.participants,
        my_seat=preview.my_seat, deal_direction=old_plan.deal_direction, simple_hole=old_plan.simple_hole)
    for eid, rank, suit, seat in preview.cards:
        old = ctrl.ledger._find(eid)
        event = candidate.deal(seat, rank, suit=suit, track_id=old.payload.get('track_id'),
            source=old.source, evidence=json.dumps(dict(participant_revision=revision,
                replaces=eid, original_evidence=old.evidence), ensure_ascii=False))
        event.observed_at = old.observed_at
        plan.mark_filled(plan.slot().slot_id, event.event_id, rank)
        plan.advance_after_initial_success()
    if preview.add_hole:
        event = candidate.deal(DEALER, hidden=True, source=ctrl.recording_source,
            evidence=json.dumps(dict(recording_contract=SIMPLE_HOLE_CONTRACT,
                basis='调整人数并确认初始可见牌已录齐，登记未知底牌',
                trigger_event_id=plan.last_saved.event_id), ensure_ascii=False))
        plan.mark_filled(plan.hole_slot().slot_id, event.event_id, kind='hidden')
        plan.advance_after_initial_success(lambda rank: dealer_up_requires_peek(candidate.replay().current.rules, rank))
    if plan.mode == MODE_CONTINUATION:
        target = next_open_hand(candidate.replay().current.table, plan.participating_seats)
        plan.enter_continuation(*target) if target else plan.enter_dealer_phase()
    plan.input_paused, plan.input_pause_reason = old_plan.input_paused, old_plan.input_pause_reason
    added = candidate.events[len(ctrl.ledger.events):]
    ctrl.store.save_ledger(candidate)  # All undo + replacement records share one existing transaction.
    ctrl.entry_plan, ctrl.entry_warning = plan, ''
    ctrl._accept_committed(candidate, added, lambda _event: None)
    return added
