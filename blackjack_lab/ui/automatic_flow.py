"""Visible terminal states derived from the recorded cards and locked rules."""
from ..core.table import DEALER
from ..ledger.events import CARD_DEALT, CORRECTION
from .deal_entry import MODE_CONTINUATION, MODE_DEALER, next_open_hand


def dealer_finish_message(table):
    if not table.dealer.hands or len(table.dealer.hands[0].cards) < 2:
        return ''
    total, soft = table.dealer.hands[0].total()
    if total is None:
        return ''  # A visible total must never stand in for the unrevealed hole.
    if total > 21:
        return f'庄家 {total} 点，已爆牌'
    if total >= 18 or total == 17 and (not soft or table.rules.dealer_soft17 == 'S17'):
        return f'庄家 {"软" if soft else ""}{total} 点，已自动停牌'
    return ''


def bust_transition_message(ctrl, segment):
    plan = ctrl.entry_plan
    if (not plan or plan.mode not in (MODE_CONTINUATION, MODE_DEALER)
            or plan.paused or plan.input_paused or not plan.last_saved
            or plan.last_saved.kind != 'shown' or plan.last_saved.seat == DEALER):
        return ''
    saved = plan.last_saved
    event = ctrl.ledger.events[-1]
    if not ((event.etype == CARD_DEALT and event.event_id == saved.event_id)
            or (event.etype == CORRECTION and event.payload['target_event_id'] == saved.event_id)):
        return ''
    for index, hand in enumerate(segment.table.seat(saved.seat).hands, 1):
        if hand.is_bust and any(c.event_id == saved.event_id for c in hand.cards):
            target = next_open_hand(segment.table, plan.participating_seats)
            following = f'{target[0]}／第{target[2]}手' if target else DEALER
            return f'{saved.seat}／第{index}手 {hand.total()[0]}点，已爆牌；已自动转到{following}。'
    return ''
