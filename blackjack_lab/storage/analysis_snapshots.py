"""Immutable derived analysis sidecars. Writes never mutate event SQLite."""
from pathlib import Path
from datetime import datetime
import json
import math
from time import time
import uuid

from ..analysis.contracts import RESULT_SCHEMA, STATUS_ZH, ACTION_ZH, canonical, digest
from .safe_files import atomic_write

SNAPSHOT_SCHEMA = "hakimi-analysis-snapshot-v1"
SUPPORTED_RESULTS = {RESULT_SCHEMA, "hakimi-analysis-result-v2"}


class SnapshotFormatError(ValueError):
    """One snapshot has invalid data; callers can isolate it without hiding bugs."""


def _require(condition, field, description):
    if not condition:
        raise SnapshotFormatError(f"{field}：{description}")


def _text(value, field):
    _require(isinstance(value, str) and bool(value.strip()), field, "必须为非空文本")


def _hex(value, length, field):
    _require(isinstance(value, str) and len(value) == length
             and all(c in "0123456789abcdef" for c in value), field, f"必须为{length}位十六进制文本")


def _number(value, field):
    _require(type(value) in (int, float), field, "必须为有限数值（不能为布尔值）")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    _require(finite, field, "必须为有限数值")


def _content_digest(value, field):
    try:
        return digest(value)
    except (ValueError, RecursionError) as error:
        raise SnapshotFormatError(f"{field}：无法计算规范JSON摘要") from error


def is_minimal_result(result):
    """The original storage API also accepted metadata-only envelopes."""
    return set(result) == {"schema", "input", "input_digest"}


def _validate_result(result):
    _require(isinstance(result, dict), "result", "必须为JSON对象")
    _require(result.get("schema") in SUPPORTED_RESULTS if isinstance(result.get("schema"), str) else False,
             "result.schema", "不支持的结果格式")
    info = result.get("input")
    _require(isinstance(info, dict), "result.input", "必须为JSON对象")
    _hex(result.get("input_digest"), 64, "result.input_digest")
    _require(_content_digest(info, "result.input") == result["input_digest"], "result.input_digest", "输入身份不匹配")
    # Preserve accepted legacy minimal envelopes. They remain readable as metadata,
    # and the history UI does not treat them as computed EV or recomputable input.
    if is_minimal_result(result):
        return
    for name in ("session_id", "shoe_id", "round_id", "seat", "hand_id"):
        _text(info.get(name), "result.input." + name)
    _require(type(info.get("through_seq")) is int and info["through_seq"] > 0,
             "result.input.through_seq", "必须为正整数")
    _require(type(info.get("n_decks")) is int and info["n_decks"] in (6, 7, 8),
             "result.input.n_decks", "必须为6、7或8")
    _hex(info.get("prefix_digest"), 64, "result.input.prefix_digest")
    _hex(result.get("rules_digest"), 64, "result.rules_digest")
    for name in ("engine_version", "strategy_version", "status", "reason"):
        _text(result.get(name), "result." + name)
    _number(result.get("elapsed_seconds"), "result.elapsed_seconds")
    _require(type(result.get("partial_comparison")) is bool, "result.partial_comparison", "必须为布尔值")
    if result["schema"] != RESULT_SCHEMA:
        # Split results share identity fields, but have hands instead of player_ranks.
        _require(info.get("schema") == "hakimi-split-analysis-input-v1", "result.input.schema", "分牌输入格式不匹配")
        _require(isinstance(info.get("hands"), (list, tuple)) and bool(info["hands"]), "result.input.hands", "必须为非空手牌数组")
        for hand in info["hands"]:
            _require(isinstance(hand, dict), "result.input.hands[]", "必须为JSON对象")
            _text(hand.get("hand_id"), "result.input.hands[].hand_id")
        return
    _require(info.get("schema") == "hakimi-analysis-input-v1", "result.input.schema", "单手输入格式不匹配")
    ranks = info.get("player_ranks")
    _require(isinstance(ranks, (list, tuple)) and bool(ranks)
             and all(isinstance(rank, str) and bool(rank) for rank in ranks), "result.input.player_ranks", "必须为非空牌面文本数组")
    _require(type(info.get("dealer_up")) is int and 1 <= info["dealer_up"] <= 10,
             "result.input.dealer_up", "必须为1至10的整数")
    _require(type(info.get("peek_negative")) is bool, "result.input.peek_negative", "必须为布尔值")
    if result["status"] != "available":
        return
    actions = result.get("actions")
    _require(isinstance(actions, dict), "result.actions", "必须为JSON对象")
    for action, item in actions.items():
        _require(action in ACTION_ZH and isinstance(item, dict), "result.actions", "动作名称或内容无效")
        status = item.get("status")
        _require(isinstance(status, str) and status in STATUS_ZH, "result.actions.status", "动作状态无效")
        if status == "available":
            _number(item.get("ev"), "result.actions.ev")
    highest = result.get("highest_ev_action")
    _require(highest is None or isinstance(highest, str) and highest in ACTION_ZH,
             "result.highest_ev_action", "动作名称无效")
    probabilities = result.get("probabilities")
    _require(isinstance(probabilities, dict), "result.probabilities", "必须为JSON对象")
    _number(probabilities.get("hit_bust"), "result.probabilities.hit_bust")
    for name in ("next_target_draw", "dealer_terminal_if_stand_now"):
        values = probabilities.get(name)
        _require(isinstance(values, dict), "result.probabilities." + name, "必须为JSON对象")
        for value in values.values():
            _number(value, "result.probabilities." + name)
    if "probability_status" in result:
        _require(isinstance(result["probability_status"], dict), "result.probability_status", "必须为JSON对象")


