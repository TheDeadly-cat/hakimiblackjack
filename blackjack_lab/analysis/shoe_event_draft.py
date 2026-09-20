"""Round-by-round shoe event draft. Cut card is a marker, not a playing card."""
from __future__ import annotations

from pathlib import Path
import json
from time import time
from uuid import uuid4

from ..core.table import DEALER, player_seat_name

SCHEMA = "hakimi-shoe-event-draft-v1"
COUNTED_KINDS = frozenset({"deal", "burn", "hidden"})
ALLOWED_SEATS = frozenset((DEALER,) + tuple(player_seat_name(i) for i in range(1, 8)))
MARKER_KINDS = frozenset({
    "shoe_open", "pre_first_card", "cut_card", "stop_play",
    "reshuffle_or_box_change", "dropped_frame", "obstruction", "round_start",
    "round_end", "between_rounds", "in_play_still",
})
EVENT_KINDS = tuple(sorted(COUNTED_KINDS | MARKER_KINDS | {"unknown_obstructed"}))
OBSERVATION_RISK_KINDS = frozenset({"dropped_frame", "obstruction", "unknown_obstructed"})
PASSIVE_IMPORT_KINDS = frozenset({"round_start", "round_end", "in_play_still"})
SOFTWARE_ATTESTERS = frozenset({
    "grok", "chatgpt", "codex", "cursor", "software", "ai", "assistant",
})
NOTE = (
    "待核对草稿：确认正确的可以批量确认，错的改单张，不清楚的保留未知。"
    "红色切牌卡是流程标志，不得当作普通扑克牌加入记牌数量。"
    "从开头录到结尾不表示每张牌都能识别；有遮挡、掉帧或未揭示就保留未知，"
    "不得为了凑完整剩余组成而推测填补。"
)


def _event(kind, **fields):
    if kind not in EVENT_KINDS:
        raise ValueError(f"未知事件种类: {kind}")
    event_id = fields.pop("event_id", None) or uuid4().hex[:12]
    status = fields.pop("status", "draft")
    row = {
        "event_id": event_id,
        "kind": kind,
        "status": status,
        "round_id": fields.pop("round_id", None),
        "rank": fields.pop("rank", None),
        "count": fields.pop("count", None),
        "frame_index": fields.pop("frame_index", None),
        "still_path": fields.pop("still_path", None),
        "notes": fields.pop("notes", "") or "",
        "counted_in_remaining": kind in COUNTED_KINDS,
        "is_playing_card": kind in COUNTED_KINDS,
        "recorded_at": time(),
    }
    if kind == "cut_card":
        row["counted_in_remaining"] = False
        row["is_playing_card"] = False
        row["is_process_marker"] = True
    row.update(fields)
    return row


def empty_draft(*, video_path=None, video_sha256=None, video_bytes=None,
                role="development", filename=None):
    if role not in ("development", "candidate", "holdout"):
        raise ValueError("材料角色只能是 development / candidate / holdout")
    if role == "holdout" and "12.58.11.02" in str(filename or video_path or ""):
        raise ValueError("12.58.11.02 已用于 navy-live 开发，不能登记为未使用留出")
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "human_run": False,
        "role": role,
        "filename": filename,
        "video_path": str(video_path) if video_path else None,
        "video_sha256": video_sha256,
        "video_bytes": video_bytes,
        "source_span": None,
        "round_coverage": {},
        "n_decks": None,
        "initial_burn_count": None,
        "burn_cards_known": None,
        "events": [],
        "note": NOTE,
    }


