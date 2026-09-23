"""Pre-deal EV of an explicitly declared policy, separate from current-hand EV."""
from dataclasses import asdict, dataclass
import hashlib
import json
import math

from ..core.cards import TEN_RANKS
from ..core.rules import CONFIRM_VERIFIED
from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS
from ..ledger.ledger import EventLedger
from .contracts import InputUnavailable, canonical, digest

ENGINE = 'opening-finite-mc-v2'
STRATEGY = 'composition-replacement-policy-two-hand-v2'
SCHEMA = 'hakimi-opening-ev-v1'
SAMPLES = 2_000_000
NOTE = ('按当前人数及本人座位模拟；其他玩家采用同一固定策略。策略按开局组成推导，'
        '模拟发牌为有限不放回；不是有限牌盒最优策略。每1单位底注计分牌、加倍后的合计净收益。')


@dataclass(frozen=True)
class OpeningInput:
    session_id: str
    shoe_id: str
    round_id: str | None
    through_seq: int
    prefix_digest: str
    rules_json: str
    counts: tuple
    participants: tuple
    focal: int
    engine_version: str = ENGINE
    strategy_version: str = STRATEGY

    @property
    def rules_digest(self):
        return digest(json.loads(self.rules_json))

    @property
    def input_digest(self):
        return digest(self.to_dict())

    @property
    def seed(self):
        # Same composition and scenario reuse the same sample, even after undo.
        key = canonical([self.rules_json, self.counts, self.participants, self.focal, STRATEGY])
        return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) & 0x7fffffff

    def to_dict(self):
        return asdict(self)

    def validate(self):
        if (not self.session_id or not self.shoe_id or type(self.through_seq) is not int or self.through_seq < 1
                or not self.prefix_digest or self.engine_version != ENGINE or self.strategy_version != STRATEGY):
            raise ValueError('开局输入身份不完整')
        validate_counts(self.counts, len(self.participants), self.focal)
        if len(set(self.participants)) != len(self.participants):
            raise ValueError('参与座位重复')
        rules = json.loads(self.rules_json)
        validate_rules(rules)
        if any(n > rules['n_decks'] * (16 if i == 9 else 4) for i, n in enumerate(self.counts)):
            raise ValueError('剩余牌超过牌盒容量')


def validate_counts(counts, seats, focal):
    if (len(counts) != 10 or any(type(n) is not int or not 0 <= n <= (128 if i == 9 else 32) for i, n in enumerate(counts))
            or type(seats) is not int or not 1 <= seats <= 7 or type(focal) is not int or not 0 <= focal < seats):
        raise ValueError('开局牌面组成或参与座位无效')
    if sum(counts) < 2 * seats + 2:
        raise InputUnavailable('INSUFFICIENT_CARDS', '剩余牌不足以开始本轮')


def validate_rules(rules):
    required = dict(confirm_status=CONFIRM_VERIFIED, shoe_model='finite_no_replacement',
                    dealer_soft17='S17', american_hole_card=True,
                    dealer_bj_extra_bet_rule='all_bets_lost', double_on_totals=None, split_match='same_value',
                    max_split_hands=2, resplit_aces=False, split_ace_hit_once=True,
                    split_deal_order='sequential_complete_first', start_from_new_shoe=True,
                    burn_cards_known=True, initial_burn_count=0)
    if (any(key not in rules or type(rules[key]) is not type(value) or rules[key] != value for key, value in required.items())
            or rules.get('blackjack_payout') != [3, 2] or type(rules.get('double_after_split')) is not bool
            or rules.get('surrender') not in (None, 'late')
            or rules.get('check_bj_when') not in ('before_player_actions_A_T', 'before_player_actions_A')
            or type(rules.get('n_decks')) is not int or rules['n_decks'] not in (6, 7, 8)):
        raise InputUnavailable('OPENING_RULES_UNSUPPORTED', '开局估算暂支持常用S17／同值两手桌规')


def build_opening_input(ledger, participants=('玩家1',), my_seat='玩家1', direction='forward'):
    prefix = ledger.to_list()
    state = EventLedger.from_list(ledger.session_id, prefix).replay()
    seg = state.current
    if seg is None or seg.closed:
        raise InputUnavailable('NO_OPEN_SHOE', '尚无可用牌盒')
    shoe, table = seg.shoe, seg.table
    if any(r['status'] != 'complete' for r in seg.round_observations):
        raise InputUnavailable('OBSERVATION_INCOMPLETE', '此前轮次有漏录或完整性未知')
    if shoe.gap or shoe.pending_candidates or shoe.burn_unknown:
        raise InputUnavailable('UNKNOWN_REMOVALS', '剩余牌况不完整')
    if shoe.unrevealed_out or seg.unresolved:
        raise InputUnavailable('UNREVEALED', '等待暗牌揭示')
    if table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
        if any(h.cards for seat in [table.dealer, *table.players.values()] for h in seat.hands):
            raise InputUnavailable('ROUND_ACTIVE', '本轮进行中，结束后计算')
        participants = tuple(table.participants)
    else:
        if direction not in ('forward', 'reverse'):
            raise InputUnavailable('SEAT_ORDER', '请核对发牌方向')
        participants = tuple(participants if direction == 'forward' else reversed(participants))
    if not participants or my_seat not in participants:
        raise InputUnavailable('FOCAL_SEAT', '请先选择参与座位和本人座位')
    rules_json = seg.rules.to_json()
    validate_rules(json.loads(rules_json))
    counts = tuple(shoe.remaining[r] for r in ('A', '2', '3', '4', '5', '6', '7', '8', '9')) + (
        sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    if shoe.physical_remaining() != sum(counts) or not shoe.conservation_check()[0]:
        raise InputUnavailable('COUNT_MISMATCH', '剩余牌数量需核对')
    result = OpeningInput(ledger.session_id, seg.shoe_id, seg.round_id, prefix[-1]['seq'], digest(prefix),
                          rules_json, counts, participants, participants.index(my_seat))
    result.validate()
    return result


def summarize_histogram(histogram, samples, confidence=0.95):
    if (type(samples) is not int or not 2 <= samples <= 4_000_000 or len(histogram) != 17
            or any(type(n) is not int or n < 0 for n in histogram) or sum(histogram) != samples
            or confidence != 0.95):
        raise ValueError('开局估算样本分布不完整')
    outcomes = tuple((i - 8) / 2 for i in range(17))
    mean = sum(n * x for n, x in zip(histogram, outcomes)) / samples
    variance = sum(n * (x - mean) ** 2 for n, x in zip(histogram, outcomes)) / (samples - 1)
    # Maurer & Pontil (2009), Thm 4, scaled [-4,4], two-sided union bound.
    logarithm = math.log(4 / (1 - confidence))
    radius = math.sqrt(2 * variance * logarithm / samples) + 7 * 8 * logarithm / (3 * (samples - 1))
    low, high = max(-4, mean - radius), min(4, mean + radius)
    return dict(ev=mean, advantage_percent=100 * mean, sample_variance=variance,
                confidence=confidence, interval=[low, high], radius=radius,
                sign='positive' if low > 0 else 'negative' if high < 0 else 'uncertain',
                standard_error=math.sqrt(variance / samples))
