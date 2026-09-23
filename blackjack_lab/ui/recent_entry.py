"""Select user-visible input and name the existing per-event undo target."""
import json
from dataclasses import dataclass

from ..ledger.events import CARD_DEALT, CARD_REVEALED, UNDO, SESSION_STARTED


@dataclass(frozen=True)
class RecentCard:
    event_id: str
    physical_id: str
    seat: str
    hand: int
    ordinal: int
    rank: str
    revealed: bool

    @property
    def label(self):
        role = '底牌揭示' if self.revealed else f'第{self.ordinal}张'
        return f'最近录入：{self.seat} · 第{self.hand}手 · {role} · {self.rank}'


def recent_visible(ctrl, seg=None):
    seg = seg or ctrl.state().current
    if seg is None or seg.closed:
        return None
    voided = ctrl.ledger._voided_ids()
    # The automatic hole is not user-visible input, even when it is last in
    # the transaction. Its trigger_event_id points to the preceding shown card.
    for event in reversed(ctrl.ledger.events):
        if event.event_id in voided or event.round_id != seg.round_id:
            continue
        if event.etype == CARD_REVEALED:
            physical = event.payload['target_event_id']
        elif event.etype == CARD_DEALT and event.payload.get('face_state') == 'shown':
            physical = event.event_id
        else:
            continue
        seat = event.payload['seat']
        for h, hand in enumerate(seg.table.seat(seat).hands, 1):
            for n, card in enumerate(hand.cards, 1):
                if card.event_id == physical and not card.is_unknown:
                    return RecentCard(event.event_id, physical, seat, h, n, card.rank, event.etype == CARD_REVEALED)
    return None


def undo_label(ctrl):
    voided = ctrl.ledger._voided_ids()
    event = next((e for e in reversed(ctrl.ledger.events)
                  if e.etype not in (UNDO, SESSION_STARTED) and e.event_id not in voided), None)
    if event is None:
        return '无可撤销记录'
    if event.etype == 'ROUND_STARTED' and ctrl._automatic_undo_group():
        return '撤销录牌及自动下一局'
    if event.etype == CARD_REVEALED:
        return '撤销底牌揭示' if ctrl.ledger._find(event.payload['target_event_id']).payload.get('face_state') == 'hidden' else '撤销未知牌揭示'
    if event.etype == CARD_DEALT:
        if event.payload.get('face_state') == 'hidden':
            try:
                receipt = json.loads(event.evidence or '{}')
            except (ValueError, TypeError):
                receipt = {}
            return '撤销自动暗牌' if isinstance(receipt, dict) and receipt.get('trigger_event_id') else '撤销暗牌登记'
        return f"撤销{event.payload['seat']}发牌"
    names = {'CORRECTION': '本次纠错', 'ROUND_STARTED': '本轮开始', 'ROUND_ENDED': '本轮结算／结束',
             'SHOE_CREATED': '牌靴创建', 'SHOE_ENDED': '牌靴结束', 'PEEK_NEGATIVE': '非BJ检查',
             'PLAYER_ACTION': f"{event.payload.get('seat', '')}{event.payload.get('action', '')}",
             'BURN_CARDS': '烧牌登记', 'OBSERVATION_GAP': '缺口标记'}
    return '撤销' + names.get(event.etype, event.etype)
