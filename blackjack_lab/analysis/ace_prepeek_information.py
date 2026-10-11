"""Build a before-check input from an already validated visible event prefix."""
from dataclasses import asdict
import json

from .ace_prepeek_contracts import AcePrepeekInput, ACE_PREPEEK_STAGE, ACE_PREPEEK_SCOPE
from .contracts import ACTION_ZH, InputUnavailable, UNSUPPORTED, canonical
from .seat_scenario import MODEL, scenario_for
from .split_contracts import VALUES, supported_bclc_rules
from ..core.cards import TEN_RANKS, UNKNOWN, is_natural_blackjack
from ..core.table import DEALER, PHASE_DEALING, PHASE_IN_PROGRESS
from ..ui.read_snapshot import event_prefix_digest


def is_ace_prepeek_candidate(current):
    if not current or not supported_bclc_rules(current.rules) or current.table.dealer_hole_checked_negative:
        return False
    dealer = current.table.dealer.hands[0].cards if current.table.dealer.hands else []
    return bool(dealer and dealer[0].rank == 'A' and any(c.rank == UNKNOWN for c in dealer))


def build_ace_prepeek_input(session_id, seat, hand_id, seq, prefix, current, *, other_players_stand=False):
    """Never edits the table, ledger, rules or observed negative-peek fact."""
    if (not is_ace_prepeek_candidate(current) or current.closed
            or current.table.phase not in (PHASE_DEALING, PHASE_IN_PROGRESS)):
        raise InputUnavailable('ACE_PREPEEK_STAGE', '当前不是支持的BCLC A检查前原始起手')
    rules, shoe, table = current.rules, current.shoe, current.table
    if seat not in table.participants or seat == DEALER:
        raise InputUnavailable('TARGET_CHANGED', '目标不是实际参与的初始玩家手牌')
    if len(table.participants) > 1 and not other_players_stand:
        raise InputUnavailable('SINGLE_PLAYER_ONLY', '多人检查前比较需明确其他玩家之后不再补牌的条件', UNSUPPORTED)
    dealer = table.dealer.hands[0].cards
    holes = [event_id for event_id, item in current.unresolved.items() if item['seat'] == DEALER
             and item['round_id'] == current.round_id and item['face_state'] == 'hidden']
    if (len(dealer) != 2 or dealer[0].rank != 'A' or dealer[1].rank != UNKNOWN
            or len(holes) != 1 or shoe.unrevealed_out != 1
            or holes[0] != dealer[1].event_id):
        raise InputUnavailable('DEALER_INFORMATION', '检查前需要A明牌及原来的一张未知底牌')
    initial_hands = []
    for name in table.participants:
        hands = table.players[name].hands
        if len(hands) != 1:
            raise InputUnavailable('ACE_PREPEEK_STAGE', '检查前仅支持尚未采取动作的原始起手', UNSUPPORTED)
        hand = hands[0]
        if (len(hand.cards) != 2 or hand.hidden_cards or hand.from_split or hand.actions
                or hand.doubled or hand.awaiting_hit or hand.is_closed or hand.bet_units != 1):
            raise InputUnavailable('ACE_PREPEEK_STAGE', '先录齐原始两张牌；检查前不能已有玩家动作', UNSUPPORTED)
        initial_hands.append(dict(seat=name, hand_id=hand.hand_id, ranks=list(hand.ranks),
            card_event_ids=[c.event_id for c in hand.cards], from_split=False, bet_units=1,
            actions=[], closed=False, forced_draw=False))
    hand = table.players[seat].hands[0]
    if hand_id is not None and hand_id != hand.hand_id:
        raise InputUnavailable('TARGET_CHANGED', '目标手牌与当前初始手牌不一致')
    counts = tuple(shoe.remaining[r] for r in ('A','2','3','4','5','6','7','8','9')) + (
        sum(shoe.remaining[r] for r in TEN_RANKS) - shoe.t_bucket_out,)
    natural = is_natural_blackjack(hand.ranks)
    legal = () if natural or counts[9] == sum(counts) else (
        ('stand','hit','double','split') if VALUES[hand.ranks[0]] == VALUES[hand.ranks[1]]
        else ('stand','hit','double'))
    information = dict(evaluation_stage=ACE_PREPEEK_STAGE, negative_peek=False, full_initial_deal=True,
        exact_out=shoe.exact_out, t_bucket_out=shoe.t_bucket_out,
        unrevealed_out=shoe.unrevealed_out, burn_unknown=shoe.burn_unknown,
        gap=shoe.gap, pending_candidates=shoe.pending_candidates,
        dealer_hole='one_unknown_physical_card', round_observations=current.round_observations,
        participants=list(table.participants), initial_hands=initial_hands,
        dealer_cards=[dict(event_id=c.event_id, rank=None if c.rank == UNKNOWN else c.rank,
                           face_state='hidden' if c.rank == UNKNOWN else 'shown') for c in dealer],
        action_states={action: asdict(state) for action, state in table.action_states(seat, hand.hand_id).items()})
    scope = ACE_PREPEEK_SCOPE
    if len(table.participants) > 1:
        information['seat_scenario'] = scenario_for(table, seat)
        scope = scope.replace('single-player', MODEL)
    snapshot = AcePrepeekInput(session_id, current.shoe_id, current.round_id, seq,
        event_prefix_digest(prefix), seat, hand.hand_id, rules.n_decks, canonical(json.loads(rules.to_json())),
        canonical(information), counts, shoe.physical_remaining(), tuple(hand.ranks),
        tuple(c.event_id for c in hand.cards), legal, counts[9], sum(counts), support_scope=scope)
    try:
        snapshot.validate()
    except (ValueError, TypeError, KeyError) as error:
        raise InputUnavailable('ACE_PREPEEK_INVALID', str(error), UNSUPPORTED) from error
    return snapshot