class AnalysisSnapshots:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, result, recomputed_from=None):
        _validate_result(result)
        if recomputed_from is not None:
            _hex(recomputed_from, 32, "recomputed_from")
        snapshot_id = uuid.uuid4().hex
        body = {"schema": SNAPSHOT_SCHEMA, "snapshot_id": snapshot_id, "saved_at": time(),
                "recomputed_from": recomputed_from, "result": result}
        envelope = {**body, "content_digest": digest(body)}
        atomic_write(self.directory / (snapshot_id + ".json"), canonical(envelope).encode("utf-8"), overwrite=False)
        return envelope

    def load(self, snapshot_id):
        _hex(snapshot_id, 32, "snapshot_id")
        try:
            data = json.loads((self.directory / (snapshot_id + ".json")).read_text(encoding="utf-8"))
        except (ValueError, UnicodeError, RecursionError) as error:
            raise SnapshotFormatError("文件不是有效UTF-8 JSON：" + str(error)) from error
        _require(isinstance(data, dict), "快照根结构", "必须为JSON对象")
        _require(data.get("schema") == SNAPSHOT_SCHEMA, "schema", "不支持的快照格式")
        _require(data.get("snapshot_id") == snapshot_id, "snapshot_id", "与文件名不匹配")
        _hex(data.get("content_digest"), 64, "content_digest")
        saved_at = data.get("saved_at")
        _number(saved_at, "saved_at")
        try:
            datetime.fromtimestamp(saved_at)
        except (ValueError, OSError, OverflowError) as error:
            raise SnapshotFormatError("saved_at：超出本机可显示时间范围") from error
        if data.get("recomputed_from") is not None:
            _hex(data["recomputed_from"], 32, "recomputed_from")
        _validate_result(data.get("result"))
        body = {key: value for key, value in data.items() if key != "content_digest"}
        _require(_content_digest(body, "快照") == data["content_digest"], "content_digest", "分析快照内容摘要校验失败")
        return data

    def list(self):
        entries, damaged = [], []
        for path in self.directory.glob("*.json"):
            try:
                entries.append(self.load(path.stem))
            except (SnapshotFormatError, OSError) as error:
                damaged.append({"file": path.name, "error": str(error)})
        return sorted(entries, key=lambda e: (e["saved_at"], e["snapshot_id"])), damaged

    @staticmethod
    def matches_prefix(saved, ledger):
        data = saved["result"]["input"]
        if ledger.session_id != data["session_id"]:
            return False
        prefix = [e for e in ledger.to_list() if e["seq"] <= data["through_seq"]]
        return digest(prefix) == data["prefix_digest"]
