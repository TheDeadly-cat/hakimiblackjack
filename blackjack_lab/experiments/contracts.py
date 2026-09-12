"""Experiment configuration identity. Not a second EV engine."""
from dataclasses import asdict, dataclass

from ..core.cards import TEN_BUCKET
from ..core.shoe import ConsistencyError, ShoeState

SCHEMA = "hakimi-experiment-v1"
CONFIG_VERSION = 1
KIND_SYNTHETIC = "synthetic-scenario"
KIND_HISTORY = "history-prefix-replay"
REMOVAL_FIXED = "fixed_known_ranks"
REMOVAL_NONE = "none"
TEMPLATES = ("single", "split", "das")
SUPPORTED_DECKS = (6, 7, 8)
TRUE_TEXTS = {"true", "1", "yes", "y", "on", "是"}
FALSE_TEXTS = {"false", "0", "no", "n", "off", "否"}

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


def parse_optional_bool(value, field="peek_negative"):
    if value is None or value == "":
        return None
    if type(value) is bool:
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in TRUE_TEXTS:
            return True
        if text in FALSE_TEXTS:
            return False
    raise ExperimentError("ILLEGAL_TYPE", f"{field} 必须是 true 或 false，不能把 {value!r} 悄悄改成研究条件")


def parse_strict_int(value, field):
    if type(value) is bool or type(value) is float:
        raise ExperimentError("ILLEGAL_TYPE", f"{field} 必须是整数，不能把 {value!r} 悄悄改成研究条件")
    if type(value) is int:
        return value
    if isinstance(value, str):
        text = value.strip()
        sign, digits = "", text
        if text.startswith("-") or text.startswith("+"):
            sign, digits = text[0], text[1:]
        if digits.isdigit() and digits:
            return int(digits) if sign == "+" else int(sign + digits)
    raise ExperimentError("ILLEGAL_TYPE", f"{field} 必须是整数，不能把 {value!r} 悄悄改成研究条件")


def parse_decks(decks):
    if decks is None or decks == "":
        raise ExperimentError("ILLEGAL_DECKS", "副数只支持 6、7、8")
    if type(decks) is int:
        items = (parse_strict_int(decks, "副数"),)
    elif isinstance(decks, str):
        parts = [item.strip() for item in decks.replace("，", ",").split(",") if item.strip()]
        items = tuple(parse_strict_int(item, "副数") for item in parts)
    elif isinstance(decks, (list, tuple)):
        items = tuple(parse_strict_int(item, "副数") for item in decks)
    else:
        raise ExperimentError("ILLEGAL_TYPE", f"副数类型无效: {type(decks).__name__}")
    if not items or any(item not in SUPPORTED_DECKS for item in items):
        raise ExperimentError("ILLEGAL_DECKS", "副数只支持 6、7、8")
    return items


def assert_original_composition(n_decks, ranks):
    """Known original faces must fit 4×decks. T consumes leftover 10/J/Q/K, not a fifth ten rank."""
    try:
        shoe = ShoeState(n_decks)
        for rank in ranks:
            shoe.remove_known(TEN_BUCKET if rank == "T" else rank)
    except (ConsistencyError, ValueError) as error:
        raise ExperimentError("ILLEGAL_COMPOSITION", str(error)) from error
