"""Import a shoe-event draft into an open ledger. Cut cards are never dealt."""
from __future__ import annotations

from ..core.table import DEALER, PHASE_DEALING, PHASE_IN_PROGRESS, player_seat_name
from .events import CARD_DEALT, OBSERVATION_GAP, SOURCE_LICENSED_VIDEO
from ..analysis.shoe_event_draft import ALLOWED_SEATS

NOTE = (
    "占用候选没有座位真值。未确认牌面记观察缺口，不发明点数。"
    "切牌卡是流程标志，不得 CARD_DEALT。"
    "导入不等于验收通过，也不能把研究模板写成该真实桌。"
    "庄家只能在明确选定后入账，不能作为缺省座位。"
)

PLAYER_SEATS = frozenset(player_seat_name(i) for i in range(1, 8))


def apply_event_draft(ctrl, draft, *, seat, source=SOURCE_LICENSED_VIDEO):
    """Write confirmed ranks; keep unknowns as gaps. Requires an open shoe."""
    if not isinstance(seat, str) or seat.strip() not in ALLOWED_SEATS:
        raise ValueError("导入必须声明座位（庄家或玩家1–7）；占用页不能发明座次")
    seat = seat.strip()
    state = ctrl.state()
    if state.current is None or state.current.closed:
        raise ValueError("需要先建立牌靴。研究模板可离线使用，不能冒充该真实桌。")
    if state.current.table.phase in (PHASE_DEALING, PHASE_IN_PROGRESS):
        raise ValueError("请先结束当前轮再导入草稿")
    events = list(draft.get("events") or [])
    written = []
    burns = [event for event in events if event.get("kind") == "burn"]
    confirmed_burns = [
        event for event in burns
        if event.get("status") == "confirmed"
        and type(event.get("count")) is int
        and event["count"] > 0
    ]
    for event in confirmed_burns:
        written.append(ctrl.burn(event["count"], "开发片已确认烧牌"))
    if not burns or len(confirmed_burns) != len(burns):
        written.append(ctrl.mark_gap("烧牌数量未知，不得默认为 0"))
    rounds = []
    seen = set()
    for event in events:
        if event.get("kind") not in ("round_start", "deal", "hidden", "round_end"):
            continue
        key = event.get("round_id") or "unassigned"
        if key in seen:
            continue
        seen.add(key)
        rounds.append(key)
    for key in rounds:
        items = [event for event in events if event.get("round_id") == key]
        if not any(event.get("kind") in ("deal", "hidden") for event in items):
            continue
        participant = seat if seat != DEALER else player_seat_name(1)
        player_seats = []
        for event in items:
            dest = event.get("seat")
            if dest in PLAYER_SEATS and dest not in player_seats:
                player_seats.append(dest)
        if player_seats:
            participant_list = sorted(
                player_seats, key=lambda name: int(str(name).replace("玩家", "") or 0))
        else:
            participant_list = [participant] if participant in PLAYER_SEATS else [player_seat_name(1)]
        written.append(ctrl.start_round(participant_list))
        counted = [event for event in items if event.get("kind") in ("deal", "hidden")]
        booked = 0
        for event in counted:
            dest = event.get("seat") if event.get("seat") in ALLOWED_SEATS else seat
            evidence = event.get("still_path")
            evidence = evidence if isinstance(evidence, str) else None
            if event.get("kind") == "deal" and event.get("status") == "confirmed" and event.get("rank") not in (None, "", "unknown"):
                written.append(ctrl.deal_shown(
                    dest, event["rank"], evidence=evidence, source=source))
                booked += 1
            elif event.get("kind") == "hidden" and event.get("status") == "confirmed":
                written.append(ctrl.deal_hidden(
                    dest, evidence=evidence, source=source))
                booked += 1
        if booked != len(counted):
            written.append(ctrl.mark_gap(f"{key} 座位或牌面未确认；占用候选不能当逐牌真值"))
        written.append(ctrl.end_round_unsettled(
            f"{key} 开发片导入，未结算", observation_status="incomplete"))
    if any(event.get("kind") == "cut_card" for event in events):
        written.append(ctrl.mark_gap("切牌卡是流程标志，未计入记牌"))
    if any(event.get("kind") == "between_rounds" for event in events):
        written.append(ctrl.mark_gap("局间等待画面上的牌是上一局遗留，未计入本轮发牌"))
    types = [event.etype for event in written]
    return {
        "accepted": False,
        "source": source,
        "seat": seat,
        "events_written": len(written),
        "card_dealt": types.count(CARD_DEALT),
        "gaps": types.count(OBSERVATION_GAP),
        "offline_mc_ready": False,
        "note": NOTE,
    }
