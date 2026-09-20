"""Concrete table-rule differences. Felt text is not a complete rules page."""
from __future__ import annotations

from pathlib import Path
import json

SCHEMA = "hakimi-table-rules-diff-v1"
STATUS_VISIBLE = "visible_on_felt"
STATUS_NOT_VISIBLE = "not_visible"
STATUS_PUBLIC_HELP = "public_help_unconfirmed"
STATUS_PUBLIC_CONFLICT = "conflict_in_public_help"
NOTE = (
    "这是差异整理，不是已验收桌规档案，也不能把研究模板写成该真实桌。"
    "供应商品牌不能套用同一模型：Evolution 的 Power/Infinite/Speed Blackjack 与普通多座不同。"
    "画面若是 Pragmatic Play Live，不得把口头 Evolution 写进档案。"
    "n_decks 不得默认为 6，也不得把公开页常见的 8 写入本桌 n_decks。"
    "公开规则页只是待确认候选，不适用于该真实桌，除非用户打开该桌帮助页确认。"
)

# Generic Pragmatic Live public pages. Not this Stake table. Do not copy onto n_decks.
PUBLIC_HELP_SOURCES = (
    {
        "id": "pragmatic_official_das",
        "url": "https://www.pragmaticplay.com/en/live-casino/double-down-after-split/",
        "fetched": True,
        "quote": (
            "Players can experience the benefits of Double Down After Split on all "
            "Pragmatic Play blackjack titles, including classic Blackjack, Speed "
            "Blackjack, and Privé Lounge Blackjack."
        ),
        "claims": {"double_after_split": True},
        "applies_to_this_table": False,
        "note": "官方产品页。Stake 绒面不等于 Evolution；也未证明本桌就是 classic 而非 Speed/Privé。",
    },
    {
        "id": "btcgosu_pp_vs_evo",
        "url": "https://www.btcgosu.com/labs/blackjack/",
        "fetched": True,
        "quote": (
            "Pragmatic vs Evolution: 8 decks, blackjack 3:2, dealer stands on 17, "
            "DAS yes on Pragmatic, surrender none, hole-card rule no peek. "
            "Authors note that a peek rule would change absolute edges. "
            "Reshuffle at roughly 50–55% penetration."
        ),
        "claims": {
            "n_decks": 8,
            "s17": True,
            "bj_payout": "3:2",
            "double_after_split": True,
            "surrender": False,
            "peek": False,
            "penetration": "about 50-55%",
        },
        "applies_to_this_table": False,
        "note": "第三方对照文，不是 Stake 该桌帮助页。",
    },
    {
        "id": "win_bet_pp_live_52_ruby",
        "url": "https://win.bet/en/game/pragmatic-play-live-blackjack-52-ruby",
        "fetched": False,
        "search_snippet": "8-deck shoe; dealer peeks for blackjack (Vegas rules).",
        "claims": {"n_decks": 8, "peek": True},
        "applies_to_this_table": False,
        "note": "另一张 Pragmatic 桌的运营商页摘要；与 btcgosu 的 no peek 冲突。本次未抓取全文。",
    },
    {
        "id": "blackjackinfo_pp_live",
        "url": "https://www.blackjackinfo.com/live-blackjack-game/pragmatic-play/",
        "fetched": False,
        "http_status": 403,
        "search_snippet": "Classic live blackjack commonly listed as 8 decks, S17, DAS, no surrender.",
        "claims": {
            "n_decks": 8,
            "s17": True,
            "double_after_split": True,
            "surrender": False,
        },
        "applies_to_this_table": False,
        "note": "本次抓取返回 403；只作搜索摘要，不得当成本桌规则。",
    },
)

FIELDS = (
    {
        "id": "game_name_variant",
        "label": "准确游戏名称、桌号和变体",
        "why": "决定是不是普通多副牌模型",
    },
    {
        "id": "n_decks_removed_ranks",
        "label": "牌副数、是否移除某些牌",
        "why": "决定初始组成",
    },
    {
        "id": "s17_h17_bj_payout",
        "label": "S17／H17、blackjack 赔付",
        "why": "改变终局与净收益",
    },
    {
        "id": "hole_card_peek",
        "label": "底牌和 A／十点明牌检查时机",
        "why": "改变可用信息及庄家 blackjack 分支",
    },
    {
        "id": "double_split_das_surrender",
        "label": "加倍、分牌、DAS、投降",
        "why": "决定合法动作和损失范围",
    },
    {
        "id": "special_fees_or_charlies",
        "label": "特殊费用、特殊胜利或和局",
        "why": "普通收益模型可能不适用",
    },
    {
        "id": "burn_cut_shuffle_order",
        "label": "烧牌、切牌、重洗和发牌顺序",
        "why": "决定牌靴状态怎样连续更新",
    },
)


