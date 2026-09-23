"""Explicit EV signs; an unavailable decision is never displayed as zero."""
from ..analysis.contracts import AVAILABLE, ACTION_ZH, STATUS_ZH


def ev_sign(value):
    return '正EV' if value > 1e-10 else '负EV' if value < -1e-10 else '零EV'


def decision_evs(result):
    if not result or result.get('status') != AVAILABLE:
        return '各动作EV：等待初始牌录齐及当前计算'
    labels = dict(ACTION_ZH, deal='确定发牌', complete='等待庄家')
    parts = []
    for action, item in result.get('actions', {}).items():
        value = (f"{ev_sign(item['ev'])} {item['ev']:+.4f}" if item.get('status') == AVAILABLE
                 else STATUS_ZH.get(item.get('status'), '待计算'))
        parts.append(f'{labels.get(action, action)} {value}')
    return result['input']['seat'] + '：' + '  ·  '.join(parts)
