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
        _validate_split_result(result)
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


def _validate_split_result(result):
    from ..analysis.split_contracts import is_das_engine, SplitAnalysisInput
    from ..analysis.split_service import DAS_JOINT_KEYS, DAS_TOTAL_NETS, SPLIT_ACTION_ZH
    try:
        snapshot = SplitAnalysisInput.from_dict(result['input'])
        snapshot.validate()
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
        raise SnapshotFormatError('result.input：无效分牌输入：'+str(error)) from error
    _require(result['rules_digest'] == snapshot.rules_digest, 'rules_digest', '与规则快照不一致')
    if result['status'] != 'available':
        return
    das = is_das_engine(snapshot.engine_version)
    expected_current = 1 if snapshot.pre_split else sum(h.bet_units for h in snapshot.hands)
    _require(type(result.get('current_investment')) is int and result['current_investment'] == expected_current,
             'current_investment', '原注/分牌总投入不一致')
    actions = result.get('actions')
    _require(isinstance(actions, dict), 'actions', '必须为对象')
    _require(set(snapshot.legal_actions).issubset(actions), 'actions', '缺少当前合法动作')
    joint_keys = DAS_JOINT_KEYS if das else {f'{a},{b}' for a in (-1, 0, 1) for b in (-1, 0, 1)}
    total_nets = DAS_TOTAL_NETS if das else {-2., -1., 0., 1., 2.}
    lo, hi = (-4, 4) if das else (-2, 2)
    for action,item in actions.items():
        _require(action in SPLIT_ACTION_ZH and isinstance(item, dict), 'actions', '动作格式无效')
        status = item.get('status')
        _require(isinstance(status, str) and status in STATUS_ZH, 'actions.status', '状态无效')
        if status != 'available':
            continue
        for name in ('ev', 'additional_investment', 'total_investment'):
            _number(item.get(name), 'actions.'+name)
        if das:
            added = 1 if action == 'double' or (snapshot.pre_split and action in ('split', 'double')) else 0
        else:
            added = 1 if snapshot.pre_split and action in ('split','double') else 0
        _require(item['additional_investment']==added and item['total_investment']==result['current_investment']+added,
                 'actions.total_investment','原注与追加注口径不一致')
        if das:
            future = item.get('possible_future_additional')
            ceiling = item.get('max_final_investment')
            _require(type(future) is int and 0 <= future <= 2, 'possible_future_additional', '必须为0到2的整数上界')
            _require(type(ceiling) is int and ceiling == result['current_investment'] + added + future,
                     'max_final_investment', '最终投入上界不一致')
        distribution = item.get('net_distribution')
        _require(isinstance(distribution, dict) and bool(distribution), 'net_distribution', '必须为收益对象')
        total = expectation = 0.
        two_hand = not snapshot.pre_split or action == 'split'
        for net,p in distribution.items():
            _number(p,'net_distribution.probability')
            try:
                outcome = float(net)
            except (ValueError, TypeError) as error:
                raise SnapshotFormatError('net_distribution：收益键无效') from error
            _number(outcome,'net_distribution.net')
            _require(p>=0 and lo<=outcome<=hi, 'net_distribution', '收益或概率越界')
            if two_hand:
                allowed = set(range(-4, 5)) if das else (-2, -1, 0, 1, 2)
                _require(outcome == int(outcome) and int(outcome) in allowed, 'net_distribution', '两手净收益不能有半注或BJ收益')
            total += p
            expectation += outcome*p
        _require(abs(total-1)<=1e-10 and abs(expectation-item['ev'])<=1e-10, 'net_distribution', '概率和或EV不一致')
        if two_hand:
            hand_evs = item.get('hand_evs')
            _require(isinstance(hand_evs,(list,tuple)) and len(hand_evs)==2, 'hand_evs', '必须包含两手边际')
            for value in hand_evs:
                _number(value,'hand_evs[]')
            _require(abs(sum(hand_evs)-item['ev'])<=1e-10, 'hand_evs', '与总EV不一致')
            joint = item.get('joint_distribution')
            _require(isinstance(joint,dict) and set(joint)==joint_keys,
                     'joint_distribution','必须包含已声明的共享结算格')
            for value in joint.values():
                _number(value,'joint_distribution[]')
                _require(value>=0,'joint_distribution[]','概率不得为负')
            _require(abs(sum(joint.values())-1)<=1e-10,'joint_distribution','概率和必须为1')
            for index in (0,1):
                marginal = sum(int(pair.split(',')[index])*p for pair,p in joint.items())
                _require(abs(marginal-hand_evs[index])<=1e-10,'joint_distribution','边际EV不匹配')
            totals = {float(k):p for k,p in distribution.items()}
            _require(set(totals)==total_nets,'net_distribution','需要完整的合计收益格')
            for net,p in totals.items():
                expected = sum(pj for pair,pj in joint.items() if sum(map(int,pair.split(',')))==net)
                _require(abs(p-expected)<=1e-10,'joint_distribution','与合计收益分布不匹配')
    highest = result.get('highest_ev_action')
    _require(highest is None or isinstance(highest,str) and highest in actions, 'highest_ev_action', '动作无效')
    probabilities = result.get('probabilities')
    _require(isinstance(probabilities,dict), 'probabilities', '必须为对象')
    if 'next_target_draw' in probabilities:
        _require(isinstance(probabilities['next_target_draw'],dict),'next_target_draw','必须为对象')
        for value in probabilities['next_target_draw'].values():
            _number(value,'next_target_draw[]')
        _number(probabilities.get('hit_bust'),'hit_bust')


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
