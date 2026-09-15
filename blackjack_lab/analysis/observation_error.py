"""Synthetic recording-error contrast for pre-deal windows.

Snapshot remaining-pack edits (rank swap, leftover miss/dup) stay diagnostic.
Event-stream errors rebuild remaining from corrupted face-up deals after a round.
The same miss/dup/misread kinds can be injected into a real EventLedger and
replayed; that remaining contrast is not an exact six-deck window claim.
Independent unused video is still required before any live-table window claim.
"""
from __future__ import annotations

import copy
from random import Random

from ..core.cards import UNKNOWN
from ..core.shoe import ConsistencyError
from ..core.table import TableError
from ..ledger.events import CARD_DEALT, FACE_SHOWN, new_event_id
from ..ledger.ledger import EventLedger, LedgerError
from .contracts import InputUnavailable
from .predeal_contracts import PREDEAL_MAX_REMAINING, SURRENDER_UNSET, require_declared_surrender
from .probability import CalculationStopped, InsufficientCards
from .research_windows import (
    WINDOW_PRE_DEAL, build_predeal_input, confusion_matrix, evaluation_scope,
)
from .shoe_windows import CONSUMPTION_PI, evaluate_predeal, play_round, remaining_after_events

SCHEMA = "hakimi-observation-error-study-v1"


def misread_one_rank(pack, rng):
    """Swap one remaining 10<->9. Persistent only for this snapshot, not a live model."""
    pack = list(pack)
    if not pack:
        return pack, {"kind": "none"}
    index = rng.randrange(len(pack))
    previous = pack[index]
    pack[index] = 9 if previous == 10 else 10
    return pack, {"kind": "rank_swap", "index": index, "from": previous, "to": pack[index]}


def missed_remaining_card(pack, rng):
    """Observer remaining silently drops one card. Truth is unchanged."""
    observer = list(pack)
    if not observer:
        return observer, {"kind": "none"}
    index = rng.randrange(len(observer))
    removed = observer.pop(index)
    return observer, {"kind": "missed_remaining_card", "index": index, "removed": removed}


def duplicate_remaining_card(pack, rng):
    """Observer remaining duplicates one card. Truth is unchanged."""
    observer = list(pack)
    if not observer:
        return observer, {"kind": "none"}
    index = rng.randrange(len(observer))
    observer.append(observer[index])
    return observer, {"kind": "duplicate_remaining_card", "index": index, "copied": observer[index]}


def unknown_removal_observer(pack, rng):
    """A card left the true shoe; observer still counts it (unknown removal)."""
    actual = list(pack)
    if len(actual) < 5:
        return actual, list(pack), {"kind": "none"}
    index = rng.randrange(len(actual))
    removed = actual.pop(index)
    observer = list(actual) + [removed]
    return actual, observer, {"kind": "unknown_removal", "index": index, "removed": removed}


EVENT_ERROR_KINDS = ("miss_public_deal", "duplicate_public_deal", "misread_public_deal")


def apply_public_event_error(pack, public_dealt, rng, kind):
    """Corrupt face-up deal events, then rebuild remaining. Not a remaining-pack edit."""
    if kind not in EVENT_ERROR_KINDS:
        raise ValueError("公开事件误差只接受漏记、重复或错认")
    dealt = list(public_dealt)
    if not dealt:
        return remaining_after_events(pack, []), {"kind": "none"}
    if kind == "miss_public_deal":
        index = rng.randrange(len(dealt))
        missed = dealt.pop(index)
        note = {"kind": kind, "index": index, "missed": missed}
    elif kind == "duplicate_public_deal":
        index = rng.randrange(len(dealt))
        copied = dealt[index]
        dealt.append(copied)
        note = {"kind": kind, "index": index, "copied": copied}
    else:
        index = rng.randrange(len(dealt))
        previous = dealt[index]
        dealt[index] = 9 if previous == 10 else 10
        note = {"kind": kind, "index": index, "from": previous, "to": dealt[index]}
    remaining = remaining_after_events(pack, dealt)
    if remaining is None:
        note["inconsistent"] = True
    return remaining, note


LEDGER_EVENT_SCHEMA = "hakimi-ledger-event-error-v1"


