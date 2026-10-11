"""Resolve queued recording input against the preceding durable result, without Tk."""
from ..core.table import DEALER, TableError
from .automatic_flow import dealer_finish_message
from .deal_entry import (MODE_INITIAL, MODE_MANUAL, MODE_CONTINUATION,
                         MODE_DEALER, MODE_PEEK_WAIT, MODE_UNALIGNED, next_open_hand)


class RecordingInputRejected(TableError):
    """An explicit input-stage refusal before any persistent command is called.

    The owner must still prove that the complete durable and in-memory prefixes
    are unchanged. This type alone is never evidence of a rollback.
    """


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
    """Resolve a queued input from its freshly validated private command."""
    with ctrl._recording_command_frame():
        return _record_input(ctrl, intent)


def _record_input(ctrl, intent):
    """Use the same controller commands/validation as direct recording.

    Automatic button/key input follows the worker's *current* frozen plan. A
    manual selection remains explicit; navigation is disabled during a batch.
    """
    kind, selection = intent['kind'], intent['selection']
    plan, seg = ctrl.entry_plan, ctrl.state().current
    if seg is None:
        raise RecordingInputRejected('请先新建牌靴')
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
    try:
        # This is a read-only routing query, before deal/reveal is invoked.
        automatic_target = (ctrl.recording_dealer_route(seat, hand_id)
                            if kind in ('rank', 'card') and not revealing else None)
    except TableError as error:
        raise RecordingInputRejected(str(error)) from error
    if kind not in ('peek', 'undo'):
        if plan and (plan.input_paused or kind in ('rank', 'card', 'hole') and plan.mode == MODE_UNALIGNED):
            raise RecordingInputRejected('录入已暂停；请先核对已保存记录，不要重复录牌')
        if plan and plan.mode == MODE_PEEK_WAIT and not revealing and not automatic_target:
            raise RecordingInputRejected('等待实际庄家检查结果，不能继续录入玩家牌')
        if (plan and plan.mode == MODE_CONTINUATION and plan.initial_complete()
                and (kind == 'action' or not revealing)):
            acting = next_open_hand(seg.table, plan.participating_seats)
            if acting and (seat, hand_id) != acting[:2]:
                raise RecordingInputRejected(f'当前实际行动位置：{acting[0]}／第{acting[2]}手；导航不会提前行动')
    slot_id = None
    if kind in ('rank', 'card') and not revealing and plan and plan.mode in (MODE_INITIAL, MODE_MANUAL):
        slot = plan.slot()
        if slot and slot.expected_face != 'shown':
            raise RecordingInputRejected('当前槽位是庄家暗牌，请按“暗牌 .”确认底牌已发，再继续录入')
        if slot and slot.slot_id not in plan.filled_slots:
            if not plan.paused:
                try:
                    slot = plan.accept_shown_on_cursor()
                except ValueError as error:
                    raise RecordingInputRejected(str(error)) from error
                seat, hand_id, slot_id = slot.seat, None, slot.slot_id
            elif slot.seat == seat:
                slot_id = slot.slot_id
    if kind in ('rank', 'card'):
        rank = intent['rank']
        if seat == DEALER and not revealing and dealer_finish_message(seg.table):
            raise RecordingInputRejected(dealer_finish_message(seg.table) + '；请核对本轮记录，不再追加庄家牌')
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
                    raise RecordingInputRejected('此手有多张未知牌，请在时间线选中原发牌事件')
                elif candidates:
                    target_id = candidates[0]
            if not target_id:
                raise RecordingInputRejected('该手牌没有待揭示的暗牌/未知牌')
            ctrl.reveal(target_id, rank, selection['suit'], auto_next=intent['auto_next'])
            message = f'暗牌揭示为 {rank}（原牌不重复扣除）'
        else:
            ctrl.deal_shown(seat, rank, hand_id=hand_id, suit=selection['suit'],
                            initial_slot_id=slot_id, auto_next=intent['auto_next'])
            message = f'已保存 {seat} ← {rank}'
    elif kind in ('hole', 'hidden', 'unknown'):
        if kind == 'hole':
            if plan is None:
                raise RecordingInputRejected('尚未冻结本轮发牌计划')
            try:
                slot = plan.accept_hole_on_cursor()
            except ValueError as error:
                raise RecordingInputRejected(str(error)) from error
            seat, hand_id = slot.seat, None
        if kind == 'unknown':
            ctrl.deal_unknown(seat, hand_id)
            message = '已登记未知牌面，待核对'
        else:
            ctrl.deal_hidden(seat, hand_id)
            message = '已登记暗牌存在，待揭示'
    elif kind == 'action':
        if not hand_id:
            raise RecordingInputRejected('目标座位还没有手牌')
        # Check only the existing read-only legality projection. Do not catch
        # errors from player_action, which may include a durable write.
        states = seg.table.action_states(seat, hand_id)
        action = intent['action']
        if action in states and not states[action].allowed:
            raise RecordingInputRejected(f'{action} 不被允许：{states[action].reason}')
        ctrl.player_action(seat, hand_id, action)
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
