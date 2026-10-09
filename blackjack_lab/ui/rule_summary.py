"""Compact description of selected rules, never a recorded negative-peek fact."""


def rule_summary(rules):
    parts = [rules.dealer_soft17 or '停牌规则待核对']
    if rules.profile_id == 'bclc-playnow-classic-two-initial-v1' and rules.version == 2 and rules.double_after_split is False:
        parts.append('分牌后禁止加倍')
    if rules.check_bj_when == 'before_player_actions_A':
        parts += ['仅A检查', '十点未排除BJ']
    elif rules.check_bj_when == 'before_player_actions_A_T':
        parts.append('A/十点检查')
    else:
        parts.append('BJ检查待核对')
    if rules.dealer_bj_extra_bet_rule == 'all_bets_lost':
        parts.append('庄家BJ追加注全输')
    else:
        parts.append('追加注规则待核对')
    if rules.split_deal_order == 'both_second_cards_first':
        parts.append('两手先补齐')
    elif rules.split_deal_order == 'sequential_complete_first':
        parts.append('先完成第一手')
    else:
        parts.append('分牌顺序待核对')
    return '｜'.join(parts)
