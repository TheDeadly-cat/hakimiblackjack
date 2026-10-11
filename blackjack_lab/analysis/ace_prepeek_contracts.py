"""Observed BCLC initial hand, before the dealer's actual Ace check.

This identity describes contingent strategies, not permission to act and not
an observed negative peek. The existing single/split identities stay unchanged.
"""
from dataclasses import asdict, dataclass
from fractions import Fraction
import json
import math

from .contracts import ACTION_ZH, canonical, digest
from .split_contracts import SplitHand, VALUES, supported_bclc_rules
from .seat_scenario import MODEL, validate_scenario
from ..core.cards import RANKS, is_natural_blackjack
from ..core.rules import RuleProfile, CONFIRM_VERIFIED

ACE_PREPEEK_INPUT_SCHEMA = 'hakimi-bclc-ace-prepeek-input-v1'
ACE_PREPEEK_RESULT_SCHEMA = 'hakimi-bclc-ace-prepeek-result-v1'
ACE_PREPEEK_ENGINE = 'bclc-ace-prepeek-mixture-v1'
ACE_PREPEEK_STRATEGY = 'BJ-before-action-then-bclc-no-DAS-v1'
ACE_PREPEEK_STAGE = 'before_ace_peek'
ACE_PREPEEK_SCOPE = ('BCLC-v2/S17/3:2/US-A-peek-before-actions/zero-burn/'
                     'single-player/initial-unsplit/contingent-non-BJ-actions/no-DAS')


