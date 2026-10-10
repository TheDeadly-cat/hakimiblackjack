"""Fast dealer natural-BJ scalar; no action gate, worker, or fabricated peek."""
from fractions import Fraction

from .contracts import digest
from .information import prepare_prefix
from ..core.cards import TEN_RANKS, UNKNOWN, is_natural_blackjack
from ..core.table import PHASE_DEALING, PHASE_IN_PROGRESS

ENGINE = 'dealer-natural-information-v1'


def scalar(counts, up=None, negative=False):
    if len(counts) != 10 or any(type(n) is not int or n < 0 for n in counts):
        raise ValueError('BJ需要10类非负整数组成')
    if up is not None and (type(up) is not int or up not in range(1, 11)):
        raise ValueError('庄家明牌点值无效')
    if type(negative) is not bool:raise ValueError('检查状态必须为布尔值')
    n, a, t = sum(counts), counts[0], counts[-1]
    if up is None:
        if n < 2: raise ValueError('不足两张牌')
        return Fraction(2*a*t, n*(n-1))
    if up not in (1, 10): return Fraction()
    if n < 1: raise ValueError('底牌池为空')
    winning = t if up == 1 else a
    if negative:
        if n == winning: raise ValueError('非BJ检查与剩余底牌组成矛盾')
        return Fraction()
    return Fraction(winning, n)


def evaluate_prepared(session_id, seq, prefix, current):
    from ..ui.read_snapshot import event_prefix_digest
    result = dict(engine_version=ENGINE, session_id=session_id, as_of_seq=seq,
                  prefix_digest=event_prefix_digest(prefix), shoe_id=current.shoe_id if current else None,
                  round_id=current.round_id if current else None, phase='unavailable',
                  probability=None, fraction=None, known=False, forecast=False,
                  basis='', reason_code='NO_SHOE', reason='尚无牌靴')
    def publish(phase, probability, basis, known=False):
        result.update(phase=phase, probability=float(probability), fraction=str(probability),
                      basis=basis, known=known, forecast=not known, reason_code=None, reason='')
        return result
    def unavailable(code, reason):
        result.update(reason_code=code, reason=reason)
        return result
    if current is None: return result
    shoe, table, rules = current.shoe, current.table, current.rules
    if current.closed: return unavailable('SHOE_CLOSED', '牌靴已结束')
    cards = table.dealer.hands[0].cards if table.dealer.hands else []
    shown = [c for c in cards if c.rank != UNKNOWN]
    # Facts about the original hand don't require an intact shoe composition.
    if len(cards) >= 2 and all(c.rank != UNKNOWN for c in cards[:2]):
        bj = len(cards) == 2 and is_natural_blackjack([c.rank for c in cards])
        return publish('revealed', Fraction(int(bj)), '已确认BJ' if bj else '已确认非BJ', True)
    if len(cards) >= 3:
        return publish('resolved', Fraction(), '已确认非BJ（至少三张）', True)
    up = shown[0].rank if len(shown) == 1 else None
    value = 1 if up == 'A' else 10 if up in (*TEN_RANKS, 'T') else int(up) if up else None
    if value is not None and value not in (1, 10):
        return publish('impossible', Fraction(), '明牌2～9，不可能天然BJ', True)
    counts = tuple(shoe.remaining[r] for r in ('A','2','3','4','5','6','7','8','9')) + (sum(shoe.remaining[r] for r in TEN_RANKS)-shoe.t_bucket_out,)
    composition_known = (rules.start_from_new_shoe is True and rules.burn_cards_known is True
        and rules.initial_burn_count is not None and not shoe.gap and not shoe.pending_candidates
        and not shoe.burn_unknown and shoe.conservation_check()[0])
    if table.dealer_hole_checked_negative:
        if composition_known and value in (1, 10):
            try: scalar(counts, value, True)
            except ValueError as error: return unavailable('PEEK_CONTRADICTION', str(error))
        return publish('excluded', Fraction(), '已排除BJ（实际检查）', True)
    if not composition_known:
        return unavailable('COMPOSITION_UNKNOWN', '剩余组成不完整，BJ风险无法量化')
    holes = [info for info in current.unresolved.values() if info['seat']=='庄家'
             and info['round_id']==current.round_id and info['face_state']=='hidden']
    if shoe.unrevealed_out != len(holes) or len(holes)>1:
        return unavailable('EXTRA_UNKNOWN_CARD', '存在未核对的未知牌')
    if value is None and holes:
        return unavailable('UPCARD_UNKNOWN', '庄家明牌尚未确认')
    try: probability = scalar(counts, value)
    except ValueError as error: return unavailable('INSUFFICIENT_CARDS', str(error))
    if value is not None:
        return publish('conditional', probability, '未排除BJ；底牌池含暗牌及未发牌' if holes else '未排除BJ；底牌尚未登记')
    active = table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS)
    other_cards = active and any(h.cards for seat in table.players.values() for h in seat.hands)
    return publish('awaiting_upcard' if other_cards else 'predeal', probability,
                   '明牌待录，按当前已知组成' if other_cards else '发牌前起手')


def evaluate(ledger, through_seq=None):
    try:
        return evaluate_prepared(ledger.session_id, *prepare_prefix(ledger, through_seq))
    except Exception as error:
        return dict(engine_version=ENGINE, session_id=ledger.session_id, as_of_seq=through_seq,
                    prefix_digest=None, shoe_id=None, round_id=None, phase='unavailable',
                    probability=None, fraction=None, known=False, forecast=False, basis='',
                    reason_code='INVALID_PREFIX', reason='记录需核对：'+str(error))


def label(result):
    if result['probability'] is None:
        return 'BJ — · '+result['reason']
    return f"BJ {result['probability']:.2%} · {result['basis']}"
