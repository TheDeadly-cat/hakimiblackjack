"""Explicit per-seat counterfactual; never removes participants from the ledger."""
from dataclasses import replace
import json

from .contracts import InputUnavailable, canonical

MODEL = 'per-seat-others-no-draw-v1'
NOTE = '条件假设：其他玩家之后不再补牌；每录一张牌重新计算。'


def scenario_for(table, seat):
    others = []
    for name in table.participants:
        hands = table.players[name].hands
        if name == seat:
            continue
        if not hands or any(len(h.cards) < 2 or h.hidden_cards for h in hands):
            raise InputUnavailable('TABLE_INCOMPLETE', '先录齐所有参与玩家的已发牌，再分别计算')
        if any(h.awaiting_hit or h.doubled and not h.is_closed for h in hands):
            raise InputUnavailable('OTHER_DRAW_PENDING', f'{name}有已选动作的待补牌，请先录入该牌')
        others.append({'seat': name, 'hands': [
            {'hand_id': h.hand_id, 'cards': [{'event_id': c.event_id, 'rank': c.rank} for c in h.cards]}
            for h in hands]})
    return {'model': MODEL, 'target_seat': seat, 'participants': list(table.participants),
            'other_future_draws': 0, 'others': others}


def with_scenario(snapshot, scenario):
    if scenario is None:
        return snapshot
    information = json.loads(snapshot.information_json)
    information['seat_scenario'] = scenario
    snapshot = replace(snapshot, information_json=canonical(information),
                       support_scope=snapshot.support_scope.replace('single-player', MODEL))
    snapshot.validate()
    return snapshot


def validate_scenario(info, seat, scope):
    scenario = info.get('seat_scenario')
    if scenario is None and MODEL not in scope:
        return
    if not isinstance(scenario, dict) or MODEL not in scope:
        raise ValueError('条件比较缺少独立的计算口径身份')
    participants = scenario.get('participants')
    if (scenario.get('model') != MODEL or scenario.get('target_seat') != seat
            or type(scenario.get('other_future_draws')) is not int or scenario['other_future_draws'] != 0
            or not isinstance(participants, list) or not 2 <= len(participants) <= 7
            or any(p not in [f'玩家{i}' for i in range(1, 8)] for p in participants)
            or len(set(participants)) != len(participants) or seat not in participants):
        raise ValueError('条件比较的座位或其他玩家后续行动假设无效')
    others = scenario.get('others')
    if (not isinstance(others, list) or any(not isinstance(item, dict) for item in others)
            or [item.get('seat') for item in others] != [p for p in participants if p != seat]):
        raise ValueError('条件比较缺少其他参与玩家的牌面记录')
    from ..core.cards import RANKS, TEN_BUCKET
    seen_cards = set()
    for item in others:
        hands = item.get('hands')
        if not isinstance(hands, list) or not hands:
            raise ValueError('条件比较缺少其他玩家手牌')
        for hand in hands:
            if (not isinstance(hand, dict) or not isinstance(hand.get('hand_id'), str)
                    or not hand['hand_id'] or not isinstance(hand.get('cards'), list) or len(hand['cards']) < 2):
                raise ValueError('条件比较的其他玩家手牌尚未完整')
            for card in hand['cards']:
                if (not isinstance(card, dict) or card.get('rank') not in (*RANKS, TEN_BUCKET)
                        or not isinstance(card.get('event_id'), str) or not card['event_id']
                        or card['event_id'] in seen_cards):
                    raise ValueError('条件比较的其他玩家牌面或身份无效')
                seen_cards.add(card['event_id'])


def note_for(info):
    information = json.loads(info.get('information_json', '{}'))
    return NOTE if information.get('seat_scenario', {}).get('model') == MODEL else ''