def shown_public_deal_indexes(events):
    """Face-up confirmed ranks only. Hidden/unknown hole is not a public deal."""
    indexes = []
    for index, event in enumerate(events):
        if not isinstance(event, dict) or event.get("etype") != CARD_DEALT:
            continue
        payload = event.get("payload") or {}
        if payload.get("face_state") != FACE_SHOWN:
            continue
        rank = payload.get("rank")
        if rank in (None, "", UNKNOWN):
            continue
        indexes.append(index)
    return indexes


def _importable_events(events):
    out = []
    for event in events:
        item = copy.deepcopy(event)
        payload = item.get("payload")
        if isinstance(payload, dict):
            item["payload"] = {
                key: value for key, value in payload.items() if not str(key).startswith("_")
            }
        out.append(item)
    return out


def _resequence_events(events):
    out = _importable_events(events)
    for seq, item in enumerate(out, start=1):
        item["seq"] = seq
    return out


def _shoe_knowledge(shoe):
    return {
        "remaining": dict(shoe.remaining),
        "unrevealed_out": shoe.unrevealed_out,
        "physical_remaining": shoe.physical_remaining(),
    }


def _replay_observer(session_id, events, *, rule_version="v0.1"):
    observer = EventLedger.from_list(session_id, _importable_events(events),
                                     rule_version=rule_version)
    shoe = observer.replay().current.shoe
    return observer, _shoe_knowledge(shoe)


def ledger_predeal_view(ledger):
    """Exact next-round entry from a ledger. Six-deck leftover stays unavailable."""
    try:
        built = build_predeal_input(ledger)
    except InputUnavailable as err:
        return {
            "status": err.status,
            "reason_code": err.code,
            "ev": None,
            "window": WINDOW_PRE_DEAL,
            "exact_predeal_not_claimed": True,
        }
    return {
        "status": "unsupported" if built.physical_remaining > PREDEAL_MAX_REMAINING else "built",
        "reason_code": None,
        "ev": None,
        "window": WINDOW_PRE_DEAL,
        "physical_remaining": built.physical_remaining,
        "exact_predeal_not_claimed": True,
    }


def _window_contrast(truth_ledger, observer):
    truth_view = ledger_predeal_view(truth_ledger)
    if observer is None:
        observer_view = {
            "status": "inapplicable",
            "reason_code": "OBSERVER_INCONSISTENT",
            "ev": None,
            "window": WINDOW_PRE_DEAL,
            "exact_predeal_not_claimed": True,
        }
    else:
        observer_view = ledger_predeal_view(observer)
    scope = evaluation_scope([truth_view, observer_view])
    return {
        "truth_predeal": truth_view,
        "observer_predeal": observer_view,
        "zero_window": scope["zero_window"],
        "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
        "window_reason_codes_match": truth_view.get("reason_code") == observer_view.get("reason_code"),
    }


def apply_ledger_event_error(events, *, kind, shown_index=None, rng=None):
    """Corrupt shown CARD_DEALT events on a copied list. Hidden deals stay put."""
    if kind not in EVENT_ERROR_KINDS:
        raise ValueError("公开事件误差只接受漏记、重复或错认")
    events = [copy.deepcopy(event) for event in events]
    shown = shown_public_deal_indexes(events)
    if not shown:
        return events, {"kind": "none"}
    if shown_index is None:
        if rng is None:
            raise ValueError("账本公开事件误差需要 shown_index 或 rng")
        shown_index = rng.randrange(len(shown))
    if type(shown_index) is not int or shown_index < 0 or shown_index >= len(shown):
        raise ValueError("shown_index 越界")
    pick = shown[shown_index]
    payload = dict(events[pick].get("payload") or {})
    if kind == "miss_public_deal":
        missed = events.pop(pick)
        note = {
            "kind": kind,
            "shown_index": shown_index,
            "event_id": missed.get("event_id"),
            "missed": payload.get("rank"),
        }
    elif kind == "duplicate_public_deal":
        extra = copy.deepcopy(events[pick])
        extra["event_id"] = new_event_id()
        events.insert(pick + 1, extra)
        note = {
            "kind": kind,
            "shown_index": shown_index,
            "copied": payload.get("rank"),
            "copied_event_id": extra["event_id"],
        }
    else:
        previous = payload.get("rank")
        payload["rank"] = "9" if previous in ("10", "J", "Q", "K", "T") else "10"
        events[pick] = dict(events[pick])
        events[pick]["payload"] = payload
        note = {
            "kind": kind,
            "shown_index": shown_index,
            "from": previous,
            "to": payload["rank"],
            "event_id": events[pick].get("event_id"),
        }
    return _resequence_events(events), note


