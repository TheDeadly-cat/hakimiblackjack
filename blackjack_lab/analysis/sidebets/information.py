"""Stage-bound side-bet inputs and results; predictions and observed categories stay separate."""
import time
import uuid

from ..contracts import digest, InputUnavailable
from ..information import prepare_prefix
from ...core.table import PHASE_DEALING, PHASE_IN_PROGRESS
from ...ledger.card_inventory import project_prepared, original_cards
from .contracts import SidebetProfile, CATEGORIES
from .exact import calculate, classify, ENGINE

INPUT_SCHEMA='hakimi-sidebet-input-v1'
RESULT_SCHEMA='hakimi-sidebet-result-v1'


def window(current):
    if current is None or current.closed:return None
    active=current.table.phase in (PHASE_DEALING,PHASE_IN_PROGRESS)
    number=current.table.round_no if active else current.table.round_no+1
    return current.shoe_id,number


def dealing_started(current):
    return bool(current and current.table.phase in (PHASE_DEALING,PHASE_IN_PROGRESS)
        and any(h.cards for seat in [current.table.dealer,*current.table.players.values()] for h in seat.hands))


def build_input(ledger,seat,profile=None,purpose='forecast',through_seq=None):
    profile=profile or SidebetProfile()
    seq,prefix,current=prepare_prefix(ledger,through_seq)
    if window(current) is None:raise InputUnavailable('NO_ACTIVE_SHOE','尚无进行中的牌靴')
    if seat not in current.table.players:raise InputUnavailable('SEAT_UNKNOWN','边注需指定玩家座位')
    if purpose not in ('forecast','observed'):raise ValueError('边注时点类型无效')
    active=current.table.phase in (PHASE_DEALING,PHASE_IN_PROGRESS)
    if active and seat not in current.table.participants:
        raise InputUnavailable('SEAT_INACTIVE','该座位未参与本轮')
    if purpose=='forecast' and dealing_started(current):
        raise InputUnavailable('BETTING_CLOSED','本轮已发牌，仅可查看封盘前原记录或单独复算历史')
    if purpose=='observed' and not active:
        raise InputUnavailable('NO_CURRENT_HAND','没有可展示的当前原始牌')
    inventory=project_prepared(ledger.session_id,seq,prefix,current)
    composition={k:inventory[k] for k in ('status','counts','missing','reason_code','reason')}
    if composition['counts'] is not None:composition['counts']=list(composition['counts'])
    originals=original_cards(inventory,current.round_id,seat) if purpose=='observed' else {'perfect_pairs':[],'twenty_one_plus_three':[]}
    return dict(schema=INPUT_SCHEMA,session_id=ledger.session_id,shoe_id=current.shoe_id,
        round_id=current.round_id if active else None,window_round_no=window(current)[1],
        through_seq=seq,prefix_digest=digest(prefix),seat=seat,purpose=purpose,
        profile=profile.to_dict(),rules_digest=profile.rules_digest,composition=composition,
        original_cards=originals,original_card_ids={k:[c['event_id'] for c in v] for k,v in originals.items()},
        stage='local_predeal' if purpose=='forecast' else 'closed_observed',
        timing_note='本地录牌阶段，不表示已连接真实下注窗口')


def compute(snapshot):
    profile=SidebetProfile.from_dict(snapshot['profile'])
    if snapshot['purpose'] in ('forecast','corrected_predeal'):
        composition=snapshot['composition']
        if composition['status']!='available':
            return dict(kind='forecast',bets={name:dict(status='unavailable',probabilities=None,ev=None,
                reason=composition['reason']) for name in CATEGORIES})
        return dict(kind='forecast',**calculate(snapshot['composition']['counts'],profile))
    bets={}
    if snapshot['composition']['status']=='invalid':
        return dict(kind='observed',bets={name:dict(status='unavailable',category=None,net_units=None,
            reason=snapshot['composition']['reason']) for name in CATEGORIES})
    for name,k,key in (('perfect_pairs',2,'perfect_pairs'),('21+3',3,'twenty_one_plus_three')):
        cards=snapshot['original_cards'][key]
        if len(cards)!=k:
            bets[name]=dict(status='unavailable',category=None,net_units=None,reason='原始结算牌未齐')
            continue
        try:category=classify(name,[(c['rank'],c['suit']) for c in cards],profile)
        except ValueError:
            bets[name]=dict(status='unavailable',category=None,net_units=None,reason='原始牌面需细分或补花色')
            continue
        payouts=profile.payouts(name);net=None if payouts is None else payouts[category]
        bets[name]=dict(status='available',category=category,net_units=None if net is None else float(net),
            net_fraction=None if net is None else str(net),ev_scope=profile.confirmation,
            reason='已记录的原始牌型，非下注前概率',card_ids=[c['event_id'] for c in cards])
    return dict(kind='observed',bets=bets)


def execute(snapshot,request_id=None):
    start=time.perf_counter();output=compute(snapshot)
    return dict(schema=RESULT_SCHEMA,engine_version=ENGINE,request_id=request_id or uuid.uuid4().hex,
        created_at=time.time(),elapsed_seconds=time.perf_counter()-start,input=snapshot,
        input_digest=digest(snapshot),output=output,
        status='available' if any(v['status']=='available' for v in output['bets'].values()) else 'unavailable')