@dataclass(frozen=True)
class AcePrepeekInput:
    session_id: str
    shoe_id: str
    round_id: str
    through_seq: int
    prefix_digest: str
    seat: str
    hand_id: str
    n_decks: int
    rules_json: str
    information_json: str
    counts: tuple[int, ...]             # The unknown hole is INCLUDED.
    physical_remaining: int
    player_ranks: tuple[str, ...]
    card_event_ids: tuple[str, ...]
    legal_actions: tuple[str, ...]      # Strategies only if the actual check is non-BJ.
    bj_numerator: int
    bj_denominator: int
    dealer_up: int = 1
    peek_negative: bool = False         # The actual observed fact, never the kernel condition.
    uncertain_actions: tuple[str, ...] = ()
    schema: str = ACE_PREPEEK_INPUT_SCHEMA
    engine_version: str = ACE_PREPEEK_ENGINE
    strategy_version: str = ACE_PREPEEK_STRATEGY
    support_scope: str = ACE_PREPEEK_SCOPE

    @property
    def player(self):
        return tuple(VALUES[r] for r in self.player_ranks)

    @property
    def natural_blackjack(self):
        return is_natural_blackjack(self.player_ranks)

    @property
    def dealer_bj_fraction(self):
        return Fraction(self.bj_numerator, self.bj_denominator)

    @property
    def dealer_bj_probability(self):
        return float(self.dealer_bj_fraction)

    @property
    def kernel_actions(self):
        return ('stand',) if self.natural_blackjack else self.legal_actions

    @property
    def pre_split(self):
        return True

    @property
    def hands(self):
        # Reuse only the old per-action numeric validator, not its peek fact.
        return (SplitHand(self.hand_id, None, self.player_ranks, self.card_event_ids,
                          self.player_ranks, self.card_event_ids, False, False, False, False),)

    @property
    def input_digest(self):
        return digest(self.to_dict())

    @property
    def rules_digest(self):
        return digest(json.loads(self.rules_json))

    def to_dict(self):
        return {**asdict(self), 'dealer_bj_probability': self.dealer_bj_probability,
                'dealer_bj_fraction': str(self.dealer_bj_fraction)}

    def validate(self):
        if (self.schema != ACE_PREPEEK_INPUT_SCHEMA or self.engine_version != ACE_PREPEEK_ENGINE
                or self.strategy_version != ACE_PREPEEK_STRATEGY):
            raise ValueError('检查前评估的输入、引擎或策略身份不匹配')
        if (type(self.n_decks) is not int or self.n_decks != 8
                or type(self.through_seq) is not int or self.through_seq < 1
                or any(not isinstance(v, str) or not v for v in
                       (self.session_id, self.shoe_id, self.round_id, self.seat, self.hand_id))
                or not isinstance(self.prefix_digest, str) or len(self.prefix_digest) != 64
                or any(c not in '0123456789abcdef' for c in self.prefix_digest)):
            raise ValueError('检查前评估的来源身份或前缀无效')
        rules = RuleProfile.from_json(self.rules_json)
        if not (supported_bclc_rules(rules) and rules.confirm_status == CONFIRM_VERIFIED
                and rules.start_from_new_shoe is True and rules.burn_cards_known is True
                and type(rules.initial_burn_count) is int and rules.initial_burn_count == 0
                and rules.shoe_model == 'finite_no_replacement' and rules.american_hole_card is True
                and rules.blackjack_payout == (3, 2) and rules.dealer_bj_extra_bet_rule == 'all_bets_lost'
                and rules.double_on_totals is None):
            raise ValueError('检查前评估仅支持已锁定BCLC v2、完整八副新靴、零烧牌及A先检查')
        if type(self.dealer_up) is not int or self.dealer_up != 1 or self.peek_negative is not False:
            raise ValueError('检查前评估必须是A明牌且实际尚未记录非BJ')
        if (any(type(v) is not tuple for v in (self.counts, self.player_ranks, self.card_event_ids,
                                             self.legal_actions, self.uncertain_actions))
                or len(self.counts) != 10 or any(type(n) is not int or n < 0 for n in self.counts)
                or type(self.physical_remaining) is not int or self.physical_remaining != sum(self.counts) - 1
                or sum(self.counts) < 2 or len(self.player_ranks) != 2
                or any(r not in VALUES for r in self.player_ranks)
                or len(self.card_event_ids) != 2 or len(set(self.card_event_ids)) != 2
                or any(not isinstance(v, str) or not v for v in self.card_event_ids)
                or self.uncertain_actions):
            raise ValueError('检查前评估要求完整原始两张牌与唯一未揭示底牌')
        if (type(self.bj_numerator) is not int or type(self.bj_denominator) is not int
                or self.bj_numerator != self.counts[9] or self.bj_denominator != sum(self.counts)):
            raise ValueError('BJ概率必须来自含底牌的实际剩余T/总底牌池')
        expected_actions = () if self.natural_blackjack or self.bj_numerator == self.bj_denominator else (
            ('stand', 'hit', 'double', 'split') if self.player[0] == self.player[1]
            else ('stand', 'hit', 'double'))
        if self.legal_actions != expected_actions:
            raise ValueError('检查前策略集合与原始手牌/非BJ分支不一致')
        info = json.loads(self.information_json)
        if (not isinstance(info, dict) or info.get('evaluation_stage') != ACE_PREPEEK_STAGE
                or info.get('negative_peek') is not False or info.get('full_initial_deal') is not True
                or info.get('gap') is not False or type(info.get('pending_candidates')) is not int
                or info['pending_candidates'] != 0 or type(info.get('burn_unknown')) is not int
                or info['burn_unknown'] != 0 or type(info.get('unrevealed_out')) is not int
                or info['unrevealed_out'] != 1 or info.get('dealer_hole') != 'one_unknown_physical_card'):
            raise ValueError('检查前信息包含未知组成、错误stage或伪造的非BJ事实')
        exact, bucket = info.get('exact_out'), info.get('t_bucket_out')
        if (not isinstance(exact, dict) or set(exact) != set(RANKS)
                or any(type(n) is not int or not 0 <= n <= 32 for n in exact.values())
                or type(bucket) is not int or not 0 <= bucket <= 128):
            raise ValueError('检查前实际移除牌组成无效')
        expected_counts = tuple(32 - exact[r] for r in ('A','2','3','4','5','6','7','8','9')) + (
            128 - sum(exact[r] for r in ('10','J','Q','K')) - bucket,)
        if self.counts != expected_counts:
            raise ValueError('检查前计数与完整八副牌的实际移除牌不一致')
        observations = info.get('round_observations')
        if (not isinstance(observations, list) or any(not isinstance(r, dict)
                or r.get('status') != 'complete' for r in observations)):
            raise ValueError('此前轮次存在观察完整性未知或漏牌')
        players, participants = info.get('initial_hands'), info.get('participants')
        if (not isinstance(participants, list) or not 1 <= len(participants) <= 7
                or len(set(participants)) != len(participants) or self.seat not in participants
                or any(p not in [f'玩家{i}' for i in range(1,8)] for p in participants)
                or not isinstance(players, list) or len(players) != len(participants)):
            raise ValueError('检查前初始牌参与者不完整')
        all_ids, shown = [], []
        for item, seat in zip(players, participants):
            if (not isinstance(item, dict) or item.get('seat') != seat
                    or not isinstance(item.get('hand_id'), str) or not item['hand_id']
                    or item.get('from_split') is not False or type(item.get('bet_units')) is not int
                    or item['bet_units'] != 1 or item.get('actions') != []
                    or item.get('closed') is not False or item.get('forced_draw') is not False
                    or not isinstance(item.get('ranks'), list) or len(item['ranks']) != 2
                    or any(r not in VALUES for r in item['ranks'])
                    or not isinstance(item.get('card_event_ids'), list) or len(item['card_event_ids']) != 2
                    or any(not isinstance(v, str) or not v for v in item['card_event_ids'])):
                raise ValueError('检查前必须是完整原始起手且没有已执行玩家动作')
            if seat == self.seat and (item['hand_id'] != self.hand_id
                    or tuple(item['ranks']) != self.player_ranks or tuple(item['card_event_ids']) != self.card_event_ids):
                raise ValueError('目标初始牌与源信息不一致')
            all_ids.extend(item['card_event_ids']); shown.extend(item['ranks'])
        dealer = info.get('dealer_cards')
        if (not isinstance(dealer, list) or len(dealer) != 2
                or any(not isinstance(c, dict) or not isinstance(c.get('event_id'), str)
                       or not c['event_id'] for c in dealer)
                or dealer[0].get('rank') != 'A' or dealer[0].get('face_state') != 'shown'
                or dealer[1].get('rank') is not None or dealer[1].get('face_state') != 'hidden'):
            raise ValueError('检查前仅允许A明牌加原来的一张未知底牌')
        all_ids.extend(c['event_id'] for c in dealer); shown.append('A')
        if len(set(all_ids)) != len(all_ids):
            raise ValueError('检查前初始牌物理归属重复')
        if any(shown.count(r) > exact[r] for r in RANKS) or shown.count('T') > bucket:
            raise ValueError('初始牌未包含在实际移除组成中')
        states = info.get('action_states')
        if (not isinstance(states, dict) or set(states) != set(ACTION_ZH.values())
                or any(not isinstance(s, dict) or s.get('allowed') is not False
                       or s.get('reason_code') != 'PEEK_REQUIRED' for s in states.values())):
            raise ValueError('实际动作状态必须仍等待检查，不能将计算条件冒充事实')
        scope = ACE_PREPEEK_SCOPE if len(participants) == 1 else ACE_PREPEEK_SCOPE.replace('single-player', MODEL)
        if self.support_scope != scope:
            raise ValueError('检查前评估的多人条件范围身份不匹配')
        validate_scenario(info, self.seat, self.support_scope)

    @classmethod
    def from_dict(cls, data):
        if not isinstance(data, dict):
            raise ValueError('检查前输入必须为对象')
        data = dict(data)
        probability = data.pop('dealer_bj_probability')
        fraction = data.pop('dealer_bj_fraction')
        for name in ('counts','player_ranks','card_event_ids','legal_actions','uncertain_actions'):
            data[name] = tuple(data[name])
        snapshot = cls(**data)
        snapshot.validate()
        if (type(probability) not in (int,float) or not math.isfinite(probability)
                or probability != snapshot.dealer_bj_probability or fraction != str(snapshot.dealer_bj_fraction)):
            raise ValueError('输入BJ概率与实际T底牌池不一致')
        return snapshot