def compare_ledger_event_error(truth_ledger, *, kind, shown_index=None, rng=None):
    """Replay a corrupted EventLedger and compare remaining ranks, not exact EV."""
    if not isinstance(truth_ledger, EventLedger):
        raise TypeError("对照需要真实 EventLedger，不是剩余列表")
    truth_events = truth_ledger.to_list()
    observer_events, note = apply_ledger_event_error(
        truth_events, kind=kind, shown_index=shown_index, rng=rng)
    truth = _shoe_knowledge(truth_ledger.replay().current.shoe)
    observer = None
    observer_knowledge = None
    replay_error = None
    try:
        observer, observer_knowledge = _replay_observer(
            truth_ledger.session_id, observer_events,
            rule_version=truth_ledger.rule_version)
    except (LedgerError, TableError, ConsistencyError, ValueError) as err:
        replay_error = f"{type(err).__name__}: {err}"
    window = _window_contrast(truth_ledger, observer)
    return {
        "schema": LEDGER_EVENT_SCHEMA,
        "kind": note.get("kind"),
        "note": note,
        "truth_remaining": truth["remaining"],
        "observer_remaining": None if observer_knowledge is None else observer_knowledge["remaining"],
        "truth_unrevealed_out": truth["unrevealed_out"],
        "observer_unrevealed_out": (
            None if observer_knowledge is None else observer_knowledge["unrevealed_out"]),
        "truth_physical_remaining": truth["physical_remaining"],
        "observer_physical_remaining": (
            None if observer_knowledge is None else observer_knowledge["physical_remaining"]),
        "remaining_match": (
            observer_knowledge is not None
            and observer_knowledge["remaining"] == truth["remaining"]),
        "knowledge_match": observer_knowledge == truth,
        "replay_error": replay_error,
        "accepted": False,
        "independent_video": False,
        "not_a_reliable_window_claim": True,
        "exact_predeal_not_claimed": True,
        "source": "ledger-event-stream",
        "snapshot_remaining_is_not_event_stream": False,
        **window,
    }


def apply_ledger_delay(events, *, lag_events):
    """Observer ledger is a confirmed prefix. Hidden cards may still match remaining ranks."""
    if type(lag_events) is not int or lag_events < 0:
        raise ValueError("事件延迟必须是非负整数")
    events = _importable_events(events)
    if lag_events == 0:
        return events, {"kind": "zero_lag", "lag_events": 0}
    if lag_events > len(events):
        raise ValueError("延迟不能超过事件数")
    dropped = events[-lag_events:]
    kept = events[:-lag_events]
    return kept, {
        "kind": "event_lag",
        "lag_events": lag_events,
        "dropped_event_ids": [item.get("event_id") for item in dropped],
        "dropped_etypes": [item.get("etype") for item in dropped],
    }


def compare_ledger_delay(truth_ledger, *, lag_events):
    """Lagged prefix vs truth. Zero lag must match; both-unavailable is not a zero window."""
    if not isinstance(truth_ledger, EventLedger):
        raise TypeError("对照需要真实 EventLedger，不是剩余列表")
    observer_events, note = apply_ledger_delay(truth_ledger.to_list(), lag_events=lag_events)
    truth = _shoe_knowledge(truth_ledger.replay().current.shoe)
    observer = None
    observer_knowledge = None
    replay_error = None
    try:
        observer, observer_knowledge = _replay_observer(
            truth_ledger.session_id, observer_events,
            rule_version=truth_ledger.rule_version)
    except (LedgerError, TableError, ConsistencyError, ValueError) as err:
        replay_error = f"{type(err).__name__}: {err}"
    window = _window_contrast(truth_ledger, observer)
    return {
        "schema": LEDGER_EVENT_SCHEMA,
        "kind": note.get("kind"),
        "note": note,
        "truth_remaining": truth["remaining"],
        "observer_remaining": None if observer_knowledge is None else observer_knowledge["remaining"],
        "truth_unrevealed_out": truth["unrevealed_out"],
        "observer_unrevealed_out": (
            None if observer_knowledge is None else observer_knowledge["unrevealed_out"]),
        "truth_physical_remaining": truth["physical_remaining"],
        "observer_physical_remaining": (
            None if observer_knowledge is None else observer_knowledge["physical_remaining"]),
        "remaining_match": (
            observer_knowledge is not None
            and observer_knowledge["remaining"] == truth["remaining"]),
        "knowledge_match": observer_knowledge == truth,
        "replay_error": replay_error,
        "accepted": False,
        "independent_video": False,
        "not_a_reliable_window_claim": True,
        "exact_predeal_not_claimed": True,
        "source": "ledger-event-stream",
        **window,
    }


