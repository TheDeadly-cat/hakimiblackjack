"""Resolve queued recording input against the preceding durable result, without Tk."""
from ..core.table import DEALER, TableError
from ..ledger.ledger import LedgerError
from .automatic_flow import dealer_finish_message
from .deal_entry import (MODE_INITIAL, MODE_MANUAL, MODE_CONTINUATION,
                         MODE_DEALER, MODE_PEEK_WAIT, MODE_UNALIGNED, next_open_hand)


def _hand_id(seg, seat, selection):
    if seat != DEALER and seat not in seg.table.players:
        return None
    hands = seg.table.seat(seat).hands
    if selection['hand_id']:
        return selection['hand_id']
    if selection['hand_mode'] == 'acting':
        return next((h.hand_id for h in hands if not seg.table.split_hand_closed(h)),
                    hands[-1].hand_id if hands else None)
    return hands[-1].hand_id if hands else None


def record_input(ctrl, intent):
    """Use the same controller commands/validation as direct recording.

    Automatic button/key input follows the worker's *current* frozen plan. A
    manual selection remains explicit; navigation is disabled during a batch.
    """
    kind, selection = intent['kind'], intent['selection']
    plan, seg = ctrl.entry_plan, ctrl.state().current
    if seg is None:
        raise LedgerError('请先新建牌靴')
    seat = selection['seat']
    hand_id = _hand_id(seg, seat, selection)
    revealing = selection['mode'] == '揭示'
    if intent['follow_plan'] and not revealing and plan and not plan.paused:
        if plan.mode == MODE_INITIAL and kind in ('rank', 'card'):
            slot = plan.slot()
            if slot:
                seat, hand_id = slot.seat, None
        elif plan.mode in (MODE_CONTINUATION, MODE_DEALER, MODE_PEEK_WAIT):
            seat = plan.continuation_seat or DEALER
            hand_id = plan.continuation_hand_id if seat != DEALER else None
            if not hand_id:
                hand_id = _hand_id(seg, seat, dict(selection, hand_id=None, hand_mode='acting'))
    automatic_target = (ctrl.recording_dealer_route(seat, hand_id)
                        if kind in ('rank', 'card') and not revealing else None)
    if kind not in ('peek', 'undo'):
        if plan and (plan.input_paused or kind in ('rank', 'card', 'hole') and plan.mode == MODE_UNALIGNED):
            raise TableError('录入已暂停；请先核对已保存记录，不要重复录牌')
        if plan and plan.mode == MODE_PEEK_WAIT and not revealing and not automatic_target:
            raise TableError('等待实际庄家检查结果，不能继续录入玩家牌')
        if (plan and plan.mode == MODE_CONTINUATION and plan.initial_complete()
                and (kind == 'action' or not revealing)):
            acting = next_open_hand(seg.table, plan.participating_seats)
            if acting and (seat, hand_id) != acting[:2]:
                raise TableError(f'当前实际行动位置：{acting[0]}／第{acting[2]}手；导航不会提前行动')
    slot_id = None
    if kind in ('rank', 'card') and not revealing and plan and plan.mode in (MODE_INITIAL, MODE_MANUAL):
        slot = plan.slot()
        if slot and slot.expected_face != 'shown':
            raise TableError('当前槽位是庄家暗牌，不能用点值键代替暗牌确认')
        if slot and slot.slot_id not in plan.filled_slots:
            if not plan.paused:
                slot = plan.accept_shown_on_cursor()
                seat, hand_id, slot_id = slot.seat, None, slot.slot_id
            elif slot.seat == seat:
                slot_id = slot.slot_id
    if kind in ('rank', 'card'):
        rank = intent['rank']
        if seat == DEALER and not revealing and dealer_finish_message(seg.table):
            raise TableError(dealer_finish_message(seg.table) + '；请核对本轮记录，不再追加庄家牌')
        if revealing or automatic_target:
            target_id = automatic_target
            if not target_id:
                candidates = [eid for eid, info in seg.unresolved.items()
                              if info['seat'] == seat and info['hand_id'] == hand_id
                              and info['round_id'] == seg.round_id]
                selected_id = intent.get('reveal_event_id')
                if selected_id in candidates:
                    target_id = selected_id
                elif len(candidates) > 1:
                    raise TableError('此手有多张未知牌，请在时间线选中原发牌事件')
                elif candidates:
                    target_id = candidates[0]
            if not target_id:
                raise TableError('该手牌没有待揭示的暗牌/未知牌')
            ctrl.reveal(target_id, rank, selection['suit'], auto_next=intent['auto_next'])
            message = f'暗牌揭示为 {rank}（原牌不重复扣除）'
        else:
            ctrl.deal_shown(seat, rank, hand_id=hand_id, suit=selection['suit'],
                            initial_slot_id=slot_id, auto_next=intent['auto_next'])
            message = f'已保存 {seat} ← {rank}'
    elif kind in ('hole', 'hidden', 'unknown'):
        if kind == 'hole':
            if plan is None:
                raise TableError('尚未冻结本轮发牌计划')
            slot = plan.accept_hole_on_cursor()
            seat, hand_id = slot.seat, None
        if kind == 'unknown':
            ctrl.deal_unknown(seat, hand_id)
            message = '已登记未知牌面，待核对'
        else:
            ctrl.deal_hidden(seat, hand_id)
            message = '已登记暗牌存在，待揭示'
    elif kind == 'action':
        if not hand_id:
            raise TableError('目标座位还没有手牌')
        ctrl.player_action(seat, hand_id, intent['action'])
        message = f'已保存动作：{intent["action"]}'
    elif kind == 'peek':
        ctrl.peek_negative()
        message = '已保存：实际检查底牌，确认非 Blackjack'
    elif kind == 'undo':
        ctrl.undo_last()
        message = '已追加撤销，原始事件保留'
    else:
        raise ValueError('未知录牌输入')
    current = ctrl.state().current
    changed = current.round_id != seg.round_id
    if changed:
        message = '本次录牌、上轮结算和下一轮开始已一并保存'
    return dict(message=message, round_changed=changed, seat=seat, hand_id=hand_id)
