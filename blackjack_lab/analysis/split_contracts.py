"""V0.2b1: immutable visible-prefix identity, two sequential hands, original-unit net.

The existing four-hand research template and v1 input schema are unchanged.
"""
from dataclasses import asdict, dataclass
import json

from .contracts import AnalysisInput, canonical, digest, research_rules

SPLIT_PROFILE = "research-s17-us-peek-two-sequential-v1"
SPLIT_INPUT_SCHEMA = "hakimi-split-analysis-input-v1"
SPLIT_RESULT_SCHEMA = "hakimi-analysis-result-v2"
SPLIT_ENGINE = "v0.2b1-finite-two-hand-1"
SPLIT_STRATEGY = "sequential-two-hand-total-net-hit-stand-v1"
SPLIT_ORDER = "sequential_complete_first"
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


def supported_split_rules(rules):
    return (rules.profile_id == SPLIT_PROFILE and rules.version == 1
            and rules.max_split_hands == 2 and rules.split_deal_order == SPLIT_ORDER
            and rules.split_match == "same_rank" and rules.double_after_split is False
            and rules.resplit_aces is False and rules.split_ace_hit_once is True)


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

    def validate(self):
        if (not isinstance(self.hand_id, str) or not self.hand_id
                or self.parent_id is not None and (not isinstance(self.parent_id, str) or not self.parent_id)
                or any(type(v) is not tuple for v in (self.ranks, self.card_event_ids, self.origin_ranks, self.origin_event_ids))
                or not self.ranks or any(not isinstance(r, str) or r not in VALUES for r in self.ranks)
                or len(self.ranks) != len(self.card_event_ids)
                or any(not isinstance(v, str) or not v for v in self.card_event_ids)
                or len(set(self.card_event_ids)) != len(self.card_event_ids)
                or any(type(v) is not bool for v in (self.from_split, self.split_ace, self.closed, self.forced_draw))
                or type(self.bet_units) is not int or self.bet_units != 1):
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
        return AnalysisInput(self.session_id, self.shoe_id, self.round_id, self.through_seq,
            self.prefix_digest, self.seat, h.hand_id, self.n_decks, self.rules_json, self.information_json,
            self.counts, self.physical_remaining, h.values, h.ranks, self.dealer_up, self.peek_negative,
            self.legal_actions, self.uncertain_actions)

    def validate(self):
        from ..core.rules import RuleProfile
        if type(self.dealer_up) is not int or not 1 <= self.dealer_up <= 10:
            raise ValueError("庄家明牌点值无效")
        if self.schema != SPLIT_INPUT_SCHEMA or not supported_split_rules(RuleProfile.from_json(self.rules_json)):
            raise ValueError("不是已声明的两手顺序研究模板；禁止把旧四手规则截成两手")
        if (any(type(v) is not tuple for v in (self.hands, self.pending_hand_ids, self.counts, self.legal_actions, self.uncertain_actions))
                or len(self.hands) not in (1, 2) or any(type(h) is not SplitHand for h in self.hands)):
            raise ValueError("分牌输入必须包含不可变的一手或两手")
        for h in self.hands:
            h.validate()
        ids = tuple(h.hand_id for h in self.hands)
        cards = tuple(e for h in self.hands for e in h.card_event_ids)
        if len(set(ids)) != len(ids) or self.hand_id not in ids or len(set(cards)) != len(cards):
            raise ValueError("目标身份或物理牌归属重复")
        pending = tuple(h.hand_id for h in self.hands if not h.closed)
        if self.pending_hand_ids != pending or self.active_hand_id != (pending[0] if pending else None):
            raise ValueError("当前行动手和顺序队列不一致")
        info = json.loads(self.information_json)
        if info.get("split_order_violations") != []:
            raise ValueError("实际录入顺序不符合首手完成后才发第二手的研究流程")
        # Reuse the existing finite-shoe/rules/information identity guard without
        # mapping split actions into single-hand calculations.
        basis = self.single_input()
        if self.pre_split:
            if self.hands[0].from_split or self.hands[0].closed or self.hands[0].forced_draw:
                raise ValueError("分牌前输入不是可决策的原手")
            basis.validate()
        else:
            from dataclasses import replace
            replace(basis, legal_actions=(), uncertain_actions=()).validate()
            if (not all(h.from_split for h in self.hands) or self.hands[0].parent_id is not None
                    or self.hands[1].parent_id != self.hands[0].hand_id
                    or self.hands[0].origin_ranks != self.hands[1].origin_ranks
                    or self.hands[0].origin_ranks == ("T",)):
                raise ValueError("两手父子关系或同牌面条件不一致")
            if self.active_index == 0 and len(self.hands[1].ranks) != 1:
                raise ValueError("不得包含第二手尚未轮到的未来牌")
            active = self.hands[self.active_index] if self.active_index < 2 else None
            expected = (("complete",) if active is None else ("deal",) if active.forced_draw
                        else ("stand", "hit"))
            if self.legal_actions != expected or self.uncertain_actions:
                raise ValueError("分牌后的动作必须按当前行动手和强制补牌状态计算")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        data["hands"] = tuple(SplitHand.from_dict(h) for h in data["hands"])
        for name in ("counts", "pending_hand_ids", "legal_actions", "uncertain_actions"):
            data[name] = tuple(data[name])
        return cls(**data)