def development_clip_stub(*, filename, video_path=None, video_sha256=None,
                          video_bytes=None, role="development"):
    draft = empty_draft(
        video_path=video_path, video_sha256=video_sha256, video_bytes=video_bytes,
        role=role, filename=filename)
    for spec in (
        {"kind": "shoe_open", "notes": "用户声明从开靴录到红牌停手/洗牌换盒；开靴画面待逐帧核对"},
        {"kind": "pre_first_card", "status": "unknown_kept",
         "notes": "第一张牌之前的画面尚未抽出或核对"},
        {"kind": "burn", "status": "unknown_kept", "count": None,
         "notes": "烧牌未知，不得默认为 0"},
        {"kind": "round_start", "round_id": "round-1",
         "notes": "第一轮边界待核对"},
        {"kind": "deal", "round_id": "round-1", "status": "unknown_kept", "rank": None,
         "notes": "未确认逐牌真值；保留未知"},
        {"kind": "cut_card", "notes": "红牌切牌卡是流程标志，不计入记牌"},
        {"kind": "stop_play", "notes": "实际停止时点待核对"},
        {"kind": "reshuffle_or_box_change", "notes": "洗牌换盒时点待核对"},
    ):
        draft["events"].append(_event(**spec))
    return draft


def add_event(draft, kind, **fields):
    event = _event(kind, **fields)
    if kind == "cut_card" and event["counted_in_remaining"]:
        raise ValueError("切牌卡不得计入记牌数量")
    draft["events"].append(event)
    draft["accepted"] = False
    return event


def add_unknown_card(draft, round_id, *, kind="deal", seat=None, still_path=None,
                     notes=None):
    """Human-added unknown slot. Does not invent a rank or confirm a seat by default."""
    if kind not in ("deal", "hidden"):
        raise ValueError("只能追加未知明牌或暗牌")
    if round_id in (None, ""):
        raise ValueError("追加牌位必须属于一局")
    if seat is not None and (not isinstance(seat, str) or seat.strip() not in ALLOWED_SEATS):
        raise ValueError("座位必须是庄家或玩家1–7，不能发明座次")
    return add_event(
        draft, kind,
        round_id=round_id,
        rank=None,
        status="unknown_kept",
        seat=seat.strip() if seat else None,
        still_path=still_path,
        notes=notes or "人工追加的未知牌位，不是识别结果",
    )


def keep_unknown(draft, event_ids=None):
    """Retain unknown ranks/burns without inventing cards or signing a person."""
    wanted = None if event_ids is None else set(event_ids)
    for event in draft["events"]:
        if wanted is not None and event["event_id"] not in wanted:
            continue
        if event["kind"] == "cut_card":
            event["status"] = "unknown_kept" if event.get("status") == "draft" else event["status"]
            event["counted_in_remaining"] = False
            continue
        if event["kind"] not in COUNTED_KINDS:
            continue
        rank = event.get("rank")
        if rank in (None, "", "unknown") and event.get("count") is None:
            event["status"] = "unknown_kept"
    draft["accepted"] = False
    return draft


def _require_human_attester(name, action):
    if not name or str(name).strip().lower() in SOFTWARE_ATTESTERS:
        raise ValueError(f"{action}必须由人作出，软件不能代签")
    return str(name).strip()


def observation_risk_blocks(event):
    """Unresolved deal-observation doubts block round coverage. Excluded background does not."""
    if event.get("kind") not in OBSERVATION_RISK_KINDS:
        return False
    if event.get("affects_composition") is False and event.get("status") == "confirmed":
        return False
    return True


def blocking_observation_risks(draft, round_id=None):
    events = draft.get("events") or []
    risks = []
    for event in events:
        if not observation_risk_blocks(event):
            continue
        event_round = event.get("round_id")
        if round_id is None:
            risks.append(event)
            continue
        if event_round in (round_id, None, "", "unassigned"):
            risks.append(event)
    return risks


def draft_source_span(draft):
    explicit = draft.get("source_span")
    if isinstance(explicit, dict):
        start = explicit.get("start_frame")
        end = explicit.get("end_frame")
        if start is not None or end is not None:
            return {"start_frame": start, "end_frame": end}
    frames = [
        event.get("frame_index")
        for event in draft.get("events") or []
        if event.get("frame_index") is not None
    ]
    if not frames:
        return None
    return {"start_frame": min(frames), "end_frame": max(frames)}