def correct_ledger_misread(truth_ledger, *, shown_index=0):
    """Misread a shown rank, then append CORRECTION. Remaining must match truth again."""
    if not isinstance(truth_ledger, EventLedger):
        raise TypeError("对照需要真实 EventLedger，不是剩余列表")
    observer_events, note = apply_ledger_event_error(
        truth_ledger.to_list(), kind="misread_public_deal", shown_index=shown_index)
    observer, before = _replay_observer(
        truth_ledger.session_id, observer_events,
        rule_version=truth_ledger.rule_version)
    observer.correct(note["event_id"], {"rank": note["from"]}, reason="纠错公开错认")
    after = _shoe_knowledge(observer.replay().current.shoe)
    truth = _shoe_knowledge(truth_ledger.replay().current.shoe)
    window = _window_contrast(truth_ledger, observer)
    return {
        "schema": LEDGER_EVENT_SCHEMA,
        "kind": "corrected_misread",
        "note": note,
        "before_remaining": before["remaining"],
        "after_remaining": after["remaining"],
        "truth_remaining": truth["remaining"],
        "remaining_match": after["remaining"] == truth["remaining"],
        "knowledge_match": after == truth,
        "correction_changed_remaining": before["remaining"] != after["remaining"],
        "accepted": False,
        "independent_video": False,
        "not_a_reliable_window_claim": True,
        "exact_predeal_not_claimed": True,
        "source": "ledger-event-stream",
        **window,
    }


def _event_observer_record(pack, noise, *, budget_seconds, surrender):
    record = dict(noise)
    if pack is None:
        record.update(status="inapplicable", reason_code="OBSERVER_INCONSISTENT", ev=None,
                      observer_kind="event-stream",
                      reason="公开事件与剩余组成不一致；不能把该观察写成已抓住窗口")
        return record
    evaluated = evaluate_predeal(pack, budget_seconds=budget_seconds, surrender=surrender)
    evaluated.update(noise)
    evaluated["observer_kind"] = "event-stream"
    return evaluated


