"""Experiment configuration identity. Not a second EV engine."""
from dataclasses import asdict, dataclass, field

SCHEMA = "hakimi-experiment-v1"
CONFIG_VERSION = 1
KIND_SYNTHETIC = "synthetic-scenario"
KIND_HISTORY = "history-prefix-replay"
REMOVAL_FIXED = "fixed_known_ranks"
REMOVAL_NONE = "none"
TEMPLATES = ("single", "split", "das")
SUPPORTED_DECKS = (6, 7, 8)

RANK_INDEX = {
    "A": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5, "7": 6, "8": 7, "9": 8,
    "10": 9, "J": 9, "Q": 9, "K": 9, "T": 9,
}


class ExperimentError(ValueError):
    def __init__(self, code, reason):
        self.code = code
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_id: str
    kind: str
    n_decks: tuple[int, ...]
    template: str
    player_ranks: tuple[str, ...]
    dealer_up: str
    extra_removed: tuple[str, ...] = ()
    peek_negative: bool | None = None
    seat: str = "玩家1"
    session_id: str | None = None
    through_seq: int | None = None
    db_path: str | None = None
    note: str = ""
    schema: str = SCHEMA
    config_version: int = CONFIG_VERSION
    removal_kind: str = REMOVAL_FIXED
    not_a_round_simulation: bool = True

    def to_dict(self):
        return asdict(self)


def parse_ranks(text):
    if text is None:
        return ()
    if isinstance(text, (list, tuple)):
        parts = [str(item).strip().upper() for item in text]
    else:
        parts = [item.strip().upper() for item in str(text).replace("，", ",").replace(" ", ",").split(",") if item.strip()]
    ranks = []
    for item in parts:
        if item in ("10", "T", "J", "Q", "K", "A") or (len(item) == 1 and item in "23456789"):
            ranks.append("T" if item == "T" else item)
        else:
            raise ExperimentError("ILLEGAL_RANK", f"不能识别的牌面: {item}")
    return tuple(ranks)
