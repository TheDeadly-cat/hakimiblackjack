"""Ledger-frozen offline Monte Carlo input. Not the 16-card exact pre-deal entry."""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .contracts import digest
from .research_windows import WINDOW_PRE_DEAL

OFFLINE_MC_INPUT_SCHEMA = "hakimi-offline-mc-input-v1"
OFFLINE_MC_RESULT_SCHEMA = "hakimi-offline-mc-result-v1"
OFFLINE_MC_ENGINE_VERSION = "v0.3f-offline-fixed-policy-mc-1"
OFFLINE_MC_MAX_BUDGET_SECONDS = 120.0


def pack_from_counts(counts):
    from .research_windows import require_count_vector
    counts = require_count_vector(counts)
    pack = []
    for value, n in enumerate(counts, start=1):
        pack.extend([value] * n)
    return tuple(pack)


@dataclass(frozen=True)
class OfflineMcInput:
    counts: tuple[int, ...]
    pack: tuple[int, ...]
    physical_remaining: int
    rules_json: str
    information_json: str
    session_id: str
    shoe_id: str
    round_id: str
    through_seq: int
    prefix_digest: str
    n_decks: int
    surrender: str | None
    policy_id: str
    n_samples: int
    seed: int
    family_size: int = 1
    alpha: float = 0.05
    play_budget_seconds: float = 2.0
    schema: str = OFFLINE_MC_INPUT_SCHEMA
    window: str = WINDOW_PRE_DEAL
    engine_version: str = OFFLINE_MC_ENGINE_VERSION
    strategy_version: str = ""
    support_scope: str = "S17/3:2/US-peek/unsplit/no-insurance/offline-fixed-policy-mc"
    splits: bool = False
    insurance: bool = False

    def __post_init__(self):
        if not self.strategy_version:
            object.__setattr__(self, "strategy_version", self.policy_id)

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
        from .fixed_policy_mc import SUPPORTED_POLICIES
        from .research_windows import require_count_vector, require_point_values
        if self.schema != OFFLINE_MC_INPUT_SCHEMA or self.window != WINDOW_PRE_DEAL:
            raise ValueError("离线MC输入版本不匹配")
        if self.engine_version != OFFLINE_MC_ENGINE_VERSION:
            raise ValueError("离线MC引擎版本不匹配")
        if self.splits or self.insurance:
            raise ValueError("离线MC当前范围：无分牌、无保险")
        counts = require_count_vector(self.counts)
        pack = require_point_values(self.pack, what="离线MC剩余")
        if pack != pack_from_counts(counts):
            raise ValueError("离线MC牌列必须由十桶精确展开，不能平均未知牌")
        if type(self.physical_remaining) is not int or self.physical_remaining != len(pack):
            raise ValueError("离线MC物理剩余必须等于已知组成张数")
        if self.physical_remaining < 4:
            raise ValueError("剩余牌不足下一轮初始四张")
        if self.n_decks not in (6, 7, 8):
            raise ValueError("离线MC只接受6/7/8副已确认牌靴")
        if self.policy_id not in SUPPORTED_POLICIES:
            raise ValueError("离线MC必须使用已声明冻结策略")
        if self.strategy_version != self.policy_id:
            raise ValueError("离线MC策略版本必须等于冻结策略")
        if type(self.n_samples) is not int or self.n_samples < 1:
            raise ValueError("离线MC样本数必须是正整数")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("离线MC种子必须是非负整数")
        if type(self.family_size) is not int or self.family_size < 1:
            raise ValueError("family_size必须是正整数")
        rules = RuleProfile.from_json(self.rules_json)
        if not (rules.confirm_status == CONFIRM_VERIFIED
                and rules.dealer_soft17 == "S17" and rules.blackjack_payout == (3, 2)
                and rules.american_hole_card is True
                and rules.check_bj_when == "before_player_actions_A_T"
                and rules.dealer_bj_extra_bet_rule == "all_bets_lost"
                and rules.double_on_totals is None and rules.surrender in (None, "late")
                and rules.shoe_model == "finite_no_replacement"):
            raise ValueError("离线MC仅验收S17、3:2、美式决策前检查、任意两张加倍、无投降/晚投降")
        if self.surrender not in (None, "late") or rules.surrender != self.surrender:
            raise ValueError("离线MC投降规则必须与规则档案一致")
        info = json.loads(self.information_json)
        if info.get("remaining_is_complete") is not True:
            raise ValueError("未知组成不能当作精确组成做离线MC")
        if info.get("gap") is not False or info.get("pending_candidates") != 0:
            raise ValueError("存在观察缺口或待核对牌，不能做离线组成MC")
        if info.get("unrevealed_out") != 0 or info.get("burn_unknown") != 0:
            raise ValueError("仍有未揭示牌或未知烧牌，剩余组成不是精确已知")
        if type(self.through_seq) is not int or self.through_seq < 0:
            raise ValueError("离线MC时点序号无效")
        if len(self.prefix_digest) != 64 or any(c not in "0123456789abcdef" for c in self.prefix_digest):
            raise ValueError("离线MC前缀摘要无效")
        for name in (self.session_id, self.shoe_id, self.round_id):
            if not isinstance(name, str) or not name:
                raise ValueError("离线MC输入缺少身份")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        if "surrender" not in data:
            raise ValueError("离线MC输入必须显式给出投降规则，不能默认晚投降")
        data["counts"] = tuple(data["counts"])
        data["pack"] = tuple(data["pack"])
        return cls(**data)
