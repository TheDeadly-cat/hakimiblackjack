"""V0.2b1: immutable visible-prefix identity, two sequential hands, original-unit net.

The existing four-hand research template and v1 input schema are unchanged.
"""
from dataclasses import asdict, dataclass
import json

from .contracts import AnalysisInput, canonical, digest, research_rules

SPLIT_PROFILE = "research-s17-us-peek-two-sequential-v1"
DAS_PROFILE = "research-s17-us-peek-two-sequential-das-v1"
SAME_VALUE_SPLIT_PROFILE = "research-s17-us-peek-two-sequential-same-value-v1"
SAME_VALUE_DAS_PROFILE = "research-s17-us-peek-two-sequential-das-same-value-v1"
ACE_PEEK_DAS_PROFILE = "research-s17-us-ace-peek-two-sequential-das-same-value-v1"
BOTH_INITIAL_PROFILE = "research-s17-us-ace-peek-two-initial-das-same-value-v1"
ALL_SPLIT_PROFILES = (SPLIT_PROFILE, DAS_PROFILE, SAME_VALUE_SPLIT_PROFILE, SAME_VALUE_DAS_PROFILE, ACE_PEEK_DAS_PROFILE, BOTH_INITIAL_PROFILE)
SPLIT_INPUT_SCHEMA = "hakimi-split-analysis-input-v1"
SPLIT_RESULT_SCHEMA = "hakimi-analysis-result-v2"
SAME_VALUE_SCOPE = "S17/3:2/US-peek/zero-burn/single-player/two-sequential/same-value/no-DAS/no-resplit"
SAME_VALUE_DAS_SCOPE = "S17/3:2/US-peek/zero-burn/single-player/two-sequential/same-value/DAS-non-ace/no-resplit"
SPLIT_ENGINE = "v0.2b1-finite-two-hand-1"
DAS_ENGINE = "v0.2b2-finite-two-hand-das-2"
DAS_ENGINE_LEGACY = "v0.2b2-finite-two-hand-das-1"
SPLIT_STRATEGY = "sequential-two-hand-total-net-hit-stand-v1"
DAS_STRATEGY = "sequential-two-hand-total-net-das-v2"
DAS_STRATEGY_LEGACY = "sequential-two-hand-total-net-das-v1"
BOTH_INITIAL_ENGINE = "finite-two-initial-das-1"
BOTH_INITIAL_STRATEGY = "both-second-cards-visible-total-net-das-v1"
KNOWN_DAS_ENGINES = (DAS_ENGINE_LEGACY, DAS_ENGINE, BOTH_INITIAL_ENGINE)
SPLIT_ORDER = "sequential_complete_first"
BOTH_INITIAL_ORDER = "both_second_cards_first"
HARD_BUDGET_SECONDS = 5.0
P95_TARGET_SECONDS = 2.0
VALUES = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "T": 10}


def split_research_rules(n_decks=6, surrender="late"):
    rules = research_rules(n_decks, surrender)
    rules.profile_id = SPLIT_PROFILE
    rules.game_name = "单玩家两手顺序分牌研究"
    rules.max_split_hands = 2
    rules.split_deal_order = SPLIT_ORDER
    rules.remark = "首手完成后才给第二手补第二张；无再分/无DAS/分A一张；原注合计净收益"
    return rules


def das_research_rules(n_decks=6, surrender="late"):
    rules = split_research_rules(n_decks, surrender)
    rules.profile_id = DAS_PROFILE
    rules.game_name = "单玩家两手顺序分牌DAS研究"
    rules.double_after_split = True
    rules.remark = "首手完成后才给第二手补第二张；无再分/非A允许DAS/分A一张；原注合计净收益"
    return rules


def same_value_split_research_rules(n_decks=6, surrender="late"):
    rules = split_research_rules(n_decks, surrender)
    rules.profile_id = SAME_VALUE_SPLIT_PROFILE
    rules.split_match = "same_value"
    rules.game_name = "单玩家两手顺序分牌同点值研究"
    rules.remark = "同点值配对（含T/T）；首手完成后才给第二手补第二张；无再分/无DAS/分A一张"
    return rules


