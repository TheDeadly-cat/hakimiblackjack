"""Build analysis snapshots from synthetic research conditions or a frozen history prefix."""
from dataclasses import replace
from pathlib import Path

from ..analysis.contracts import InputUnavailable, research_rules
from ..analysis.information import build_input
from ..analysis.split_contracts import das_research_rules, split_research_rules
from ..core.cards import UNKNOWN
from ..ledger.ledger import EventLedger
from ..storage.database import LocalStore
from .contracts import (
    KIND_HISTORY, KIND_SYNTHETIC, RANK_INDEX, REMOVAL_FIXED, REMOVAL_NONE, TEMPLATES,
    ExperimentConfig, ExperimentError, assert_original_composition, parse_decks,
    parse_optional_bool, parse_ranks, parse_strict_int,
)


def template_rules(template, n_decks):
    if template == "single":
        return research_rules(n_decks)
    if template == "split":
        return split_research_rules(n_decks)
    if template == "das":
        return das_research_rules(n_decks)
    raise ExperimentError("ILLEGAL_TEMPLATE", f"模板必须是 {TEMPLATES} 之一")


def config_from_mapping(data, experiment_id):
    kind = data.get("kind")
    if kind not in (KIND_SYNTHETIC, KIND_HISTORY):
        raise ExperimentError("ILLEGAL_KIND", "输入类型必须是合成场景或历史前缀回放")
    n_decks = parse_decks(data.get("n_decks") if data.get("n_decks") not in (None, "") else data.get("decks"))
    template = data.get("template", "single")
    extra = parse_ranks(data.get("extra_removed") or data.get("removed") or ())
    player = parse_ranks(data.get("player_ranks") or data.get("player") or ())
    dealer = str(data.get("dealer_up") or data.get("up") or "").strip().upper()
    if dealer == "T":
        dealer = "T"
    peek = parse_optional_bool(data.get("peek_negative"), "peek_negative")
    removal = REMOVAL_NONE if not extra else REMOVAL_FIXED
    through_seq = data.get("through_seq")
    if through_seq is not None and through_seq != "":
        through_seq = parse_strict_int(through_seq, "事件序号")
    else:
        through_seq = None
    return ExperimentConfig(
        experiment_id=experiment_id,
        kind=kind,
        n_decks=n_decks,
        template=template,
        player_ranks=player,
        dealer_up=dealer,
        extra_removed=extra,
        peek_negative=peek,
        seat=data.get("seat") or "玩家1",
        session_id=data.get("session_id"),
        through_seq=through_seq,
        db_path=data.get("db_path"),
        note=data.get("note") or "",
        removal_kind=removal,
        not_a_round_simulation=True,
    )


def known_original_ranks(config):
    dealer = (config.dealer_up,) if config.dealer_up else ()
    return tuple(config.player_ranks) + dealer + tuple(config.extra_removed)


def _live_ledger(n_decks, config):
    if len(config.player_ranks) < 2:
        raise ExperimentError("ILLEGAL_HAND", "合成场景需要至少两张玩家牌")
    if config.dealer_up not in RANK_INDEX and config.dealer_up != "T":
        raise ExperimentError("ILLEGAL_UP", "庄家明牌无效")
    if UNKNOWN in config.player_ranks or UNKNOWN == config.dealer_up:
        raise ExperimentError("ILLEGAL_RANK", "合成场景不能用未知牌面代替组成")
    ledger = EventLedger(f"{config.experiment_id}-{n_decks}d")
    ledger.start_session("合成对照实验；固定已知组成，不是真实轮次模拟")
    ledger.create_shoe(template_rules(config.template, n_decks))
    ledger.start_round([config.seat])
    ledger.deal("庄家", config.dealer_up, source="合成实验")
    ledger.deal("庄家", hidden=True, source="合成实验")
    peeked = False
    ten_up = config.dealer_up in ("A", "10", "J", "Q", "K", "T")
    peek = True if config.peek_negative is None else config.peek_negative
    for index, card in enumerate(config.player_ranks):
        if index >= 2 and peek and ten_up and not peeked:
            ledger.peek_negative()
            peeked = True
        ledger.deal(config.seat, card, source="合成实验")
    if peek and ten_up and not peeked:
        ledger.peek_negative()
    return ledger


def apply_fixed_removals(snapshot, extra_ranks, known_ranks=()):
    assert_original_composition(snapshot.n_decks, tuple(known_ranks) if known_ranks else tuple(extra_ranks))
    if not extra_ranks:
        return snapshot
    counts = list(snapshot.counts)
    for rank in extra_ranks:
        if rank not in RANK_INDEX:
            raise ExperimentError("ILLEGAL_RANK", f"额外移除牌面无效: {rank}")
        index = RANK_INDEX[rank]
        if counts[index] <= 0:
            raise ExperimentError("ILLEGAL_COMPOSITION", f"额外移除 {rank} 超出当前剩余组成")
        counts[index] -= 1
    counts = tuple(counts)
    physical = sum(counts) - 1
    if physical < 0:
        raise ExperimentError("ILLEGAL_COMPOSITION", "额外移除后没有足够的牌")
    updated = replace(snapshot, counts=counts, physical_remaining=physical)
    updated.validate()
    return updated


def snapshot_for_deck(config, n_decks):
    assert_original_composition(n_decks, known_original_ranks(config))
    ledger = _live_ledger(n_decks, config)
    try:
        snapshot = build_input(ledger, config.seat)
    except InputUnavailable as error:
        raise ExperimentError(error.code, error.reason) from error
    try:
        return apply_fixed_removals(snapshot, config.extra_removed, known_original_ranks(config)), ledger
    except ExperimentError:
        raise
    except ValueError as error:
        raise ExperimentError("ILLEGAL_COMPOSITION", str(error)) from error


def snapshot_from_history(config):
    if not config.db_path or not config.session_id or type(config.through_seq) is not int:
        raise ExperimentError("HISTORY_IDENTITY", "历史回放必须提供数据库、会话和事件序号")
    path = Path(config.db_path)
    if not path.is_file():
        raise ExperimentError("HISTORY_MISSING", "历史数据库不存在")
    store = LocalStore(path)
    try:
        full = store.load_events(config.session_id)
        if not any(event.seq == config.through_seq for event in full):
            raise ExperimentError("PREFIX_MISSING", "所选历史时点不存在")
        later = [event for event in full if event.seq > config.through_seq]
        ledger = store.load_ledger(config.session_id, config.through_seq)
    finally:
        store.close()
    try:
        snapshot = build_input(ledger, config.seat, through_seq=config.through_seq)
    except InputUnavailable as error:
        raise ExperimentError(error.code, error.reason) from error
    return snapshot, ledger, later