def _row(field_id, *, status, value=None, evidence=None, needs_user=True):
    spec = next(item for item in FIELDS if item["id"] == field_id)
    return {
        "id": field_id,
        "label": spec["label"],
        "why": spec["why"],
        "status": status,
        "value": value,
        "evidence": evidence,
        "needs_user": bool(needs_user),
        "defaulted": False,
    }


def empty_diff():
    return {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "applies_to_live_table": False,
        "research_template_ok_for_offline": True,
        "provider_on_screen": None,
        "table_label_on_screen": None,
        "not_evolution": None,
        "n_decks": None,
        "rows": [_row(item["id"], status=STATUS_NOT_VISIBLE) for item in FIELDS],
        "uncertain": [item["id"] for item in FIELDS],
        "note": NOTE,
    }


def _source_urls(*source_ids):
    by_id = {item["id"]: item["url"] for item in PUBLIC_HELP_SOURCES}
    return "；".join(by_id[item] for item in source_ids if item in by_id)


def _provider_allows_pragmatic_public_help(provider):
    text = str(provider or "").lower()
    if "evolution" in text and "pragmatic" not in text and "stake" not in text:
        return False
    return "stake" in text or "pragmatic" in text


def _overlay_public_help(row):
    """Fill a non-visible row with public-help candidates. Never marks this table."""
    field_id = row["id"]
    updated = dict(row)
    updated["needs_user"] = True
    updated["defaulted"] = False
    updated["applies_to_this_table"] = False
    if field_id == "n_decks_removed_ranks":
        updated["status"] = STATUS_PUBLIC_HELP
        updated["value"] = "公开候选：常见 8 副、未见移除 9/10；不得写入本桌 n_decks"
        updated["evidence"] = _source_urls(
            "btcgosu_pp_vs_evo", "win_bet_pp_live_52_ruby", "blackjackinfo_pp_live")
        return updated
    if field_id == "hole_card_peek":
        updated["status"] = STATUS_PUBLIC_CONFLICT
        updated["value"] = (
            "公开来源冲突：btcgosu 记 no peek；win.bet 记 Vegas peek。"
            "开发片绒面可见蓝色底牌，故“无底牌”不成立；检查时机仍未知。"
        )
        updated["evidence"] = _source_urls("btcgosu_pp_vs_evo", "win_bet_pp_live_52_ruby")
        return updated
    if field_id == "double_split_das_surrender":
        updated["status"] = STATUS_PUBLIC_HELP
        updated["value"] = (
            "公开候选：DAS 允许（官方称适用于全部 Pragmatic Play blackjack titles，"
            "含 classic / Speed / Privé）；无投降。"
            "这不是该 Stake 桌的验收，也未排除 Speed 等变体。"
        )
        updated["evidence"] = _source_urls("pragmatic_official_das", "btcgosu_pp_vs_evo")
        return updated
    if field_id == "special_fees_or_charlies":
        updated["status"] = STATUS_PUBLIC_HELP
        updated["value"] = (
            "公开 classic 对照未见 Six Card Charlie；Infinite/Power 变体规则不同。"
            "未证明本桌没有特殊胜利或费用。"
        )
        updated["evidence"] = "变体警告：不得凭 Stake/Pragmatic 品牌套同一模型"
        return updated
    if field_id == "burn_cut_shuffle_order":
        updated["status"] = STATUS_PUBLIC_HELP
        updated["value"] = (
            "公开候选：btcgosu 写 Pragmatic 约 50–55% 渗透后重洗；"
            "本桌烧牌、切牌剩余与发牌顺序未见。"
        )
        updated["evidence"] = _source_urls("btcgosu_pp_vs_evo")
        return updated
    if field_id == "game_name_variant":
        updated["status"] = STATUS_PUBLIC_HELP
        updated["value"] = (
            "Stake 绒面；桌号与准确变体未见。"
            "公开页不能锁定是 classic 还是 Speed/Privé。"
        )
        updated["evidence"] = _source_urls("pragmatic_official_das")
        return updated
    return row


