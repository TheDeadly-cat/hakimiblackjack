"""Read-only descriptions for the compact recording workflow."""
from dataclasses import dataclass

from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS
from .deal_entry import MODE_INITIAL, MODE_PEEK_WAIT, MODE_UNALIGNED, MODE_DEALER


@dataclass(frozen=True)
class FlowState:
    stage: str
    message: str
    label: str = ''
    command: str = ''
    round_id: str = ''


def current_flow(ctrl, seg=None):
    seg, plan = seg or ctrl.state().current, ctrl.entry_plan
    if seg is None or seg.closed:
        return FlowState('setup', '确认本桌设置，或在工作台恢复记录。', '开始／恢复记录', 'review')
    if seg.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS):
        return FlowState('start', '开始下一轮会继续使用当前牌靴；换靴请到设置单独确认。', '开始本轮', 'start')
    if seg.shoe.gap or seg.shoe.pending_candidates:
        return FlowState('review', '存在观察缺口或待确认牌；请先核对记录。', '核对记录', 'review')
    if plan is None or plan.mode == MODE_UNALIGNED:
        return FlowState('review', getattr(plan, 'pause_reason', '') or '录入位置需核对。', '核对录入位置', 'review')
    if plan.input_paused:
        return FlowState('paused', '录入已暂停，恢复后继续原来的位置。', '恢复录入', 'resume')
    if plan.mode == MODE_INITIAL:
        return FlowState('initial', plan.prompt('', '').splitlines()[0])
    if plan.mode == MODE_PEEK_WAIT:
        return FlowState('peek', '按实际情况确认非BJ，或选择庄家录入实际开出的底牌。', '已检查，确认非BJ', 'peek')
    problem = ctrl.round_completion_problem(seg)
    if not problem:
        return FlowState('ready', '请确认本轮移出的牌均已记录；点击后结算并继续同一牌靴。',
                         '确认完整并下一轮', 'next', seg.round_id)
    if plan.mode == MODE_DEALER:
        return FlowState('dealer', problem + '；按顺序输入庄家实际牌面。')
    return FlowState('player', '记录当前手的实际动作；不能操作时请查看旁边的原因。')
