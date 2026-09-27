"""Read-only 52-type projection of one validated ledger prefix; never a second ledger."""
from collections import Counter

from ..analysis.contracts import digest
from ..analysis.information import prepare_prefix
from ..core.cards import RANKS, SUITS, UNKNOWN
from .events import CARD_DEALT, CARD_REVEALED, CORRECTION, UNDO, FACE_HIDDEN

TYPES = tuple((rank, suit) for rank in RANKS for suit in SUITS)
TYPE_INDEX = {card:i for i,card in enumerate(TYPES)}
SCHEMA = 'hakimi-card-inventory-v1'


def effective_payloads(prefix):
    """Resolve existing UNDO/CORRECTION semantics after ledger replay has validated controls."""
    voided = {e['payload']['target_event_id'] for e in prefix if e['etype']==UNDO}
    fixes = {}
    for event in prefix:
        if event['etype']==CORRECTION and event['event_id'] not in voided:
            fixes.setdefault(event['payload']['target_event_id'],{}).update(event['payload']['payload_fix'])
    for event in prefix:
        if event['event_id'] not in voided and event['etype'] not in (UNDO,CORRECTION):
            yield event, {**event['payload'],**fixes.get(event['event_id'],{})}


def project_prepared(session_id, seq, prefix, current):
    result=dict(schema=SCHEMA,session_id=session_id,through_seq=seq,prefix_digest=digest(prefix),
                shoe_id=current.shoe_id if current else None,n_decks=None,status='unavailable',
                counts=None,cards=[],missing={},reason_code='NO_SHOE',reason='尚无牌靴')
    if current is None:return result
    rules,shoe=current.rules,current.shoe
    cards={}
    for event,payload in effective_payloads(prefix):
        if event['shoe_id']!=current.shoe_id:continue
        if event['etype']==CARD_DEALT:
            cards[event['event_id']]=dict(event_id=event['event_id'],deal_seq=event['seq'],
                round_id=event['round_id'],seat=payload['seat'],initial_face=payload['face_state'],
                rank=payload.get('rank') or UNKNOWN,suit=payload.get('suit'),
                value_event_id=event['event_id'])
        elif event['etype']==CARD_REVEALED:
            card=cards[payload['target_event_id']]
            card.update(rank=payload['rank'],suit=payload.get('suit'),value_event_id=event['event_id'])
    removed=Counter((c['rank'],c['suit']) for c in cards.values()
                    if c['rank'] in RANKS and c['suit'] in SUITS)
    missing=Counter()
    for card in cards.values():
        if card['rank']=='T':missing['ten_rank_unspecified']+=1
        elif card['rank'] not in RANKS:missing['unknown_rank']+=1
        if card['suit'] is None:missing['unknown_suit']+=1
    if shoe.burn_unknown:missing['unknown_burn']+=shoe.burn_unknown
    if shoe.gap:missing['observation_gap']=1
    if shoe.pending_candidates:missing['pending_candidates']=shoe.pending_candidates
    if rules.start_from_new_shoe is not True:missing['initial_composition_unknown']=1
    if rules.burn_cards_known is not True or rules.initial_burn_count is None:missing['burn_count_unknown']=1
    result.update(n_decks=rules.n_decks,cards=list(cards.values()),missing=dict(missing),
                  physical_remaining=shoe.physical_remaining())
    over=[f'{rank}{suit}:{n}>{rules.n_decks}' for (rank,suit),n in removed.items() if n>rules.n_decks]
    if over:
        result.update(status='invalid',reason_code='SUIT_CAPACITY',reason='牌面花色数量超出副数：'+'，'.join(over))
        return result
    # This cross-check binds the projection to the sole physical/rank ledger.
    exact=Counter(c['rank'] for c in cards.values() if c['rank'] in RANKS)
    bucket=sum(c['rank']=='T' for c in cards.values())
    hidden=sum(c['rank'] not in (*RANKS,'T') for c in cards.values())
    if (any(exact[r]!=shoe.exact_out[r] for r in RANKS) or bucket!=shoe.t_bucket_out
            or hidden!=shoe.unrevealed_out or not shoe.conservation_check()[0]):
        result.update(status='invalid',reason_code='LEDGER_MISMATCH',reason='花色投影与原牌靴账不一致')
        return result
    if missing:
        result.update(reason_code='INCOMPLETE_INFORMATION',reason='边注组成不足：'+
            '、'.join({'unknown_suit':'需补花色','ten_rank_unspecified':'T需细分10/J/Q/K',
            'unknown_rank':'存在未知牌','unknown_burn':'烧牌牌面未知','observation_gap':'观察缺口',
            'pending_candidates':'待核对牌','initial_composition_unknown':'非完整新靴',
            'burn_count_unknown':'烧牌数量未核对'}[k] for k in missing))
        return result
    counts=tuple(rules.n_decks-removed[card] for card in TYPES)
    if sum(counts)!=shoe.physical_remaining():
        result.update(status='invalid',reason_code='LEDGER_MISMATCH',reason='52类合计与物理待发数不一致')
        return result
    result.update(status='available',counts=counts,reason_code=None,reason='完整已知52类组成；未作未知花色边缘化')
    return result


def project(ledger, through_seq=None):
    return project_prepared(ledger.session_id,*prepare_prefix(ledger,through_seq))


def original_cards(inventory, round_id, seat):
    """Physical initial IDs remain stable when split moves the second card to another hand."""
    current=[c for c in inventory['cards'] if c['round_id']==round_id]
    player=sorted((c for c in current if c['seat']==seat),key=lambda c:c['deal_seq'])[:2]
    dealer=sorted((c for c in current if c['seat']=='庄家' and c['initial_face']!=FACE_HIDDEN),
                  key=lambda c:c['deal_seq'])[:1]
    return dict(perfect_pairs=player,twenty_one_plus_three=player+dealer)
