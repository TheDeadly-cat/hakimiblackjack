"""Pre-deal opening-advantage contract. Independent of current-hand AnalysisInput.

A_t^π = E[X_{t+1}^π | I_t, R] where I_t is the remaining composition before the
next round is dealt. This is not a retitled current-hand EV.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .contracts import canonical, digest
from .research_windows import WINDOW_PRE_DEAL

PREDEAL_INPUT_SCHEMA = "hakimi-predeal-input-v1"
PREDEAL_RESULT_SCHEMA = "hakimi-predeal-result-v1"
PREDEAL_ENGINE_VERSION = "v0.3f-predeal-small-shoe-1"
PREDEAL_STRATEGY_VERSION = "predeal-unsplit-composition-optimal-v1"
PREDEAL_MAX_REMAINING = 16


def legal_predeal_actions(surrender):
    if surrender is None:
        return ("stand", "hit", "double")
    if surrender == "late":
        return ("stand", "hit", "double", "surrender")
    raise ValueError("发牌前仅验收无投降或晚投降")


def predeal_support_scope(surrender):
    if surrender is None:
        tag = "no-surrender"
    elif surrender == "late":
        tag = "late-surrender"
    else:
        raise ValueError("发牌前仅验收无投降或晚投降")
    return f"S17/3:2/US-peek/{tag}/unsplit/no-insurance/exact-small-shoe"


PREDEAL_SUPPORT = predeal_support_scope("late")
SURRENDER_UNSET = object()


def require_declared_surrender(surrender, *, what="发牌前评估"):
    """Missing is not silently late. None means no surrender."""
    if surrender is SURRENDER_UNSET:
        raise ValueError(f"{what}必须显式给出投降规则，不能默认晚投降")
    legal_predeal_actions(surrender)
    return surrender

# Player, dealer up, player, dealer hole. Same three visible cards as P-P-D
# have equal probability; this order is the American table convention.
DEAL_ORDER = "player-up-player-hole"


@dataclass(frozen=True)
class PreDealInput:
    counts: tuple[int, ...]
    physical_remaining: int
    rules_json: str
    information_json: str
    session_id: str
    shoe_id: str
    round_id: str
    through_seq: int
    prefix_digest: str
    n_decks: int | None = None
    schema: str = PREDEAL_INPUT_SCHEMA
    window: str = WINDOW_PRE_DEAL
    engine_version: str = PREDEAL_ENGINE_VERSION
    strategy_version: str = PREDEAL_STRATEGY_VERSION
    support_scope: str = PREDEAL_SUPPORT
    deal_order: str = DEAL_ORDER
    max_remaining: int = PREDEAL_MAX_REMAINING
    surrender: str | None = None
    splits: bool = False
    insurance: bool = False

    @property
    def input_digest(self):
        return digest(asdict(self))

    @property
    def rules_digest(self):
        import json
        return digest(json.loads(self.rules_json))

    def to_dict(self):
        return asdict(self)

    def validate(self):
        import json
        from ..core.rules import RuleProfile, CONFIRM_VERIFIED
        if self.schema != PREDEAL_INPUT_SCHEMA or self.window != WINDOW_PRE_DEAL:
            raise ValueError("发牌前输入版本不匹配")
        if self.engine_version != PREDEAL_ENGINE_VERSION or self.strategy_version != PREDEAL_STRATEGY_VERSION:
            raise ValueError("发牌前引擎/策略版本不匹配")
        if self.deal_order != DEAL_ORDER or self.splits or self.insurance:
            raise ValueError("发牌前策略范围不匹配：无分牌、无保险")
        if len(self.counts) != 10 or any(type(n) is not int or n < 0 for n in self.counts):
            raise ValueError("发牌前组成必须是十个非负整数点值桶")
        if type(self.physical_remaining) is not int or self.physical_remaining != sum(self.counts):
            raise ValueError("发牌前物理剩余必须等于十桶合计；底牌尚未发出")
        if self.physical_remaining < 4:
            raise ValueError("剩余牌不足下一轮初始四张")
        if self.physical_remaining > self.max_remaining or self.max_remaining != PREDEAL_MAX_REMAINING:
            raise ValueError(f"本版只精确穷举剩余不超过{PREDEAL_MAX_REMAINING}张的牌靴")
        if self.n_decks is not None and self.n_decks not in (6, 7, 8):
            raise ValueError("若声明原副数，只接受6/7/8")
        rules = RuleProfile.from_json(self.rules_json)
        if not (rules.confirm_status == CONFIRM_VERIFIED
                and rules.dealer_soft17 == "S17" and rules.blackjack_payout == (3, 2)
                and rules.american_hole_card is True and rules.check_bj_when == "before_player_actions_A_T"
                and rules.dealer_bj_extra_bet_rule == "all_bets_lost"
                and rules.double_on_totals is None and rules.surrender in (None, "late")
                and rules.shoe_model == "finite_no_replacement"):
            raise ValueError("发牌前分析仅验收S17、3:2、美式决策前检查、任意两张加倍、无投降/晚投降")
        if self.surrender not in (None, "late"):
            raise ValueError("发牌前仅验收无投降或晚投降")
        if rules.surrender != self.surrender:
            raise ValueError("发牌前投降规则必须与规则档案一致，并传到求解器")
        if self.support_scope != predeal_support_scope(self.surrender):
            raise ValueError("发牌前支持范围必须与投降规则一致")
        info = json.loads(self.information_json)
        if info.get("gap") is not False or info.get("pending_candidates") != 0:
            raise ValueError("存在观察缺口或待核对牌，不能做发牌前精确分析")
        if info.get("unrevealed_out") != 0 or info.get("burn_unknown") != 0:
            raise ValueError("仍有未揭示牌或未知烧牌，剩余组成不是精确已知")
        t_out = info.get("t_bucket_out", 0)
        if type(t_out) is not int or t_out < 0:
            raise ValueError("十点未细分张数必须是非负整数")
        if t_out and self.splits:
            raise ValueError("十点未细分不能用于同牌级分牌组成")
        if type(self.through_seq) is not int or self.through_seq < 0:
            raise ValueError("发牌前时点序号无效")
        if len(self.prefix_digest) != 64 or any(c not in "0123456789abcdef" for c in self.prefix_digest):
            raise ValueError("发牌前输入摘要无效")
        for name in (self.session_id, self.shoe_id, self.round_id):
            if not isinstance(name, str) or not name:
                raise ValueError("发牌前输入缺少身份")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if "surrender" not in data:
            raise ValueError("发牌前输入必须显式给出投降规则，不能默认晚投降")
        data["counts"] = tuple(data["counts"])
        return cls(**data)
