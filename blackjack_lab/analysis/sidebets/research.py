"""Explicit hindsight research: reconstruct an old predeal inventory using later corrections.

The full later prefix is retained. Cards physically dealt after the old cutoff are
excluded from depletion, never used as known winning cards. This is NOT an old
forecast or a current betting opportunity, even when the composition becomes known.
"""
from collections import Counter

from ..contracts import digest,InputUnavailable
from ..information import prepare_prefix
from ...core.cards import RANKS,SUITS
from ...ledger.ledger import EventLedger
from ...ledger.card_inventory import TYPES,project_prepared,effective_payloads
from .information import build_input,INPUT_SCHEMA


def origin_of(snapshot):
    if snapshot['purpose']=='corrected_predeal':return dict(snapshot['origin'])
    if snapshot['purpose']!='forecast':raise ValueError('仅发牌前记录可以进行后来信息复算')
    return {k:snapshot[k] for k in ('session_id','shoe_id','window_round_no','through_seq','prefix_digest','seat')}


def build_corrected_input(ledger,origin,profile):
    seq,prefix,_=prepare_prefix(ledger)
    if (type(origin) is not dict or set(origin)!={'session_id','shoe_id','window_round_no','through_seq','prefix_digest','seat'}
            or origin['session_id']!=ledger.session_id or type(origin['through_seq']) is not int):
        raise ValueError('原发牌前时点身份无效')
    prior=[e for e in prefix if e['seq']<=origin['through_seq']]
    if digest(prior)!=origin['prefix_digest']:raise ValueError('原发牌前前缀不符')
    before=build_input(EventLedger.from_list(ledger.session_id,prior),origin['seat'],profile)
    if (before['shoe_id'],before['window_round_no'])!=(origin['shoe_id'],origin['window_round_no']):
        raise ValueError('原发牌前窗口不符')
    segment=next((s for s in ledger.replay().segments if s.shoe_id==origin['shoe_id']),None)
    if segment is None:raise InputUnavailable('ORIGINAL_SHOE_VOIDED','原牌靴已撤销，只能读原记录')
    full=project_prepared(ledger.session_id,seq,prefix,segment)
    cards=[c for c in full['cards'] if c['deal_seq']<=origin['through_seq']]
    missing=Counter();rules=segment.rules
    for card in cards:
        if card['rank']=='T':missing['ten_rank_unspecified']+=1
        elif card['rank'] not in RANKS:missing['unknown_rank']+=1
        if card['suit'] is None:missing['unknown_suit']+=1
    burns=rules.initial_burn_count or 0
    original_events={}
    for event,payload in effective_payloads(prefix):
        if event['shoe_id']!=origin['shoe_id'] or event['seq']>origin['through_seq']:continue
        original_events[event['event_id']]=event
        if event['etype']=='BURN_CARDS':burns+=payload['count']
        if event['etype']=='OBSERVATION_GAP' and not payload.get('resolved'):missing['observation_gap']+=1
    if burns:missing['unknown_burn']=burns
    if any(r['event_id'] in original_events and r['status']!='complete' for r in segment.round_observations):
        missing['prior_round_incomplete']=1
    if rules.start_from_new_shoe is not True:missing['initial_composition_unknown']=1
    if rules.burn_cards_known is not True or rules.initial_burn_count is None:missing['burn_count_unknown']=1
    removed=Counter((c['rank'],c['suit']) for c in cards if c['rank'] in RANKS and c['suit'] in SUITS)
    over=any(n>rules.n_decks for n in removed.values())
    composition=dict(status='invalid' if over else 'unavailable' if missing else 'available',counts=None,
        missing=dict(missing),reason_code='SUIT_CAPACITY' if over else 'INCOMPLETE_INFORMATION' if missing else None,
        reason='后来核对后仍有容量冲突' if over else '后来核对后仍有数据缺项：'+str(dict(missing)) if missing else '按后来核对信息还原原发牌前组成；不是原预测')
    if not over and not missing:
        counts=[rules.n_decks-removed[t] for t in TYPES]
        if sum(counts)!=52*rules.n_decks-len(cards):raise ValueError('研究投影物理数量不一致')
        composition['counts']=counts
    return dict(schema=INPUT_SCHEMA,session_id=ledger.session_id,shoe_id=origin['shoe_id'],
        round_id=before['round_id'],window_round_no=origin['window_round_no'],through_seq=seq,
        prefix_digest=digest(prefix),seat=origin['seat'],purpose='corrected_predeal',origin=dict(origin),
        profile=profile.to_dict(),rules_digest=profile.rules_digest,composition=composition,
        original_cards={'perfect_pairs':[],'twenty_one_plus_three':[]},
        original_card_ids={'perfect_pairs':[],'twenty_one_plus_three':[]},
        excluded_later_card_ids=[c['event_id'] for c in full['cards'] if c['deal_seq']>origin['through_seq']],
        stage='hindsight_research',timing_note='采纳后来纠正或揭示的信息，还原原发牌前库存；仅用于研究，不是当时预测或新机会')