def merge_public_help_candidates(diff):
    """Attach generic public-help candidates. Never accepts this live table."""
    body = dict(diff)
    body["accepted"] = False
    body["passed"] = False
    body["applies_to_live_table"] = False
    body["n_decks"] = None
    body["public_help_applies_to_this_table"] = False
    if not _provider_allows_pragmatic_public_help(body.get("provider_on_screen")):
        body["public_help_skipped"] = "provider_not_stake_or_pragmatic"
        body["public_help_sources"] = []
        body["public_help_candidate_n_decks"] = None
        body["public_help_peek_conflict"] = False
        body["uncertain"] = [
            row["id"] for row in body.get("rows") or []
            if row.get("status") != STATUS_VISIBLE
        ]
        return body
    body["public_help_sources"] = [dict(item) for item in PUBLIC_HELP_SOURCES]
    body["public_help_candidate_n_decks"] = 8
    rows = []
    for row in body.get("rows") or []:
        if row.get("status") == STATUS_VISIBLE:
            rows.append(row)
            continue
        rows.append(_overlay_public_help(row))
    body["rows"] = rows
    body["uncertain"] = [row["id"] for row in rows if row["status"] != STATUS_VISIBLE]
    body["public_help_peek_conflict"] = any(
        row["id"] == "hole_card_peek" and row["status"] == STATUS_PUBLIC_CONFLICT
        for row in rows)
    body["user_action"] = (
        "打开该桌帮助/规则页并确认不确定行。"
        "公开规则页只是候选，不得把 8 副或 peek 写成该真实桌。"
    )
    return body


def from_felt_observation(observation, *, include_public_help=True):
    """Fill what the felt/browser still actually shows. Do not invent decks."""
    obs = dict(observation or {})
    provider = obs.get("provider_on_screen")
    table = obs.get("table_label_on_screen")
    visible = list(obs.get("visible_felt_text") or [])
    missing = set(obs.get("not_visible") or [])
    text = " | ".join(visible)
    lower = text.lower()
    rows = []

    game_value = " / ".join(part for part in (provider, table) if part) or None
    if game_value:
        rows.append(_row(
            "game_name_variant", status=STATUS_VISIBLE, value=game_value,
            evidence=obs.get("url_on_screen") or obs.get("still_path"),
            needs_user=not bool(table)))
    else:
        rows.append(_row("game_name_variant", status=STATUS_NOT_VISIBLE))

    if "n_decks" in missing or obs.get("n_decks") in (None, ""):
        rows.append(_row(
            "n_decks_removed_ranks", status=STATUS_NOT_VISIBLE,
            value=None, evidence="绒面未见副数；不得默认为 6"))
    else:
        rows.append(_row(
            "n_decks_removed_ranks", status=STATUS_VISIBLE,
            value=obs.get("n_decks"), needs_user=False))

    payout_bits = []
    if "3 TO 2" in text.upper() or "3 to 2" in text:
        payout_bits.append("BJ 3:2")
    if "stand on all 17" in lower:
        payout_bits.append("S17")
    if "INSURANCE PAYS 2 TO 1" in text.upper():
        payout_bits.append("保险 2:1")
    if payout_bits:
        rows.append(_row(
            "s17_h17_bj_payout", status=STATUS_VISIBLE,
            value="；".join(payout_bits), needs_user=False))
    else:
        rows.append(_row("s17_h17_bj_payout", status=STATUS_NOT_VISIBLE))

    peek_unknown = any(name in missing for name in (
        "american_hole_card", "peek_timing", "check_bj_when"))
    rows.append(_row(
        "hole_card_peek",
        status=STATUS_NOT_VISIBLE if peek_unknown else STATUS_VISIBLE,
        value=None if peek_unknown else obs.get("peek_timing")))

    action_unknown = any(name in missing for name in (
        "surrender", "double_after_split"))
    rows.append(_row(
        "double_split_das_surrender",
        status=STATUS_NOT_VISIBLE if action_unknown else STATUS_VISIBLE))

    rows.append(_row(
        "special_fees_or_charlies", status=STATUS_NOT_VISIBLE,
        evidence="未见 Six Card Charlie / 特殊费用；未证明不存在"))
    cut_unknown = "cut_card_remaining" in missing or obs.get("cut_card_remaining") is None
    rows.append(_row(
        "burn_cut_shuffle_order",
        status=STATUS_NOT_VISIBLE if cut_unknown else STATUS_VISIBLE,
        evidence="烧牌与切牌剩余未见"))

    provider_is_pragmatic = bool(provider and "pragmatic" in str(provider).lower())
    provider_is_stake = bool(provider and "stake" in str(provider).lower())
    uncertain = [row["id"] for row in rows if row["status"] != STATUS_VISIBLE]
    body = {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "applies_to_live_table": False,
        "research_template_ok_for_offline": True,
        "provider_on_screen": provider,
        "table_label_on_screen": table,
        "url_on_screen": obs.get("url_on_screen"),
        "source_video": obs.get("source_video"),
        "still_path": obs.get("still_path"),
        "frame_index": obs.get("frame_index"),
        "not_evolution": True if (provider_is_pragmatic or provider_is_stake) else None,
        "n_decks": None,
        "visible_felt_text": visible,
        "rows": rows,
        "uncertain": uncertain,
        "user_action": "打开该桌帮助/规则页或提供对应截图；不要先写技术 JSON",
        "note": NOTE,
    }
    if include_public_help:
        return merge_public_help_candidates(body)
    return body


