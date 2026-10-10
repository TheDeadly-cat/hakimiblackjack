"""Exact integer combination weights, bounded to the supported 6/7/8-deck shoe."""
from fractions import Fraction
from functools import lru_cache
from itertools import combinations_with_replacement, product
from math import comb

from ...ledger.card_inventory import TYPES, TYPE_INDEX
from ..contracts import digest
from .contracts import CATEGORIES, SidebetProfile

ENGINE = 'sidebet-52-combinations-v1'
SCHEMA = 'hakimi-sidebet-probability-v1'
COLORS = (0,1,1,0)  # S H D C


def validate_counts(counts):
    if type(counts) not in (tuple,list) or len(counts)!=52 or any(type(n) is not int or not 0<=n<=8 for n in counts):
        raise ValueError('边注组成必须是52个0～8的整数')


def _pair_kind(i,j):
    if i//4!=j//4:return 'loss'
    if i%4==j%4:return 'perfect'
    return 'coloured' if COLORS[i%4]==COLORS[j%4] else 'mixed'


def _three_kind(i,j,k,a23,qka,ka2,priority):
    a,b,c=sorted((i//4,j//4,k//4))
    flush=i%4==j%4==k%4
    trips=a==c
    straight=(a<b<c and ((a>0 and c-a==2) or
        ((a,b,c)==(0,1,2) and a23) or ((a,b,c)==(0,11,12) and qka) or ((a,b,c)==(0,1,12) and ka2)))
    matches={'suited_trips':trips and flush,'straight_flush':straight and flush,
             'trips':trips,'straight':straight,'flush':flush}
    return next((kind for kind in priority if matches[kind]),'loss')


@lru_cache(maxsize=8)
def _triple_classes(a23,qka,ka2,priority):
    # Only deterministic classification is cached; no inventory, probability, or payout cache.
    return tuple((i,j,k,_three_kind(i,j,k,a23,qka,ka2,priority))
                 for i,j,k in combinations_with_replacement(range(52),3))


def classify(name,cards,profile=None):
    profile=profile or SidebetProfile()
    k=2 if name=='perfect_pairs' else 3 if name=='21+3' else 0
    if not k or len(cards)!=k or any(tuple(c) not in TYPE_INDEX for c in cards):
        raise ValueError('边注牌型需要完整原始牌面和花色')
    indexes=tuple(TYPE_INDEX[tuple(c)] for c in cards)
    if k==3 and any(v is None for v in (profile.a23,profile.qka,profile.ka2)):
        choices = [(False,True) if v is None else (v,) for v in (profile.a23,profile.qka,profile.ka2)]
        categories = {_three_kind(*indexes,*rules,profile.three_priority) for rules in product(*choices)}
        if len(categories) != 1:
            raise ValueError('21+3的A顺子规则待确认')
        return categories.pop()
    return (_pair_kind(*indexes) if k==2 else
        _three_kind(*indexes,profile.a23,profile.qka,profile.ka2,profile.three_priority))


def distribution(name,counts,profile=None):
    validate_counts(counts);profile=profile or SidebetProfile()
    if name not in CATEGORIES:raise ValueError('不支持的边注')
    if name=='21+3' and any(v is None for v in (profile.a23,profile.qka,profile.ka2)):
        raise ValueError('21+3的A顺子规则待确认，暂停概率与EV')
    k=2 if name=='perfect_pairs' else 3
    n=sum(counts)
    if n<k:raise ValueError(f'剩余不足{k}张牌')
    ways=dict.fromkeys((*CATEGORIES[name],'loss'),0)
    if k==2:
        for i,x in enumerate(counts):
            if not x:continue
            for j in range(i,52):
                weight=x*(x-1)//2 if i==j else x*counts[j]
                if weight:ways[_pair_kind(i,j)]+=weight
    else:
        for i,j,k,kind in _triple_classes(profile.a23,profile.qka,profile.ka2,profile.three_priority):
            x,y,z=counts[i],counts[j],counts[k]
            if not x or not y or not z:continue
            if i==k:weight=x*(x-1)*(x-2)//6
            elif i==j:weight=x*(x-1)//2*z
            elif j==k:weight=x*y*(y-1)//2
            else:weight=x*y*z
            if weight:ways[kind]+=weight
    denominator=comb(n,2 if name=='perfect_pairs' else 3)
    if sum(ways.values())!=denominator:raise ArithmeticError('组合权重未守恒')
    return {kind:Fraction(value,denominator) for kind,value in ways.items()},ways,denominator


def calculate(counts,profile=None):
    validate_counts(counts);profile=profile or SidebetProfile()
    result=dict(schema=SCHEMA,engine_version=ENGINE,counts=list(counts),counts_digest=digest(list(counts)),
                profile=profile.to_dict(),rules_digest=profile.rules_digest,bets={})
    for name in CATEGORIES:
        try:probabilities,ways,denominator=distribution(name,counts,profile)
        except ValueError as error:
            result['bets'][name]=dict(status='unavailable',reason=str(error),probabilities=None,ev=None)
            continue
        ev=profile.ev(name,probabilities);hit=1-probabilities['loss']
        result['bets'][name]=dict(status='available',probabilities={k:float(p) for k,p in probabilities.items()},
            fractions={k:str(p) for k,p in probabilities.items()},ways=ways,combinations=denominator,
            hit_probability=float(hit),hit_fraction=str(hit),ev=None if ev is None else float(ev),
            ev_fraction=None if ev is None else str(ev),ev_scope=profile.confirmation,
            reason='赔付未核对，EV不可用' if ev is None else '自建研究EV，非真实桌规确认' if profile.confirmation=='research' else '已核对赔付')
    return result