def same_value_das_research_rules(n_decks=6, surrender="late"):
    rules = das_research_rules(n_decks, surrender)
    rules.profile_id = SAME_VALUE_DAS_PROFILE
    rules.split_match = "same_value"
    rules.game_name = "单玩家两手顺序分牌同点值DAS研究"
    rules.remark = "同点值配对（含T/T）；首手完成后才给第二手补第二张；无再分/非A允许DAS/分A一张"
    return rules


def supported_split_rules(rules):
    return (rules.profile_id == SPLIT_PROFILE and rules.version == 1
            and rules.max_split_hands == 2 and rules.split_deal_order == SPLIT_ORDER
            and rules.split_match == "same_rank" and rules.double_after_split is False
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


def supported_same_value_split_rules(rules):
    return (rules.profile_id == SAME_VALUE_SPLIT_PROFILE and rules.version == 1
            and rules.max_split_hands == 2 and rules.split_deal_order == SPLIT_ORDER
            and rules.split_match == "same_value" and rules.double_after_split is False
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


def is_das_engine(version):
    return version in KNOWN_DAS_ENGINES


def known_das_identity(engine_version, strategy_version):
    return ((engine_version == DAS_ENGINE and strategy_version == DAS_STRATEGY)
            or (engine_version == BOTH_INITIAL_ENGINE and strategy_version == BOTH_INITIAL_STRATEGY)
            or (engine_version == DAS_ENGINE_LEGACY and strategy_version == DAS_STRATEGY_LEGACY))


def supported_das_rules(rules):
    return (rules.profile_id == DAS_PROFILE and rules.version == 1
            and rules.max_split_hands == 2 and rules.split_deal_order == SPLIT_ORDER
            and rules.split_match == "same_rank" and rules.double_after_split is True
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


def supported_same_value_das_rules(rules):
    if supported_both_initial_rules(rules):
        return True
    return (rules.profile_id in (SAME_VALUE_DAS_PROFILE, ACE_PEEK_DAS_PROFILE) and rules.version == 1
            and (rules.profile_id != ACE_PEEK_DAS_PROFILE or rules.check_bj_when == 'before_player_actions_A')
            and rules.max_split_hands == 2 and rules.split_deal_order == SPLIT_ORDER
            and rules.split_match == "same_value" and rules.double_after_split is True
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


def ace_peek_das_research_rules(n_decks=8, surrender='late'):
    rules = same_value_das_research_rules(n_decks, surrender)
    rules.profile_id = ACE_PEEK_DAS_PROFILE
    rules.check_bj_when = 'before_player_actions_A'
    rules.game_name = 'S17同值分牌DAS：仅A明牌检查'
    rules.remark += '；十点明牌不检查，保留庄家BJ风险，追加注全输；未排除BJ时不能晚投降'
    return rules


def both_initial_das_rules(n_decks=8, surrender='late'):
    rules = ace_peek_das_research_rules(n_decks, surrender)
    rules.profile_id = BOTH_INITIAL_PROFILE
    rules.split_deal_order = BOTH_INITIAL_ORDER
    rules.game_name = 'S17同值DAS：两手先补齐，随后顺序行动'
    rules.remark = '分牌后两手各发一张，再按两手已知牌决策；无再分/非A允许DAS/分A一张；十点不检查BJ，追加注全输'
    return rules


def supported_both_initial_rules(rules):
    return (rules.profile_id == BOTH_INITIAL_PROFILE and rules.version == 1
            and rules.check_bj_when == 'before_player_actions_A'
            and rules.max_split_hands == 2 and rules.split_deal_order == BOTH_INITIAL_ORDER
            and rules.split_match == 'same_value' and rules.double_after_split is True
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


def pending_hands(hands, both_initial=False):
    if both_initial and len(hands) == 2:
        missing = tuple(h.hand_id for h in hands if len(h.ranks) == 1)
        if missing:
            return missing + tuple(h.hand_id for h in hands if len(h.ranks) > 1 and not h.closed)
    return tuple(h.hand_id for h in hands if not h.closed)


def declared_two_hand_template(rules):
    return (supported_split_rules(rules) or supported_das_rules(rules)
            or supported_same_value_split_rules(rules) or supported_same_value_das_rules(rules))


def split_pair_legal(ranks, split_match):
    if len(ranks) != 2 or any(rank not in VALUES for rank in ranks):
        return False
    if split_match == "same_value":
        return VALUES[ranks[0]] == VALUES[ranks[1]]
    return ranks[0] == ranks[1] and ranks[0] != "T"


def origin_pair_legal(left, right, split_match):
    if not left or not right or left[0] not in VALUES or right[0] not in VALUES:
        return False
    if split_match == "same_value":
        return VALUES[left[0]] == VALUES[right[0]]
    return left == right and left != ("T",)


def _hand_can_das(hand):
    if hand.split_ace or hand.bet_units != 1 or len(hand.ranks) != 2:
        return False
    score = sum(hand.values)
    aces = hand.values.count(1)
    if 1 in hand.values and score <= 11:
        score += 10
    while score > 21 and aces:
        score -= 10
        aces -= 1
    return score < 21


@dataclass(frozen=True)
class SplitHand:
    hand_id: str
    parent_id: str | None
    ranks: tuple[str, ...]
    card_event_ids: tuple[str, ...]
    origin_ranks: tuple[str, ...]
    origin_event_ids: tuple[str, ...]
    from_split: bool
    split_ace: bool
    closed: bool
    forced_draw: bool
    bet_units: int = 1

    @property
    def values(self):
        return tuple(VALUES[r] for r in self.ranks)

    def validate(self, allow_das=False):
        allowed_units = (1, 2) if allow_das else (1,)
        if (not isinstance(self.hand_id, str) or not self.hand_id
                or self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id)
                or any(type(v) is not tuple for v in (self.ranks, self.card_event_ids, self.origin_ranks, self.origin_event_ids))
                or not self.ranks or any(not isinstance(r, str) or r not in VALUES for r in self.ranks)
                or len(self.ranks) != len(self.card_event_ids)
                or any(not isinstance(v, str) or not v for v in self.card_event_ids)
                or len(set(self.card_event_ids)) != len(self.card_event_ids)
                or any(type(v) is not bool for v in (self.from_split, self.split_ace, self.closed, self.forced_draw))
                or type(self.bet_units) is not int or self.bet_units not in allowed_units):
            raise ValueError("分牌手身份、原始牌面或投入无效")
        length = 1 if self.from_split else 2
        if (len(self.origin_ranks) != length or self.origin_ranks != self.ranks[:length]
                or self.origin_event_ids != self.card_event_ids[:length]):
            raise ValueError("分牌原始牌归属与当前已知牌不一致")
        if self.split_ace != (self.from_split and self.origin_ranks == ("A",)):
            raise ValueError("分A身份与原牌不一致")
        if self.closed and self.forced_draw or self.split_ace and len(self.ranks) > 2:
            raise ValueError("已闭合手不得强制补牌，分A只补一张")
        if len(self.ranks) == 1 and not self.forced_draw:
            raise ValueError("分牌单张手必须等待第二张")
        score = sum(self.values)
        if 1 in self.values and score <= 11:
            score += 10
        if self.from_split and (score >= 21 or self.split_ace and len(self.ranks) == 2) and not self.closed:
            raise ValueError("已达终点的分牌手必须闭合")
        if allow_das and self.bet_units == 2:
            if self.split_ace or len(self.ranks) == 1:
                raise ValueError("分A或单张手不能处于已DAS注额")
            if len(self.ranks) == 2 and not self.forced_draw:
                raise ValueError("已选DAS后必须等待唯一补牌")
            if len(self.ranks) >= 3 and not self.closed:
                raise ValueError("DAS补牌后必须闭合")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        for name in ("ranks", "card_event_ids", "origin_ranks", "origin_event_ids"):
            data[name] = tuple(data[name])
        return cls(**data)


@dataclass(frozen=True)
class SplitAnalysisInput:
    session_id: str
    shoe_id: str
    round_id: str
    through_seq: int
    prefix_digest: str
    seat: str
    hand_id: str                     # Selected hand; may be already completed.
    n_decks: int
    rules_json: str
    information_json: str
    counts: tuple[int, ...]           # Includes the single unknown hole card.
    physical_remaining: int
    hands: tuple[SplitHand, ...]
    active_hand_id: str | None        # First unfinished hand in declared order.
    pending_hand_ids: tuple[str, ...]
    dealer_up: int
    peek_negative: bool
    legal_actions: tuple[str, ...]
    uncertain_actions: tuple[str, ...] = ()
    schema: str = SPLIT_INPUT_SCHEMA
    engine_version: str = SPLIT_ENGINE
    strategy_version: str = SPLIT_STRATEGY
    support_scope: str = "S17/3:2/US-peek/zero-burn/single-player/two-sequential/no-DAS/no-resplit"

    @property
    def input_digest(self):
        return digest(asdict(self))

    @property
    def rules_digest(self):
        return digest(json.loads(self.rules_json))

    @property
    def pre_split(self):
        return len(self.hands) == 1

    @property
    def active_index(self):
        return next((i for i, h in enumerate(self.hands) if h.hand_id == self.active_hand_id), len(self.hands))

    def to_dict(self):
        return asdict(self)

    def single_input(self):
        h = self.hands[0]
        from .seat_scenario import MODEL
        scope = {'support_scope': self.support_scope} if MODEL in self.support_scope else {}
        return AnalysisInput(self.session_id, self.shoe_id, self.round_id, self.through_seq,
            self.prefix_digest, self.seat, h.hand_id, self.n_decks, self.rules_json, self.information_json,
            self.counts, self.physical_remaining, h.values, h.ranks, self.dealer_up, self.peek_negative,
            self.legal_actions, self.uncertain_actions, **scope)

    def validate(self):
        from ..core.rules import RuleProfile
        if type(self.dealer_up) is not int or not 1 <= self.dealer_up <= 10:
            raise ValueError("庄家明牌点值无效")
        rules = RuleProfile.from_json(self.rules_json)
        both_initial = supported_both_initial_rules(rules)
        is_das = supported_das_rules(rules) or supported_same_value_das_rules(rules)
        if self.schema != SPLIT_INPUT_SCHEMA or not declared_two_hand_template(rules):
            raise ValueError("不是已声明的两手顺序研究模板；禁止把旧四手规则截成两手")
        if is_das:
            if both_initial != (self.engine_version == BOTH_INITIAL_ENGINE):
                raise ValueError('两手先补齐的规则必须绑定对应引擎，不得混用旧顺序')
            if both_initial and 'both-second-cards-first' not in self.support_scope:
                raise ValueError('两手先补齐的输入缺少顺序范围')
            if (not known_das_identity(self.engine_version, self.strategy_version)
                    or "DAS-non-ace" not in self.support_scope):
                raise ValueError("DAS模板必须使用已声明的DAS引擎、策略与支持范围")
            if supported_same_value_das_rules(rules) and "same-value" not in self.support_scope:
                raise ValueError("同点值DAS模板必须声明 same-value 支持范围")
            if supported_das_rules(rules) and "same-value" in self.support_scope:
                raise ValueError("旧same_rank DAS快照不得改写为 same-value")
        elif self.engine_version != SPLIT_ENGINE or self.strategy_version != SPLIT_STRATEGY:
            raise ValueError("无DAS模板必须使用b1引擎与策略")
        if supported_same_value_split_rules(rules) and "same-value" not in self.support_scope:
            raise ValueError("同点值模板必须声明 same-value 支持范围")
        if supported_split_rules(rules) and "same-value" in self.support_scope:
            raise ValueError("旧same_rank快照不得改写为 same-value")
        if (any(type(v) is not tuple for v in (self.hands, self.pending_hand_ids, self.counts, self.legal_actions, self.uncertain_actions))
                or len(self.hands) not in (1, 2) or any(type(h) is not SplitHand for h in self.hands)):
            raise ValueError("分牌输入必须包含不可变的一手或两手")
        for h in self.hands:
            h.validate(allow_das=is_das)
        ids = tuple(h.hand_id for h in self.hands)
        cards = tuple(e for h in self.hands for e in h.card_event_ids)
        if len(set(ids)) != len(ids) or self.hand_id not in ids or len(set(cards)) != len(cards):
            raise ValueError("目标身份或物理牌归属重复")
        pending = pending_hands(self.hands, both_initial)
        if self.pending_hand_ids != pending or self.active_hand_id != (pending[0] if pending else None):
            raise ValueError("当前行动手和顺序队列不一致")
        info = json.loads(self.information_json)
        if info.get("split_order_violations") != []:
            raise ValueError("实际录入顺序不符合本牌靴声明的分牌流程")
        # Reuse the existing finite-shoe/rules/information identity guard without
        # mapping split actions into single-hand calculations.
        basis = self.single_input()
        if self.pre_split:
            if self.hands[0].from_split or self.hands[0].closed or self.hands[0].forced_draw or self.hands[0].bet_units != 1:
                raise ValueError("分牌前输入不是可决策的原手")
            ranks = self.hands[0].ranks
            if 'split' in self.legal_actions and not split_pair_legal(ranks, rules.split_match):
                raise ValueError("分牌需要已确认的配对条件；same_rank 不得用十点汇总桶或不同牌面替代")
            basis.validate()
        else:
            from dataclasses import replace
            replace(basis, legal_actions=(), uncertain_actions=()).validate()
            if (not all(h.from_split for h in self.hands) or self.hands[0].parent_id is not None
                    or self.hands[1].parent_id != self.hands[0].hand_id
                    or not origin_pair_legal(self.hands[0].origin_ranks, self.hands[1].origin_ranks, rules.split_match)):
                raise ValueError("两手父子关系或配对条件不一致")
            if not both_initial and self.active_index == 0 and len(self.hands[1].ranks) != 1:
                raise ValueError("不得包含第二手尚未轮到的未来牌")
            if both_initial:
                one, two = self.hands
                if (len(one.ranks) == 1 and len(two.ranks) > 1
                        or len(two.ranks) == 1 and (len(one.ranks) > 2 or one.bet_units != 1)
                        or not one.closed and (len(two.ranks) > 2 or two.bet_units != 1)):
                    raise ValueError('两手初始牌或之后的行动顺序不一致')
            active = self.hands[self.active_index] if self.active_index < 2 else None
            if active is None:
                expected = ("complete",)
            elif active.forced_draw:
                expected = ("deal",)
            elif is_das and _hand_can_das(active):
                expected = ("stand", "hit", "double")
            else:
                expected = ("stand", "hit")
            if self.legal_actions != expected or self.uncertain_actions:
                raise ValueError("分牌后的动作必须按当前行动手和强制补牌状态计算")
        excluded = 9 if self.peek_negative and self.dealer_up == 1 else 0 if self.peek_negative and self.dealer_up == 10 else None
        if self.physical_remaining < 0 or sum(self.counts)-(self.counts[excluded] if excluded is not None else 0) <= 0:
            raise ValueError("没有满足当前信息的物理底牌")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["hands"] = tuple(SplitHand.from_dict(h) for h in data["hands"])
        for name in ("counts", "pending_hand_ids", "legal_actions", "uncertain_actions"):
            data[name] = tuple(data[name])
        return cls(**data)