PRINTED_FELT_SLOGANS = (
    "BLACKJACK PAYS 3 TO 2",
    "Dealer must draw to 16 and stand on all 17s",
)


def printed_felt_observation(*, still_path=None, source_video=None):
    """Record printed felt slogans. Does not invent decks, peek, or a live table."""
    return {
        "provider_on_screen": "Stake",
        "table_label_on_screen": None,
        "visible_felt_text": list(PRINTED_FELT_SLOGANS),
        "not_visible": [
            "n_decks", "surrender", "double_after_split",
            "american_hole_card", "peek_timing", "cut_card_remaining",
        ],
        "n_decks": None,
        "still_path": str(still_path) if still_path else None,
        "source_video": source_video,
        "accepted": False,
        "recorded_by": "software-felt-print",
        "note": "仅绒面印刷字。桌号、副数、投降、分牌与检查时机须打开规则页。",
    }


def load_observation(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


SOFTWARE_ATTESTER_NAMES = frozenset({
    "grok", "chatgpt", "codex", "cursor", "software", "ai", "assistant",
})
ROW_DECISION_MATCH = "matches_this_table"
ROW_DECISION_MISMATCH = "does_not_match_this_table"
ROW_DECISION_UNSURE = "still_uncertain"
ROW_DECISIONS = (ROW_DECISION_MATCH, ROW_DECISION_MISMATCH, ROW_DECISION_UNSURE)


def _require_human_attester(attested_by):
    name = (attested_by or "").strip()
    if not name:
        raise ValueError("必须有具名声明人")
    if name.lower() in SOFTWARE_ATTESTER_NAMES:
        raise ValueError("软件不能代签人工确认")
    return name


def review_rows(body):
    """Rows that still need a person. Visible S17/3:2 is not in this list."""
    return [row for row in (body or {}).get("rows") or [] if row.get("needs_user")]


def record_row_decision(body, field_id, *, decision, attested_by, notes=None,
                        recorded_by="software-recorder"):
    """Record one human check of an uncertain row. Never accepts this table.

    Confirming the public 8-deck candidate does not copy 8 onto n_decks.
    """
    if decision not in ROW_DECISIONS:
        raise ValueError("行判定只接受 matches_this_table / does_not_match_this_table / still_uncertain")
    name = _require_human_attester(attested_by)
    payload = dict(body)
    payload["accepted"] = False
    payload["passed"] = False
    payload["applies_to_live_table"] = False
    payload["n_decks"] = None
    rows = [dict(row) for row in payload.get("rows") or []]
    found = None
    for row in rows:
        if row.get("id") == field_id:
            found = row
            break
    if found is None:
        raise ValueError(f"未知规则行: {field_id}")
    found["user_decision"] = decision
    found["user_decision_by"] = name
    found["needs_user"] = decision == ROW_DECISION_UNSURE
    found["defaulted"] = False
    found["applies_to_this_table"] = False
    payload["rows"] = rows
    history = list(payload.get("row_decisions") or [])
    history.append({
        "id": field_id,
        "decision": decision,
        "attested_by": name,
        "recorded_by": (recorded_by or "software-recorder").strip() or "software-recorder",
        "notes": notes,
        "accepted": False,
        "copied_n_decks": False,
    })
    payload["row_decisions"] = history
    payload["uncertain"] = [
        row["id"] for row in rows
        if row.get("status") != STATUS_VISIBLE or row.get("needs_user")
    ]
    return payload


def write_diff(path, body):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(body)
    payload["accepted"] = False
    payload["passed"] = False
    payload["applies_to_live_table"] = False
    payload["n_decks"] = None
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
