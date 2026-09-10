"""Immutable, content-addressed visible input. No simulator state or future events."""
from dataclasses import asdict, dataclass
import hashlib
import json

ENGINE_VERSION = "v0.2a-finite-single-hand-1"
STRATEGY_VERSION = "after-hit-visible-composition-optimal-hit-stand-v1"
INPUT_SCHEMA = "hakimi-analysis-input-v1"
RESULT_SCHEMA = "hakimi-analysis-result-v1"
AVAILABLE = "available"
INAPPLICABLE = "inapplicable"
UNSUPPORTED = "unsupported"
PENDING = "pending"
COMPUTING = "computing"
TIMEOUT = "timeout"
CANCELLED = "cancelled"
STALE = "stale"
FAILED = "failed"
STATUS_ZH = {AVAILABLE: "可用", INAPPLICABLE: "不适用", UNSUPPORTED: "未支持",
             PENDING: "待核对", COMPUTING: "计算中", TIMEOUT: "超时",
             CANCELLED: "已取消", STALE: "过期", FAILED: "失败"}
ACTION_ZH = {"stand": "停牌", "hit": "补牌", "double": "加倍", "split": "分牌", "surrender": "投降"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AnalysisInput:
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
    counts: tuple[int, ...]
    physical_remaining: int
    player: tuple[int, ...]
    player_ranks: tuple[str, ...]
    dealer_up: int
    peek_negative: bool
    legal_actions: tuple[str, ...]
    uncertain_actions: tuple[str, ...] = ()
    schema: str = INPUT_SCHEMA
    engine_version: str = ENGINE_VERSION
    strategy_version: str = STRATEGY_VERSION
    support_scope: str = "S17/3:2/US-peek/zero-burn/single-player/unsplit"

    @property
    def input_digest(self):
        return digest(asdict(self))

    @property
    def rules_digest(self):
        return digest(json.loads(self.rules_json))

    def to_dict(self):
        return asdict(self)

    def validate(self):
        from ..core.rules import RuleProfile, CONFIRM_VERIFIED
        if self.schema != INPUT_SCHEMA or type(self.n_decks) is not int or self.n_decks not in (6, 7, 8):
            raise ValueError("分析输入版本或副数不支持")
        if type(self.through_seq) is not int or self.through_seq < 1:
            raise ValueError("分析事件前缀序号无效")
        if any(not isinstance(v, str) or not v for v in (self.session_id, self.shoe_id, self.round_id, self.seat, self.hand_id)):
            raise ValueError("分析输入缺少目标身份")
        if len(self.prefix_digest) != 64 or any(c not in "0123456789abcdef" for c in self.prefix_digest):
            raise ValueError("分析前缀摘要无效")
        if any(type(v) is not tuple for v in (self.counts, self.player, self.player_ranks, self.legal_actions, self.uncertain_actions)):
            raise ValueError("分析输入集合必须不可变")
        rules = RuleProfile.from_json(self.rules_json)
        if not (rules.n_decks == self.n_decks and rules.confirm_status == CONFIRM_VERIFIED
                and rules.start_from_new_shoe is True and rules.burn_cards_known is True
                and rules.initial_burn_count == 0 and rules.shoe_model == "finite_no_replacement"
                and rules.dealer_soft17 == "S17" and rules.blackjack_payout == (3, 2)
                and rules.american_hole_card is True and rules.check_bj_when == "before_player_actions_A_T"
                and rules.dealer_bj_extra_bet_rule == "all_bets_lost" and rules.double_on_totals is None
                and rules.surrender in (None, "late")):
            raise ValueError("分析输入包含未知或未支持的规则，不执行S17模板替代计算")
        if (len(self.counts) != 10 or any(type(c) is not int or c < 0 for c in self.counts)
                or type(self.physical_remaining) is not int or self.physical_remaining != sum(self.counts) - 1
                or any(c > self.n_decks * (16 if i == 9 else 4) for i, c in enumerate(self.counts))):
            raise ValueError("分析输入计数或隐藏牌模型不一致")
        if type(self.peek_negative) is not bool or self.dealer_up in (1, 10) and not self.peek_negative:
            raise ValueError("决策所需的非BJ检查信息缺失")
        values = {"A": 1, **{str(n): n for n in range(2, 11)}, "J": 10, "Q": 10, "K": 10, "T": 10}
        if tuple(values.get(r) for r in self.player_ranks) != self.player:
            raise ValueError("原始牌面与点值不一致")
        info = json.loads(self.information_json)
        if (info.get("gap") is not False or info.get("pending_candidates") != 0
                or info.get("burn_unknown") != 0 or info.get("unrevealed_out") != 1):
            raise ValueError("未满足分析所需的可见信息条件")
        for action in self.legal_actions:
            if action not in ACTION_ZH or info.get("action_states", {}).get(ACTION_ZH[action], {}).get("allowed") is not True:
                raise ValueError("合法动作与信息快照不一致")
        if len(set(self.legal_actions)) != len(self.legal_actions):
            raise ValueError("合法动作重复")

    @classmethod
    def from_dict(cls, data):
        data = dict(data)
        for name in ("counts", "player", "player_ranks", "legal_actions", "uncertain_actions"):
            data[name] = tuple(data[name])
        return cls(**data)


class InputUnavailable(ValueError):
    def __init__(self, code, reason, status=PENDING):
        self.code, self.reason, self.status = code, reason, status
        super().__init__(reason)


def research_rules(n_decks=6, surrender="late"):
    """Explicit user-selected synthetic template; never an automatic unknown-rule fallback."""
    from ..core.rules import RuleProfile, CONFIRM_VERIFIED
    return RuleProfile(profile_id="research-s17-us-peek-v1", version=1,
        n_decks=n_decks, confirm_status=CONFIRM_VERIFIED,
        vendor="自建研究模板", game_name="单玩家未分牌分析", rule_source="用户主动选择的自建研究桌规，不代表平台规则",
        dealer_soft17="S17", blackjack_payout=(3, 2), american_hole_card=True,
        check_bj_when="before_player_actions_A_T", dealer_bj_extra_bet_rule="all_bets_lost",
        double_after_split=False, double_on_totals=None, split_match="same_rank",
        max_split_hands=4, resplit_aces=False, split_ace_hit_once=True,
        surrender=surrender, n_seats=7, burn_cards_known=True, initial_burn_count=0, start_from_new_shoe=True,
        remark="零烧牌研究模板；V0.2a不计算分牌、多玩家及下一轮开局优势")
