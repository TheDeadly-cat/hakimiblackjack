"""Independent immutable opening records, with captured and authoritative prefixes distinguished."""
import copy
from contextlib import closing
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
from time import time
import uuid

from ..analysis.contracts import canonical, digest
from ..analysis.opening import OpeningInput, SCHEMA, ENGINE, STRATEGY, build_opening_input
from ..analysis.opening_service import validate_opening_result
from ..ledger.ledger import EventLedger
from .database import LocalStore
from .safe_files import atomic_write

SNAPSHOT_SCHEMA = 'hakimi-opening-snapshot-v1'
STATUSES = ('available', 'cancelled', 'stale', 'timeout', 'failed', 'unsupported')
ROOT = Path(__file__).resolve().parents[2]
SOURCES = ('blackjack_lab/analysis/opening.py', 'blackjack_lab/analysis/opening_service.py',
           'blackjack_lab/analysis/native/SplitEngine.cs')


def algorithm_manifest():
    return {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def _hex(value, size):
    if type(value) is not str or not re.fullmatch('[0-9a-f]{'+str(size)+'}', value):
        raise ValueError('开局记录摘要或ID无效')


def _number(value, label, timestamp=False):
    if type(value) not in (int,float) or not math.isfinite(value) or value < 0:
        raise ValueError(label+'必须为非负有限数值')
    if timestamp:
        datetime.fromtimestamp(value)


def _validate(body):
    if type(body) is not dict or body.get('schema') != SNAPSHOT_SCHEMA:
        raise ValueError('开局存储格式不支持')
    _hex(body.get('snapshot_id'), 32)
    _number(body.get('saved_at'), '保存时间', True)
    if body.get('recomputed_from') is not None:
        _hex(body['recomputed_from'], 32)
    result = body.get('result')
    if type(result) is not dict or result.get('schema') != SCHEMA or result.get('status') not in STATUSES:
        raise ValueError('开局结果格式或状态无效')
    snapshot = OpeningInput.from_dict(result.get('input'))
    if (result.get('input_digest') != snapshot.input_digest or result.get('rules_digest') != snapshot.rules_digest
            or result.get('engine_version') != ENGINE or result.get('strategy_version') != STRATEGY
            or type(result.get('request_id')) is not str or not result['request_id']):
        raise ValueError('开局结果身份不符')
    _number(result.get('created_at'), '计算时间', True)
    _number(result.get('elapsed_seconds'), '计算耗时')
    sources = body.get('algorithm_manifest')
    if type(sources) is not dict or set(sources) != set(SOURCES):
        raise ValueError('开局算法源码清单缺失')
    for value in sources.values():
        _hex(value, 64)
    native_source = sources[SOURCES[-1]]
    if result.get('native_source_digest') != native_source:
        raise ValueError('开局数值结果与保存的算法源码不一致')
    if result['status'] == 'available':
        _hex(result.get('native_binary_digest'), 64)
        validate_opening_result(result, snapshot, expected_native_source=native_source)
    else:
        if any(key in result for key in ('ev', 'advantage_percent', 'histogram', 'samples', 'interval',
                                         'radius', 'sign', 'standard_error', 'sample_variance')):
            raise ValueError('未完成的开局请求不能保留可用数值或部分样本')
        if type(result.get('reason')) is not str or not result['reason']:
            raise ValueError('未完成开局请求缺少原因')
    prefix = body.get('event_prefix')
    if type(prefix) is not list or not prefix or digest(prefix) != snapshot.prefix_digest:
        raise ValueError('开局原事件前缀摘要不符')
    ledger = EventLedger.from_list(snapshot.session_id, prefix)
    if prefix[-1]['seq'] != snapshot.through_seq:
        raise ValueError('开局原事件前缀长度不符')
    rebuilt = build_opening_input(ledger, snapshot.participants, snapshot.participants[snapshot.focal])
    if rebuilt.input_digest != snapshot.input_digest:
        raise ValueError('保存的开局输入与原事件前缀重放不一致')
    return snapshot


class OpeningSnapshots:
    def __init__(self, directory):
        self.directory = Path(directory)

    def save(self, result, event_prefix, recomputed_from=None, sources=None):
        body = copy.deepcopy(dict(schema=SNAPSHOT_SCHEMA, snapshot_id=uuid.uuid4().hex, saved_at=time(),
                                  recomputed_from=recomputed_from, result=result, event_prefix=event_prefix,
                                  algorithm_manifest=algorithm_manifest() if sources is None else sources))
        _validate(body)
        envelope = {**body, 'content_digest': digest(body)}
        atomic_write(self.directory/(body['snapshot_id']+'.json'), canonical(envelope).encode('utf-8'), overwrite=False)
        return envelope

    def load(self, snapshot_id):
        _hex(snapshot_id, 32)
        data = json.loads((self.directory/(snapshot_id+'.json')).read_text(encoding='utf-8'))
        if type(data) is not dict or data.get('snapshot_id') != snapshot_id:
            raise ValueError('开局快照ID与文件名不符')
        _hex(data.get('content_digest'), 64)
        body = {k:v for k,v in data.items() if k != 'content_digest'}
        if digest(body) != data['content_digest']:
            raise ValueError('开局快照内容摘要不符')
        _validate(body)
        return data

    def list(self):
        entries, damaged = [], []
        for path in self.directory.glob('*.json'):
            try:
                entries.append(self.load(path.stem))
            except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as error:
                damaged.append(dict(file=path.name, error=str(error)))
        return sorted(entries, key=lambda item:(item['saved_at'], item['snapshot_id'])), damaged

    @staticmethod
    def verified_input(saved, db_path):
        """Embedded prefix makes the record readable; only the original DB verifies replay authority."""
        if digest({k:v for k,v in saved.items() if k != 'content_digest'}) != saved.get('content_digest'):
            raise ValueError('开局快照内容摘要不符')
        snapshot = _validate(saved)
        path = Path(db_path).resolve()
        if not path.is_file():
            raise ValueError('原数据库不存在；仅可阅读保存结果，不能核验或复算原时点')
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=.2)) as connection:
                connection.row_factory = sqlite3.Row
                rows = connection.execute('SELECT * FROM events WHERE session_id=? AND seq<=? ORDER BY seq',
                                          (snapshot.session_id, snapshot.through_seq)).fetchall()
                actual = [LocalStore._decode(row).to_dict() for row in rows]
        except sqlite3.Error as error:
            raise ValueError('原数据库无法只读核验：'+str(error)) from error
        if actual != saved['event_prefix']:
            raise ValueError('原数据库前缀不匹配；仅可阅读保存结果，不能复算')
        ledger = EventLedger.from_list(snapshot.session_id, actual)
        return build_opening_input(ledger, snapshot.participants, snapshot.participants[snapshot.focal])
