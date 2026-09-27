"""Immutable side-bet records; originals remain readable and original DB gates recomputation."""
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

from ..analysis.contracts import canonical,digest
from ..analysis.sidebets.contracts import SidebetProfile
from ..analysis.sidebets.information import build_input,compute,RESULT_SCHEMA
from ..analysis.sidebets.exact import ENGINE
from ..ledger.ledger import EventLedger
from .database import LocalStore
from .safe_files import atomic_write
from .history_catalog import metadata_page

SCHEMA='hakimi-sidebet-snapshot-v1'
ROOT=Path(__file__).resolve().parents[2]
SOURCES=('blackjack_lab/analysis/sidebets/contracts.py','blackjack_lab/analysis/sidebets/exact.py',
         'blackjack_lab/analysis/sidebets/information.py','blackjack_lab/analysis/sidebets/research.py',
         'blackjack_lab/ledger/card_inventory.py')


def algorithm_manifest():
    return {name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}


def _hex(value,n=32):
    if type(value) is not str or not re.fullmatch('[a-f0-9]{'+str(n)+'}',value):raise ValueError('边注记录ID或摘要无效')


def validate(body):
    if type(body) is not dict or body.get('schema')!=SCHEMA:raise ValueError('边注记录格式无效')
    _hex(body.get('snapshot_id'))
    if body.get('recomputed_from') is not None:_hex(body['recomputed_from'])
    if body.get('prediction_id') is not None:_hex(body['prediction_id'])
    if body.get('timing') not in ('captured_predeal','observed','historical_recompute'):
        raise ValueError('边注记录时点无效')
    sources=body.get('algorithm_manifest')
    if type(sources) is not dict or set(sources)!=set(SOURCES):raise ValueError('边注算法来源缺失')
    for value in sources.values():_hex(value,64)
    result=body.get('result')
    if type(result) is not dict or result.get('schema')!=RESULT_SCHEMA or result.get('engine_version')!=ENGINE:
        raise ValueError('边注结果或版本无效')
    _hex(result.get('request_id'))
    for value,stamp in ((body.get('saved_at'),True),(body.get('captured_at'),True),
                        (result.get('created_at'),True),(result.get('elapsed_seconds'),False)):
        if type(value) not in (int,float) or not math.isfinite(value) or value<0:raise ValueError('边注时间无效')
        if stamp:datetime.fromtimestamp(value)
    original=result.get('input');prefix=body.get('event_prefix')
    if type(original) is not dict or type(prefix) is not list or not prefix:raise ValueError('边注原输入或前缀缺失')
    if digest(original)!=result.get('input_digest') or digest(prefix)!=original.get('prefix_digest'):
        raise ValueError('边注输入/前缀摘要不符')
    if prefix[-1]['seq']!=original.get('through_seq'):raise ValueError('边注前缀长度不符')
    ledger=EventLedger.from_list(original['session_id'],prefix)
    profile=SidebetProfile.from_dict(original['profile'])
    if original['purpose']=='corrected_predeal':
        from ..analysis.sidebets.research import build_corrected_input
        rebuilt=build_corrected_input(ledger,original['origin'],profile)
        if body['timing']!='historical_recompute':raise ValueError('后来信息研究不能冒充当时预测')
    else:rebuilt=build_input(ledger,original['seat'],profile,original['purpose'])
    if canonical(rebuilt)!=canonical(original):raise ValueError('边注原输入与完整前缀重放不一致')
    if body['timing']=='captured_predeal' and original['purpose']!='forecast':raise ValueError('观察结果不能冒充发牌前预测')
    if body['timing']=='observed' and original['purpose']!='observed':raise ValueError('预测不能冒充实际牌型')
    if body['timing']=='historical_recompute' and body.get('recomputed_from') is None:raise ValueError('历史复算缺来源身份')
    if result.get('status') not in ('available','unavailable'):raise ValueError('边注结果状态无效')
    expected=compute(rebuilt)
    status='available' if any(v['status']=='available' for v in expected['bets'].values()) else 'unavailable'
    if canonical(result.get('output'))!=canonical(expected) or result['status']!=status:
        raise ValueError('边注结果与原组成/原赔付不符')
    return rebuilt


class SidebetSnapshots:
    def __init__(self,directory):self.directory=Path(directory)

    def page(self,**kwargs):return metadata_page(self.directory,**kwargs)

    def save(self,result,event_prefix,timing,recomputed_from=None,prediction_id=None,sources=None,captured_at=None):
        body=copy.deepcopy(dict(schema=SCHEMA,snapshot_id=uuid.uuid4().hex,saved_at=time(),
            result=result,event_prefix=event_prefix,timing=timing,recomputed_from=recomputed_from,
            captured_at=result['created_at'] if captured_at is None else captured_at,
            prediction_id=prediction_id,algorithm_manifest=sources or algorithm_manifest()))
        validate(body)
        saved={**body,'content_digest':digest(body)}
        atomic_write(self.directory/(body['snapshot_id']+'.json'),canonical(saved).encode('utf-8'),overwrite=False)
        return saved

    def load(self,snapshot_id):
        _hex(snapshot_id)
        saved=json.loads((self.directory/(snapshot_id+'.json')).read_text(encoding='utf-8'))
        if type(saved) is not dict or saved.get('snapshot_id')!=snapshot_id:raise ValueError('边注文件身份不符')
        if digest({k:v for k,v in saved.items() if k!='content_digest'})!=saved.get('content_digest'):
            raise ValueError('边注文件内容摘要不符')
        validate(saved);return saved

    def list(self,cancelled=lambda:False):
        entries,damaged=[],[]
        for path in self.directory.glob('*.json'):
            if cancelled():break
            try:entries.append(self.load(path.stem))
            except Exception as error:damaged.append(dict(file=path.name,error=str(error)))
        return sorted(entries,key=lambda r:(r['saved_at'],r['snapshot_id'])),damaged

    @staticmethod
    def verified_input(saved,db_path):
        if digest({k:v for k,v in saved.items() if k!='content_digest'})!=saved.get('content_digest'):
            raise ValueError('边注文件内容摘要不符')
        snapshot=validate(saved);path=Path(db_path).resolve()
        if not path.is_file():raise ValueError('原数据库不存在；原结果可读，不能核验复算')
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as connection:
                connection.row_factory=sqlite3.Row
                rows=connection.execute('SELECT * FROM events WHERE session_id=? AND seq<=? ORDER BY seq',
                    (snapshot['session_id'],snapshot['through_seq'])).fetchall()
                actual=[LocalStore._decode(row).to_dict() for row in rows]
        except sqlite3.Error as error:raise ValueError('原数据库不能只读核验：'+str(error)) from error
        if actual!=saved['event_prefix']:raise ValueError('原数据库前缀不符，不能复算')
        return snapshot

    @staticmethod
    def current_ledger_for(saved,db_path):
        original=SidebetSnapshots.verified_input(saved,db_path)
        path=Path(db_path).resolve()
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=.2)) as connection:
            connection.row_factory=sqlite3.Row
            rows=connection.execute('SELECT * FROM events WHERE session_id=? ORDER BY seq',(original['session_id'],)).fetchall()
            actual=[LocalStore._decode(row).to_dict() for row in rows]
        if [e for e in actual if e['seq']<=original['through_seq']]!=saved['event_prefix']:
            raise ValueError('原数据库在核验期间已变化')
        return EventLedger.from_list(original['session_id'],actual)
