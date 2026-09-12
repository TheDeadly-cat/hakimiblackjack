"""Adapters for an optional possibly-wrong/blackjack cross-check.

The live solver never imports or executes that project. These helpers only
translate shoe counts, parse strategy.exe text, and expand the frozen case
list. The GPL binary and sources stay outside the product tree.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

DEALER_LABELS = ("blackjack", "17", "18", "19", "20", "21", "bust")
UP_TOKEN = {1: "A", 10: "T", **{n: str(n) for n in range(2, 10)}}
TOKEN_UP = {token: rank for rank, token in UP_TOKEN.items()}
TOKEN_UP["10"] = 10
DEALER_ROW = re.compile(
    r"^\s*(10|[2-9]|A)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s+\|\s+"
    r"([0-9.]+)\s*$"
)
EV_LINE = re.compile(r"^(Stand|Hit|Double|Split|Surrender)\s+E\(X\)\s*=\s*([-+0-9.]+)%")
SCHEMA = "hakimi-external-pw-cases-v1"
DEFAULT_EXE_RELATIVE = Path(".local-evidence") / "external" / "possibly-wrong-v7.6" / "strategy.exe"


def default_strategy_exe(root):
    return Path(root) / DEFAULT_EXE_RELATIVE


def full_shoe(n_decks):
    if type(n_decks) is not int or n_decks not in (6, 7, 8):
        raise ValueError("对照牌副数仅支持 6/7/8")
    return [4 * n_decks] * 9 + [16 * n_decks]


def peek_negative(up):
    return up in (1, 10)


def shoe_counts(spec_shoe):
    kind = spec_shoe["kind"]
    if kind == "ndecks":
        return full_shoe(spec_shoe["decks"])
    if kind == "counts":
        counts = list(spec_shoe["counts"])
        if len(counts) != 10 or any(type(n) is not int or n < 0 for n in counts):
            raise ValueError("自定义牌靴必须是十个非负整数")
        return counts
    raise ValueError("未知牌靴 kind: " + repr(kind))


def hakimi_remaining_from_pw_shoe(pw_shoe, player, dealer_up):
    """Hakimi remaining includes the hole and excludes player cards plus the upcard."""
    counts = list(pw_shoe)
    if len(counts) != 10:
        raise ValueError("牌靴必须是十点值桶")
    if type(dealer_up) is not int or not 1 <= dealer_up <= 10:
        raise ValueError("非法庄家明牌")
    if any(type(v) is not int or not 1 <= v <= 10 for v in player):
        raise ValueError("非法玩家牌")
    for card in (*player, dealer_up):
        counts[card - 1] -= 1
        if counts[card - 1] < 0:
            raise ValueError("possibly-wrong 牌靴缺少已发牌或明牌")
    return counts


def pw_shoe_from_hakimi_remaining(hakimi_counts, player, dealer_up):
    """Inverse of hakimi_remaining_from_pw_shoe. BJPlayer/BJDealer keep the upcard in the shoe."""
    counts = list(hakimi_counts)
    for card in (*player, dealer_up):
        counts[card - 1] += 1
    return counts


def hakimi_dealer_counts_from_pw_shoe(pw_shoe, dealer_up):
    """Dealer-only shoe: still remove the upcard that possibly-wrong leaves undealt in the API."""
    return hakimi_remaining_from_pw_shoe(pw_shoe, (), dealer_up)


def renormalize_pw_dealer_after_peek(distribution):
    """Map P(outcome | up) that still includes blackjack onto the US-peek measure."""
    missing = [label for label in DEALER_LABELS if label not in distribution]
    if missing:
        raise ValueError("庄家分布缺少 " + ",".join(missing))
    bj = float(distribution["blackjack"])
    if bj < -1e-15 or bj >= 1:
        raise ValueError("无法在庄家 BJ 概率上做非 BJ 检查条件化")
    scale = 1.0 / (1.0 - bj)
    out = {"blackjack": 0.0}
    for label in DEALER_LABELS:
        if label == "blackjack":
            continue
        out[label] = float(distribution[label]) * scale
    return out


def max_abs_dealer_error(left, right):
    return max(abs(float(left.get(label, 0)) - float(right.get(label, 0))) for label in DEALER_LABELS)


def load_spec(path):
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    errors = []
    if spec.get("schema") != SCHEMA:
        errors.append("schema")
    engine = spec.get("engine") or {}
    for key in ("release", "source_commit", "strategy_url", "strategy_exe_sha256", "strategy_exe_bytes"):
        if not engine.get(key):
            errors.append("engine")
            break
    sha = engine.get("strategy_exe_sha256")
    if type(sha) is not str or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        errors.append("sha256")
    if spec.get("hands") and spec.get("shoes") and spec.get("dealer_ups"):
        cases = expand_cases(spec)
        ids = [case["id"] for case in cases]
        if len(ids) != len(set(ids)):
            errors.append("duplicate_id")
        expected = spec.get("expected_counts") or {}
        groups = {}
        for case in cases:
            groups[case["group"]] = groups.get(case["group"], 0) + 1
        groups["total"] = len(cases)
        if expected and groups != expected:
            errors.append("case_mismatch")
    else:
        errors.append("empty")
        cases = []
    spec["_load_errors"] = errors
    spec["_cases"] = cases
    return spec


def expand_cases(spec):
    shoes = {item["id"]: item for item in spec["shoes"]}
    cases = []
    for shoe in spec["shoes"]:
        for up in spec["dealer_ups"]:
            cases.append(_dealer_case(shoe, up, "raw"))
            if peek_negative(up):
                cases.append(_dealer_case(shoe, up, "peek"))
    for hand in spec["hands"]:
        for shoe_id in hand["shoes"]:
            cases.append(_ev_case(shoes[shoe_id], hand))
    return cases


def _dealer_case(shoe, up, mode):
    token = UP_TOKEN[up]
    return {
        "id": "dealer-%s-%s-up%s" % (mode, shoe["id"], token),
        "group": "dealer_" + mode,
        "kind": "dealer_dist",
        "mode": mode,
        "comparable": True,
        "incomparable_reason": "",
        "shoe_id": shoe["id"],
        "pw_shoe": shoe_counts(shoe),
        "up": up,
        "player": (),
        "actions": {},
        "note": shoe.get("note", ""),
    }


def _ev_case(shoe, hand):
    actions = dict(hand["actions"])
    incomparable = [name for name, flag in actions.items() if flag == "not_directly_comparable"]
    return {
        "id": "ev-%s-%s" % (shoe["id"], hand["id"]),
        "group": "ev",
        "kind": "unsplit_ev",
        "mode": "ev",
        "comparable": True,
        "incomparable_reason": hand.get("incomparable_reason", "") if incomparable else "",
        "incomparable_actions": incomparable,
        "shoe_id": shoe["id"],
        "pw_shoe": shoe_counts(shoe),
        "up": hand["up"],
        "player": tuple(hand["player"]),
        "actions": actions,
        "note": hand.get("note", ""),
    }


def pw_rule_answers(spec, table_name="pw-table.txt"):
    rules = spec["rules"]
    lines = [
        "n" if not rules["hit_soft_17"] else "y",
        "y" if rules["double_any_total"] else "n",
        "y" if rules["double_soft"] else "n",
        "y" if rules["double_after_hit"] else "n",
        "y" if rules["double_after_split"] else "n",
        "y" if rules["resplit"] else "n",
        "y" if rules["cdz"] else "n",
    ]
    if not rules["cdz"]:
        lines.append("y" if rules["cdp1"] else "n")
    if rules["resplit"]:
        raise ValueError("首批对照固定 SPL1，不在规则输入中询问分 A 再分")
    lines.extend([
        "y" if rules["late_surrender"] else "n",
        str(rules["bj_payoff"]),
        table_name,
    ])
    return lines


def pw_stdin(spec, shoe, queries, table_name="pw-table.txt"):
    lines = []
    if shoe["kind"] == "ndecks":
        lines.append(str(shoe["decks"]))
    else:
        lines.append("0")
        lines.append(" ".join(str(n) for n in shoe_counts(shoe)))
    lines.extend(pw_rule_answers(spec, table_name))
    for up, player in queries:
        lines.append("%d %d %s" % (up, len(player), " ".join(str(c) for c in player)))
    lines.append("0 0")
    return "\n".join(lines) + "\n"


def parse_dealer_table(text):
    rows = {}
    in_table = False
    for line in text.splitlines():
        if "Probability of outcome of dealer's hand" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if line.strip().startswith("Total"):
            break
        match = DEALER_ROW.match(line.rstrip())
        if not match:
            continue
        token, *values = match.groups()
        if token not in TOKEN_UP:
            continue
        numbers = [float(item) for item in values]
        rows[TOKEN_UP[token]] = {
            "blackjack": numbers[6],
            "17": numbers[1],
            "18": numbers[2],
            "19": numbers[3],
            "20": numbers[4],
            "21": numbers[5],
            "bust": numbers[0],
        }
    return rows


def parse_ev_blocks(text):
    blocks = []
    current = None
    for line in text.splitlines():
        match = EV_LINE.match(line.strip())
        if match:
            action = match.group(1).lower()
            value = float(match.group(2)) / 100.0
            if action == "stand" or current is None:
                current = {}
                blocks.append(current)
            current[action] = value
    return blocks


def verify_strategy_exe(path, spec):
    engine = spec["engine"]
    data = Path(path).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    size = engine["strategy_exe_bytes"]
    expected = engine["strategy_exe_sha256"]
    if len(data) != size:
        raise ValueError("strategy.exe 大小为 %s，期望 %s" % (len(data), size))
    if digest != expected:
        raise ValueError("strategy.exe sha256=%s，期望 %s" % (digest, expected))
    return digest
