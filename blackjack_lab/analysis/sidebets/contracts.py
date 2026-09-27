"""Explicit research/verified/unconfirmed paytables, separate from category probabilities."""
from dataclasses import asdict, dataclass
from fractions import Fraction

from ..contracts import digest

PP = ('perfect', 'coloured', 'mixed')
PLUS = ('suited_trips', 'straight_flush', 'trips', 'straight', 'flush')
CATEGORIES = {'perfect_pairs': PP, '21+3': PLUS}
NAMES = {'perfect': '完美对子', 'coloured': '同色对子', 'mixed': '混色对子',
         'suited_trips': '同花三条', 'straight_flush': '同花顺', 'trips': '三条',
         'straight': '顺子', 'flush': '同花', 'loss': '未命中'}


@dataclass(frozen=True)
class SidebetProfile:
    profile_id: str = 'research-perfect-pairs-21plus3-v1'
    version: int = 1
    source: str = '自建研究；不是已核对的真实桌规'
    confirmation: str = 'research'
    payout_convention: str = 'net_profit'
    perfect_pairs: tuple = (25, 12, 6)
    twenty_one_plus_three: tuple = (100, 40, 30, 10, 5)
    pair_priority: tuple = PP
    three_priority: tuple = PLUS
    a23: bool = True
    qka: bool = True
    ka2: bool = False
    split_policy: str = 'original_two_only_once'

    def __post_init__(self):
        for name in ('profile_id', 'source'):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError('边注规则身份和来源不能为空')
        if type(self.version) is not int or self.version < 1:
            raise ValueError('边注规则版本必须为正整数')
        if self.confirmation not in ('research', 'unconfirmed', 'verified'):
            raise ValueError('边注规则确认状态无效')
        if self.payout_convention not in ('net_profit', 'gross_return'):
            raise ValueError('需明确净赢或含本金返还倍数')
        for name, length in (('perfect_pairs', 3), ('twenty_one_plus_three', 5)):
            values = getattr(self, name)
            if values is not None:
                if type(values) is not tuple or len(values) != length:
                    raise ValueError('赔付表奖级不完整')
                for value in values:
                    if type(value) not in (int, float, str) or len(str(value)) > 40:
                        raise ValueError('赔付必须是有限非负倍数')
                    try:
                        number = Fraction(str(value))
                    except (ValueError, ZeroDivisionError, OverflowError) as error:
                        raise ValueError('赔付必须是有限非负倍数') from error
                    if number < (1 if self.payout_convention == 'gross_return' else 0):
                        raise ValueError('中奖返还至少含本金；净赢倍数不得为负')
        for name, expected in (('pair_priority', PP), ('three_priority', PLUS)):
            value = getattr(self, name)
            if type(value) is not tuple or len(value) != len(expected) or set(value) != set(expected):
                raise ValueError('优先级必须包含所有奖级且不重复')
        if any(type(getattr(self, name)) is not bool for name in ('a23', 'qka', 'ka2')):
            raise ValueError('A顺子规则必须明确为布尔值')
        if self.split_policy != 'original_two_only_once':
            raise ValueError('本版边注只用原始两张，不支持分牌重复开奖')

    def to_dict(self):
        # JSON-normalized representation; never retain a caller-owned mutable list.
        import json
        return json.loads(json.dumps(asdict(self)))

    @classmethod
    def from_dict(cls, data):
        if type(data) is not dict or set(data) != set(cls.__dataclass_fields__):
            raise ValueError('边注配置字段不完整或含未知字段')
        values = dict(data)
        for name in ('perfect_pairs', 'twenty_one_plus_three', 'pair_priority', 'three_priority'):
            if type(values[name]) is list:
                values[name] = tuple(values[name])
        return cls(**values)

    @property
    def rules_digest(self):
        return digest(self.to_dict())

    def payouts(self, name):
        values = self.perfect_pairs if name == 'perfect_pairs' else self.twenty_one_plus_three if name == '21+3' else None
        if name not in CATEGORIES:
            raise ValueError('不支持的边注')
        if values is None or self.confirmation == 'unconfirmed':
            return None
        offset = 1 if self.payout_convention == 'gross_return' else 0
        return dict(zip(CATEGORIES[name], (Fraction(str(v))-offset for v in values)), loss=Fraction(-1))

    def ev(self, name, probabilities):
        if set(probabilities) != set(CATEGORIES[name]) | {'loss'} or sum(probabilities.values()) != 1:
            raise ValueError('边注概率必须覆盖全部互斥奖级并合计为1')
        if any(p < 0 for p in probabilities.values()):
            raise ValueError('边注概率不能为负')
        payouts = self.payouts(name)
        return None if payouts is None else sum((p*payouts[k] for k,p in probabilities.items()), Fraction())