def run_observation_error_study(*, pack, seed=1, lag_rounds=1, budget_seconds=5.0,
                                max_rounds=40, play_budget_seconds=2.0,
                                play_policy=CONSUMPTION_PI, surrender=SURRENDER_UNSET):
    surrender = require_declared_surrender(surrender, what="合成录牌误差对照")
    if type(lag_rounds) is not int or lag_rounds < 0:
        raise ValueError("观察延迟轮数必须是非负整数")
    rng = Random(seed)
    current = list(pack)
    rng.shuffle(current)
    history = []
    rounds = []
    for index in range(max_rounds):
        if len(current) < 4:
            break
        truth = evaluate_predeal(current, budget_seconds=budget_seconds, surrender=surrender)
        if lag_rounds == 0:
            delay = dict(truth)
            delay["observer_kind"] = "zero_lag_ideal"
        elif len(history) >= lag_rounds:
            delay = evaluate_predeal(history[-lag_rounds], budget_seconds=budget_seconds,
                                    surrender=surrender)
            delay["observer_kind"] = "lagged_prefix"
        else:
            delay = {"status": "inapplicable", "reason_code": "OBSERVER_LAG", "ev": None,
                     "observer_kind": "lag_warmup",
                     "reason": "观察延迟：尚无足够已确认前缀，不能把当前手牌EV改称已抓住窗口"}
        noisy_pack, noise = misread_one_rank(current, Random(seed + index + 17))
        noisy = evaluate_predeal(noisy_pack, budget_seconds=budget_seconds, surrender=surrender)
        noisy["noise"] = noise
        missed_pack, missed_noise = missed_remaining_card(current, Random(seed + index + 23))
        missed = evaluate_predeal(missed_pack, budget_seconds=budget_seconds, surrender=surrender)
        missed["noise"] = missed_noise
        dup_pack, dup_noise = duplicate_remaining_card(current, Random(seed + index + 29))
        duplicate = evaluate_predeal(dup_pack, budget_seconds=budget_seconds, surrender=surrender)
        duplicate["noise"] = dup_noise
        unknown_truth, unknown_obs, unknown_noise = unknown_removal_observer(
            current, Random(seed + index + 31))
        unknown_truth_ev = evaluate_predeal(unknown_truth, budget_seconds=budget_seconds,
                                            surrender=surrender)
        unknown_obs_ev = evaluate_predeal(unknown_obs, budget_seconds=budget_seconds,
                                          surrender=surrender)
        unknown_obs_ev["noise"] = unknown_noise
        history.append(list(current))
        realized, error = None, None
        public_obs = {"status": "inapplicable", "reason_code": "ROUND_NOT_PLAYED", "ev": None,
                      "observer_kind": "public-cards-only"}
        hole_revealed = None
        truth_next = {"status": "inapplicable", "reason_code": "ROUND_NOT_PLAYED", "ev": None}
        perfect_public = {"status": "inapplicable", "reason_code": "ROUND_NOT_PLAYED", "ev": None}
        event_miss = {"status": "inapplicable", "reason_code": "ROUND_NOT_PLAYED", "ev": None,
                      "observer_kind": "event-stream"}
        event_duplicate = dict(event_miss)
        event_misread = dict(event_miss)
        try:
            played = play_round(current, policy=play_policy, budget_seconds=play_budget_seconds,
                                surrender=surrender)
            realized = played["net"]
            hole_revealed = played["hole_revealed"]
            public_obs = evaluate_predeal(played["public_remaining"], budget_seconds=budget_seconds,
                                          surrender=surrender)
            public_obs["observer_kind"] = "public-cards-only"
            public_obs["hole_revealed"] = hole_revealed
            public_obs["public_remaining_is_not_private_truth"] = played[
                "public_remaining_is_not_private_truth"]
            truth_next = evaluate_predeal(played["private_remaining"], budget_seconds=budget_seconds,
                                          surrender=surrender)
            truth_next["observer_kind"] = "next-round-private-shoe"
            perfect_pack = remaining_after_events(current, played["public_dealt"])
            if perfect_pack is None:
                perfect_public = {
                    "status": "inapplicable", "reason_code": "OBSERVER_INCONSISTENT", "ev": None,
                    "observer_kind": "perfect-public-events",
                }
            else:
                perfect_public = evaluate_predeal(perfect_pack, budget_seconds=budget_seconds,
                                                  surrender=surrender)
                perfect_public["observer_kind"] = "perfect-public-events"
            event_miss = _event_observer_record(
                *apply_public_event_error(current, played["public_dealt"],
                                          Random(seed + index + 41), "miss_public_deal"),
                budget_seconds=budget_seconds, surrender=surrender)
            event_duplicate = _event_observer_record(
                *apply_public_event_error(current, played["public_dealt"],
                                          Random(seed + index + 43), "duplicate_public_deal"),
                budget_seconds=budget_seconds, surrender=surrender)
            event_misread = _event_observer_record(
                *apply_public_event_error(current, played["public_dealt"],
                                          Random(seed + index + 47), "misread_public_deal"),
                budget_seconds=budget_seconds, surrender=surrender)
            current = list(played["private_remaining"])
        except (InsufficientCards, CalculationStopped, ValueError) as err:
            error = str(err)
        rounds.append({"round_index": index, "truth": truth, "delay": delay,
                       "rank_error": noisy, "missed_card": missed, "duplicate_card": duplicate,
                       "unknown_removal_truth": unknown_truth_ev,
                       "unknown_removal_observer": unknown_obs_ev,
                       "public_observer": public_obs, "hole_revealed": hole_revealed,
                       "truth_next": truth_next, "perfect_public_events": perfect_public,
                       "event_miss": event_miss, "event_duplicate": event_duplicate,
                       "event_misread": event_misread,
                       "realized_net": realized, "play_error": error})
        if error:
            break

    delay = confusion_matrix(rounds, observer_key="delay")
    rank_error = confusion_matrix(rounds, observer_key="rank_error")
    missed_card = confusion_matrix(rounds, observer_key="missed_card")
    duplicate_card = confusion_matrix(rounds, observer_key="duplicate_card")
    unknown_removal = confusion_matrix(
        [{"truth": item["unknown_removal_truth"], "observer": item["unknown_removal_observer"]}
         for item in rounds],
        observer_key="observer")
    event_miss_matrix = confusion_matrix(
        [{"truth": item["perfect_public_events"], "observer": item["event_miss"]}
         for item in rounds],
        observer_key="observer")
    event_duplicate_matrix = confusion_matrix(
        [{"truth": item["perfect_public_events"], "observer": item["event_duplicate"]}
         for item in rounds],
        observer_key="observer")
    event_misread_matrix = confusion_matrix(
        [{"truth": item["perfect_public_events"], "observer": item["event_misread"]}
         for item in rounds],
        observer_key="observer")
    scope = evaluation_scope([item["truth"] for item in rounds])
    missed_unavailable = delay["missed_unavailable"]
    false_negative = delay["false_negative"]
    truth_positive = scope["positive"]
    return {
        "schema": SCHEMA,
        "window": WINDOW_PRE_DEAL,
        "not_a_reliable_window_claim": True,
        "independent_video": False,
        "realized_path_is_not_counterfactual_truth": True,
        "seed": seed,
        "lag_rounds": lag_rounds,
        "surrender": surrender,
        "consumption_policy": play_policy,
        "path_policy_id": play_policy,
        "evaluation_policy_id": "predeal-unsplit-composition-optimal-v1",
        "rounds": rounds,
        "summary": {
            "round_count": len(rounds),
            "truth_positive": truth_positive,
            "truth_nonpositive": scope["nonpositive"],
            "truth_unavailable": scope["unavailable"],
            "zero_window_truth": scope["zero_window"],
            "no_positive_signal_detected": scope["no_positive_signal_detected"],
            "incomplete_cannot_claim_zero_window": scope["incomplete_cannot_claim_zero_window"],
            "evaluated_count": scope["evaluated_count"],
            "unassessable_count": scope["unassessable_count"],
            "verified_no_positive_over_declared_domain": scope["verified_no_positive_over_declared_domain"],
            "delay": delay,
            "rank_error": rank_error,
            "missed_card": missed_card,
            "duplicate_card": duplicate_card,
            "unknown_removal": unknown_removal,
            "delay_missed_unavailable": missed_unavailable,
            "delay_false_negative": false_negative,
            "delay_missed_positive": missed_unavailable,
            "delay_missed_positive_rate": (
                missed_unavailable / truth_positive if truth_positive else None),
            "rank_error_false_positive": rank_error["false_positive"],
            "rank_error_false_negative": rank_error["false_negative"],
            "n_hidden_hole_after_round": sum(
                1 for item in rounds if item.get("hole_revealed") is False),
            "public_observer_is_not_private_remaining": True,
            "snapshot_remaining_is_not_event_stream": True,
            "event_miss": event_miss_matrix,
            "event_duplicate": event_duplicate_matrix,
            "event_misread": event_misread_matrix,
            "n_perfect_public_differs_from_private": sum(
                1 for item in rounds
                if item.get("hole_revealed") is False and item.get("play_error") is None),
            "note": "合成录牌误差对照，不是未使用真实录像；真值不可评不定误报；观察缺失计为因不可用错过，不是漏报；"
                    "爆牌或投降后公开剩余仍含未翻底牌，环境私有剩余不是当时观察者已知组成；"
                    "公开事件流误差按发牌事件重建剩余，与直接改未用牌组成分开计分",
        },
    }
