"""Generate pair IDs and walk a two-leg operator trial. Never certifies pairing."""
from __future__ import annotations

from time import time
from uuid import uuid4

from .operator_study import append_export, trial, write_export

SCHEMA = "hakimi-operator-wizard-v1"
NOTE = (
    "程序生成 operator_id / video_id / pair_id 和导出文件；人只负责实际操作。"
    "匹配字符串只是声明，paired 仍为 false，不能改自动提示默认。"
    "暂停后补齐不算实时跟上。"
)


def allocate_pair_identity(*, operator_name=None, video_label=None):
    """Software-minted IDs. Empty names stay undeclared; software is not the operator."""
    stamp = uuid4().hex[:12]
    video_token = _slug(video_label) or stamp[:8]
    name_token = _slug(operator_name)
    return {
        "operator_id": f"op-{name_token}-{stamp[:6]}" if name_token else f"op-{stamp[:8]}",
        "video_id": f"vid-{video_token}",
        "pair_id": f"pair-{stamp}",
        "generated_by": "software",
        "operator_name_declared": (operator_name or "").strip() or None,
        "video_label_declared": (video_label or "").strip() or None,
        "user_must_not_hand_craft_ids": True,
    }


def _slug(value):
    text = "".join(ch if ch.isalnum() else "-" for ch in (value or "").strip())
    text = "-".join(part for part in text.split("-") if part)
    return text[:24].lower() or None


def start_session(*, operator_name=None, video_label=None, identity=None):
    ident = dict(identity or allocate_pair_identity(
        operator_name=operator_name, video_label=video_label))
    return {
        "schema": SCHEMA,
        "accepted": False,
        "paired": False,
        "auto_prompt_default": False,
        "declared_pair_ids": False,
        "identity": ident,
        "pause_reconcile_not_realtime": True,
        "started_at": time(),
        "current_leg": "manual",
        "legs": {"manual": None, "assisted": None},
        "prompts": {
            "manual": "播放同一段授权录像，用纯手动方式记牌。不要暂停后一次性补齐并当成实时。",
            "assisted": "同一录像、同一组程序生成的 ID，改用辅助面板再记一遍。",
        },
        "note": NOTE,
    }


def record_leg(session, condition, *, human_run, elapsed_seconds, keystrokes, clicks,
               backlog_peak, missed_cards, duplicates, repair_seconds, usage=None):
    if condition not in ("manual", "assisted"):
        raise ValueError("对照条件只能是 manual 或 assisted")
    ident = session["identity"]
    recorded = trial(
        condition,
        human_run=bool(human_run),
        elapsed_seconds=elapsed_seconds,
        keystrokes=keystrokes,
        clicks=clicks,
        backlog_peak=backlog_peak,
        missed_cards=missed_cards,
        duplicates=duplicates,
        repair_seconds=repair_seconds,
        pause_reconcile_not_realtime=True,
        operator_id=ident["operator_id"],
        video_id=ident["video_id"],
        pair_id=ident["pair_id"],
    )
    if usage:
        recorded["usage_snapshot"] = {
            "key_presses": usage.get("key_presses"),
            "mouse_clicks": usage.get("mouse_clicks"),
            "max_pending": usage.get("max_pending"),
            "max_lag_seconds": usage.get("max_lag_seconds"),
        }
    session["legs"][condition] = recorded
    session["accepted"] = False
    session["paired"] = False
    session["auto_prompt_default"] = False
    if condition == "manual" and session["legs"]["assisted"] is None:
        session["current_leg"] = "assisted"
    else:
        session["current_leg"] = None
    return session


def trials_of(session):
    return [item for item in (session["legs"]["manual"], session["legs"]["assisted"]) if item]


def export_session(session, path, *, append=False):
    recorded = trials_of(session)
    if not recorded:
        raise ValueError("还没有可导出的对照段")
    if append:
        body = append_export(path, recorded[0], *recorded[1:])
    else:
        body = write_export(path, recorded)
    session["export_path"] = str(path)
    session["declared_pair_ids"] = bool(body.get("declared_pair_ids"))
    session["accepted"] = False
    session["paired"] = False
    session["auto_prompt_default"] = False
    session["reason_code"] = body.get("reason_code")
    return body
