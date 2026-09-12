"""Chinese display adapter for the total objective; no computation in widgets."""
from ..analysis.contracts import AVAILABLE, STATUS_ZH
from ..analysis.split_contracts import DAS_ENGINE_LEGACY, is_das_engine
from ..analysis.split_service import SPLIT_ACTION_ZH


def format_split_result(result, historical=False):
    info = result['input']
    das = is_das_engine(result.get('engine_version'))
    prefix = '历史分析 · ' if historical else '当前 · '
    lines = [f"{prefix}{info['seat']} · {info['n_decks']}副 · 庄家 {info['dealer_up']}",
             '两手顺序分牌DAS' if das else '两手顺序分牌']
    details = [f"选中手：{info['hand_id']}", f"当前行动手：{info['active_hand_id'] or '两手均已完成'}"]
    for i,hand in enumerate(info['hands'],1):
        state = '已闭合' if hand['closed'] else '等待确定要发的一张牌' if hand['forced_draw'] else '可决策'
        stake = hand.get('bet_units', 1)
        details.append(f"第{i}手 · {' '.join(hand['ranks'])} · {state} · 注额{stake}")
    if historical:
        lines.append('原时点结果，不代表当前输入')
    if result.get('engine_version') == DAS_ENGINE_LEGACY:
        lines.append('旧算法历史结果：部分普通补牌等待场景存在已知问题，数值保留供审计，不代表重新认证；请按原事件前缀另存复算。')
    if result['status'] != AVAILABLE:
        lines.append(f"{STATUS_ZH.get(result['status'],result['status'])}：{result['reason']}")
        return '\n'.join(lines)
    highest = result.get('highest_ev_action')
    if highest:
        label = '当前流程合计EV' if highest in ('deal','complete') else '模型内最高合计EV · '+SPLIT_ACTION_ZH[highest]
        lines.append(f"{label}：{result['actions'][highest]['ev']:+.6f}")
    if das:
        lines.extend([f"当前已投入：{result['current_investment']} 单位",
                      'EV单位：原始1单位注的最终净收益；分牌后至少2，每手DAS再+1，合计最大±4'])
    else:
        lines.extend([f"当前总投入：{result['current_investment']} 单位", 'EV单位：原始1单位注的最终净收益'])
    if result['partial_comparison']:
        lines.append('部分动作比较：合法性待核对，不给完整动作最高结论。')
    distributions=[]
    for action,item in result['actions'].items():
        if item['status'] != AVAILABLE:
            lines.append(f"{SPLIT_ACTION_ZH[action]}：{STATUS_ZH[item['status']]}")
            continue
        label = '确定发牌' if action=='deal' else '等待庄家' if action=='complete' else SPLIT_ACTION_ZH[action]
        if das:
            extra = (f"立刻追加{item['additional_investment']}"
                     f" · 此后可能再追加至多{item.get('possible_future_additional', 0)}"
                     f" · 最终投入上界{item.get('max_final_investment', item['total_investment'])}")
            lines.append(f"{label} EV {item['ev']:+.6f} · {extra}")
            distributions.append(f"{label}（{extra}）：")
        else:
            lines.append(f"{label} EV {item['ev']:+.6f} · 总投入{item['total_investment']}")
            distributions.append(f"{label}（另追加{item['additional_investment']}）：")
        if 'hand_evs' in item:
            distributions.append('各手边际 EV：' + ' / '.join(f"{v:+.6f}" for v in item['hand_evs']))
        distributions.append('  '.join(f"{float(v):+g}:{p:.2%}" for v,p in item['net_distribution'].items()))
    lines.extend(['',*details,'','合计净收益分布：',*distributions])
    if highest in ('deal','complete'):
        lines.append('当前为确定流程，数值包含其后所有适用的补/停决策。')
    if result.get('all_computed_ev_negative'):
        lines.append('已计算动作 EV 均为负；较高只表示可能少亏。')
    probabilities = result['probabilities']
    if (probabilities.get('next_target_draw')
            and result.get('probability_status',{}).get('next_target_draw',AVAILABLE)==AVAILABLE):
        lines.append(f"当前行动手再抽一张的爆牌概率：{probabilities['hit_bust']:.3%}")
    scope = '无再分 / 非A允许DAS / 分A一张；分A的21为普通21。' if das else '无再分 / 无DAS / 分A一张；分A的21为普通21。'
    lines.extend(['', '首手完成后才给第二手补第二张。', '两手共享剩余牌与同一庄家；无独立卷积。',
                  '净收益已计入本金损失，不再次扣除投入。',
                  scope,
                  '有限不放回枚举，双精度舍入；无采样或概率截断。',
                  '当前合计EV不等于下一轮开局优势，不提供注额建议。',
                  f"耗时 {result['elapsed_seconds']:.3f}s · 事件前缀 #{info['through_seq']}",
                  f"引擎：{result['engine_version']}", f"策略：{result['strategy_version']}",
                  f"输入摘要：{result['input_digest'][:16]}", f"规则摘要：{result['rules_digest'][:16]}"])
    return '\n'.join(lines)
