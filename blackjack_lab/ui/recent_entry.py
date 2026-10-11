"""Select user-visible input and name the validated user-operation undo target."""
import json
from dataclasses import dataclass

from ..ledger.events import CARD_DEALT, CARD_REVEALED, UNDO, SESSION_STARTED
from ..analysis.contracts import digest


@dataclass(frozen=True)
class RecentCard:
    event_id: str
    physical_id: str
    seat: str
    hand: int
    ordinal: int
    rank: str
    revealed: bool
    suit: str | None = None

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
                    return RecentCard(event.event_id, physical, seat, h, n, card.rank, event.etype == CARD_REVEALED, card.suit)
    return None


def undo_label(ctrl):
    # Painting already authenticates the entire prefix in its read-only frame.
    # Reuse only presentation text, never an executable group or restored plan.
    # Outside that frame even a matching old label must be freshly validated.
    cache_key = None
    key = id(ctrl.ledger), ctrl.context_token
    if getattr(ctrl, '_read_depth', 0) and getattr(ctrl, '_read_key', None) == key:
        prefix = ctrl.read_prefix()
        try:
            plan_digest = digest(ctrl.entry_plan.to_dict() if ctrl.entry_plan else None)
            cache_key = ctrl.session_id, prefix.prefix_digest, plan_digest
        except (ValueError, TypeError, AttributeError):
            pass  # An uncertifiable plan receives the ordinary strict label.
        cached = getattr(ctrl, '_undo_label_cache', None)
        if cache_key is not None and cached is not None and cached[0] == cache_key:
            return cached[1]
    label = _strict_undo_label(ctrl)
    if cache_key is not None:
        ctrl._undo_label_cache = cache_key, label
    return label


def _strict_undo_label(ctrl):
    voided = ctrl.ledger._voided_ids()
    event = next((e for e in reversed(ctrl.ledger.events)
                  if e.etype not in (UNDO, SESSION_STARTED) and e.event_id not in voided), None)
    if event is None:
        return '无可撤销记录'
    if event.etype == 'SHOE_CREATED' and ctrl._shoe_undo_group():
        return '撤销换靴'
    if event.etype == 'ROUND_STARTED' and ctrl._automatic_undo_group():
        return '撤销录牌及自动下一局'
    if event.etype == 'CORRECTION' and event.payload.get('target_etype') == 'SHOE_CREATED':
        return '撤销新靴事实确认'
    if event.etype in (CARD_DEALT, 'PLAYER_ACTION'):
        try:
            receipt = json.loads(event.evidence or '{}')
        except (ValueError, TypeError):
            receipt = {}
        if isinstance(receipt, dict) and receipt.get('bclc_ace_input_v1') and ctrl._bclc_ace_undo_group():
            return '撤销本次玩家输入及非BJ确认'
    if event.etype == CARD_REVEALED:
        return '撤销底牌揭示' if ctrl.ledger._find(event.payload['target_event_id']).payload.get('face_state') == 'hidden' else '撤销未知牌揭示'
    if event.etype == CARD_DEALT:
        if event.payload.get('face_state') == 'hidden':
            if ctrl._automatic_hole_undo_group():
                return '撤销本次录牌及自动暗牌'
            try:
                receipt = json.loads(event.evidence or '{}')
            except (ValueError, TypeError):
                receipt = {}
            return ('撤销自动暗牌（关联待核对）'
                    if isinstance(receipt, dict) and receipt.get('trigger_event_id') else '撤销暗牌登记')
        return f"撤销{event.payload['seat']}发牌"
    names = {'CORRECTION': '本次纠错', 'ROUND_STARTED': '本轮开始', 'ROUND_ENDED': '本轮结算／结束',
             'SHOE_CREATED': '牌靴创建', 'SHOE_ENDED': '牌靴结束', 'PEEK_NEGATIVE': '非BJ检查',
             'PLAYER_ACTION': f"{event.payload.get('seat', '')}{event.payload.get('action', '')}",
             'BURN_CARDS': '烧牌登记', 'OBSERVATION_GAP': '缺口标记'}
    return '撤销' + names.get(event.etype, event.etype)