def round_coverage_identity(draft, round_id):
    items = [event for event in draft.get("events") or [] if event.get("round_id") == round_id]
    return json.dumps({
        "round_id": round_id,
        "video_sha256": draft.get("video_sha256"),
        "video_bytes": draft.get("video_bytes"),
        "source_span": draft_source_span(draft),
        "events": [
            {
                "kind": event.get("kind"),
                "status": event.get("status"),
                "rank": event.get("rank"),
                "seat": event.get("seat"),
                "count": event.get("count"),
                "frame_index": event.get("frame_index"),
                "still_path": event.get("still_path"),
                "still_sha256": event.get("still_sha256"),
                "affects_composition": event.get("affects_composition"),
            }
            for event in items
        ],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def coverage_attestation_valid(draft, round_id):
    record = (draft.get("round_coverage") or {}).get(round_id)
    if not isinstance(record, dict):
        return False
    return record.get("identity") == round_coverage_identity(draft, round_id)


def _cards_confirmed_for_coverage(items):
    counted = [event for event in items if event.get("kind") in ("deal", "hidden")]
    if not counted:
        return False
    if any(event.get("status") != "confirmed" for event in counted):
        return False
    if any(
        event.get("kind") == "deal" and event.get("rank") in (None, "", "unknown")
        for event in counted
    ):
        return False
    return True


def confirm_round_coverage(draft, round_id, *, confirmed_by, recorded_by="software-recorder",
                           source_span=None, notes=""):
    """Second-layer confirmation: observed range for this round is covered. Not a card guess."""
    attester = _require_human_attester(confirmed_by, "整轮观察范围核对")
    if round_id in (None, "", "shoe-open", "shoe-end", "unassigned"):
        raise ValueError("只能对实际对局轮次确认观察范围")
    items = [event for event in draft.get("events") or [] if event.get("round_id") == round_id]
    if not items:
        raise ValueError("没有该轮事件，不能确认观察范围")
    if not _cards_confirmed_for_coverage(items):
        raise ValueError("牌面尚未全部核对，不能确认整轮覆盖")
    if blocking_observation_risks(draft, round_id):
        raise ValueError("未解决的丢帧、遮挡或未知事件仍可能漏牌，不能确认整轮覆盖")
    identity = round_coverage_identity(draft, round_id)
    coverage = dict(draft.get("round_coverage") or {})
    coverage[round_id] = {
        "confirmed_by": attester,
        "recorded_by": recorded_by,
        "identity": identity,
        "source_span": source_span or draft_source_span(draft),
        "video_sha256": draft.get("video_sha256"),
        "notes": notes or "",
        "confirmed_at": time(),
        "synthetic_fixture": "synthetic" in str(notes).lower() or "fixture" in str(notes).lower(),
    }
    draft["round_coverage"] = coverage
    draft["accepted"] = False
    return coverage[round_id]


def confirm_events(draft, event_ids, *, confirmed_by, recorded_by="software-recorder"):
    wanted = set(event_ids)
    attester = _require_human_attester(confirmed_by, "逐牌确认")
    for event in draft["events"]:
        if event["event_id"] not in wanted:
            continue
        if event["kind"] in COUNTED_KINDS and event.get("rank") in (None, "", "unknown"):
            if event["kind"] == "hidden":
                event["status"] = "confirmed"
                event["confirmed_by"] = attester
                event["recorded_by"] = recorded_by
                event["confirmation"] = "hole_exists_rank_unknown"
                continue
            if event["kind"] == "burn" and event.get("count") is None:
                event["status"] = "unknown_kept"
                event["confirmation"] = "kept_unknown"
                continue
            if event["kind"] != "burn":
                event["status"] = "unknown_kept"
                event["confirmation"] = "kept_unknown"
                continue
        event["status"] = "confirmed"
        event["confirmed_by"] = attester
        event["recorded_by"] = recorded_by
    draft["accepted"] = False
    return draft


def correct_event(draft, event_id, **fields):
    for event in draft["events"]:
        if event["event_id"] != event_id:
            continue
        if event["kind"] == "cut_card":
            fields.pop("counted_in_remaining", None)
            fields.pop("is_playing_card", None)
        event.update(fields)
        event["status"] = "corrected"
        event["counted_in_remaining"] = event["kind"] in COUNTED_KINDS
        if event["kind"] == "cut_card":
            event["counted_in_remaining"] = False
            event["is_playing_card"] = False
        draft["accepted"] = False
        return event
    raise KeyError(event_id)


def review_pages(draft, *, skip_waiting_only=False):
    """One page for shoe open, one per round, one for stop/cut/shuffle."""
    open_kinds = {"shoe_open", "pre_first_card", "burn"}
    end_kinds = {"cut_card", "stop_play", "reshuffle_or_box_change"}
    opening = [event for event in draft["events"] if event["kind"] in open_kinds]
    ending = [event for event in draft["events"] if event["kind"] in end_kinds]
    rounds = {}
    for event in draft["events"]:
        if event["kind"] in open_kinds or event["kind"] in end_kinds:
            continue
        key = event.get("round_id") or "unassigned"
        rounds.setdefault(key, []).append(event)
    pages = [_page("shoe-open", "开靴 / 烧牌 / 第一张之前", opening)]
    for key, events in rounds.items():
        pages.append(_page(key, f"轮次 {key}", events))
    pages.append(_page("shoe-end", "切牌 / 停手 / 换盒", ending))
    if skip_waiting_only:
        pages = [page for page in pages if not page["waiting_only"]]
    return pages


def _page(page_id, title, events):
    kinds = {event.get("kind") for event in events}
    waiting_only = "between_rounds" in kinds and "deal" not in kinds and "hidden" not in kinds
    sequence = _sequence_stills(events)
    return {
        "page_id": page_id,
        "title": title,
        "events": events,
        "still_path": sequence[0] if sequence else None,
        "sequence_stills": sequence,
        "waiting_only": waiting_only,
        "needs_rank_review": any(
            event.get("kind") in ("deal", "hidden", "burn")
            and event.get("rank") in (None, "", "unknown")
            and event.get("count") is None
            for event in events
        ),
    }


def _sequence_stills(events):
    rows = [event for event in events if event.get("still_path")]
    rows.sort(key=lambda event: (
        event.get("frame_index") is None,
        event.get("frame_index") or 0,
        int(event.get("slot") or 0),
    ))
    paths = []
    seen = set()
    for event in rows:
        path = event["still_path"]
        if path in seen:
            continue
        seen.add(path)
        paths.append(path)
    return paths


def confirm_page(draft, page_id, *, confirmed_by, recorded_by="software-recorder"):
    page = next((item for item in review_pages(draft) if item["page_id"] == page_id), None)
    if page is None:
        raise KeyError(page_id)
    return confirm_events(
        draft, [event["event_id"] for event in page["events"]],
        confirmed_by=confirmed_by, recorded_by=recorded_by,
    )


def set_event_rank(draft, event_id, rank):
    """Set or clear a playing-card rank. Cut cards cannot become counted ranks."""
    for event in draft["events"]:
        if event["event_id"] != event_id:
            continue
        if event["kind"] == "cut_card":
            raise ValueError("切牌卡不能改成计入记牌的牌面")
        if event["kind"] not in COUNTED_KINDS:
            raise ValueError("只有发牌/烧牌/暗牌可以填写牌面")
        value = None if rank in (None, "", "unknown", "—") else str(rank).strip().upper()
        event["rank"] = value
        event["status"] = "draft"
        draft["accepted"] = False
        return event
    raise KeyError(event_id)


def set_event_seat(draft, event_id, seat):
    """Assign a human-chosen seat. Occupancy pages do not invent one."""
    if not isinstance(seat, str) or seat.strip() not in ALLOWED_SEATS:
        raise ValueError("座位必须是庄家或玩家1–7，不能发明座次")
    seat = seat.strip()
    for event in draft["events"]:
        if event["event_id"] != event_id:
            continue
        if event["kind"] not in ("deal", "hidden"):
            raise ValueError("只有发牌或暗牌可以指定座位")
        event["seat"] = seat
        if event.get("status") == "confirmed":
            event["status"] = "draft"
        draft["accepted"] = False
        return event
    raise KeyError(event_id)


DEALER_TEMPLATE = "standard_dealer_two_card_v1"


def ensure_standard_dealer_slots(draft):
    """Add unknown dealer upcard + hole slots on in-play pages. Not a detection."""
    added = 0
    for page in review_pages(draft, skip_waiting_only=True):
        if page["page_id"] in ("shoe-open", "shoe-end") or page["waiting_only"]:
            continue
        cards = [
            event for event in page["events"]
            if event.get("kind") in ("deal", "hidden")
        ]
        if not cards:
            continue
        if any(
            event.get("seat") == DEALER or event.get("seat_hint") == DEALER
            for event in cards
        ):
            continue
        still = page.get("still_path")
        add_event(
            draft, "deal",
            round_id=page["page_id"],
            rank=None,
            status="unknown_kept",
            seat_hint=DEALER,
            still_path=still,
            template=DEALER_TEMPLATE,
            notes="结构占位：庄家明牌须看图确认，不是识别结果",
        )
        add_event(
            draft, "hidden",
            round_id=page["page_id"],
            rank=None,
            status="unknown_kept",
            seat_hint=DEALER,
            still_path=still,
            template=DEALER_TEMPLATE,
            notes="结构占位：庄家底牌须看图确认；蓝背未自动当成真值",
        )
        added += 2
    draft["accepted"] = False
    draft["dealer_template_slots"] = added
    return draft


def composition_status(draft):
    counted = 0
    unknown = 0
    cut_markers = 0
    for event in draft["events"]:
        if event["kind"] == "cut_card":
            cut_markers += 1
            if event.get("counted_in_remaining"):
                raise ValueError("切牌卡混入了记牌数量")
            continue
        if event["kind"] not in COUNTED_KINDS:
            continue
        confirmed = event.get("status") == "confirmed"
        rank = event.get("rank")
        if event["kind"] == "burn":
            if not confirmed or event.get("count") is None:
                unknown += 1
            else:
                counted += int(event["count"])
            continue
        if confirmed and rank not in (None, "", "unknown"):
            counted += 1
        else:
            unknown += 1
    complete = (
        unknown == 0
        and draft.get("n_decks") is not None
        and draft.get("burn_cards_known") is True
        and draft.get("initial_burn_count") is not None
    )
    return {
        "confirmed_playing_cards": counted,
        "unknown_or_unconfirmed": unknown,
        "cut_markers": cut_markers,
        "complete_remaining": bool(complete),
        "cut_card_counted_in_remaining": False,
        "n_decks": draft.get("n_decks"),
        "note": "完整剩余组成需要已知副数、已知烧牌、且没有未知牌；不能靠录像起止自动填满",
    }


def attach_stills_to_draft(draft, stills):
    """Attach decoded stills onto draft events. Does not invent ranks."""
    frames = list((stills or {}).get("frames") or [])
    kind_order = (
        "shoe_open", "pre_first_card", "deal", "cut_card", "stop_play",
        "reshuffle_or_box_change",
    )
    unused = list(frames)
    for kind in kind_order:
        if not unused:
            break
        for event in draft["events"]:
            if event["kind"] != kind or event.get("still_path"):
                continue
            frame = unused.pop(0)
            event["still_path"] = frame["path"]
            event["frame_index"] = frame["frame_index"]
            event["still_sha256"] = frame.get("sha256")
            break
    draft["stills"] = {
        "source_hashed": bool(stills.get("source_hashed")),
        "width": stills.get("width"),
        "height": stills.get("height"),
        "fps": stills.get("fps"),
        "frame_count": stills.get("frame_count"),
        "video_bytes": stills.get("video_bytes"),
        "frames": frames,
    }
    draft["accepted"] = False
    return draft


def write_draft(path, draft):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(draft)
    payload["accepted"] = False
    payload["passed"] = False
    payload["composition"] = composition_status(payload)
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def load_draft(path):
    body = json.loads(Path(path).read_text(encoding="utf-8"))
    if body.get("schema") != SCHEMA:
        raise ValueError("事件草稿格式不正确")
    body["accepted"] = False
    return body
