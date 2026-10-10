"""Local unresolved recording inputs survive restart; original records are immutable."""
import hashlib
import json
import math
from pathlib import Path
from time import time

from ..storage.safe_files import atomic_write


def load_failures(db_path, session_id):
    root = Path(str(Path(db_path).resolve()) + '.recording-failures')
    output = []
    for path in sorted(root.glob('*.json')):
        if path.name.endswith('.reviewed.json'):
            continue
        try:
            raw = path.read_bytes()
        except OSError as error:
            output.append(dict(status='unknown_commit_outcome', intent={'kind': '核对文件'},
                               error=f'待核对原记录无法读取：{error}', failure_path=str(path)))
            continue
        sha = hashlib.sha256(raw).hexdigest()
        marker = path.with_suffix('.reviewed.json')
        try:
            reviewed = json.loads(marker.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            reviewed = {}
        if (isinstance(reviewed, dict) and reviewed.get('schema') == 'blackjack-recording-review-v1'
                and set(reviewed) == {'schema','failure_sha256','session_id','saved_prefix_sha256',
                                      'saved_through_seq','reviewed_at','automatic_retry'}
                and reviewed.get('automatic_retry') is False
                and type(reviewed.get('saved_through_seq')) is int and reviewed['saved_through_seq'] > 0
                and isinstance(reviewed.get('saved_prefix_sha256'), str) and len(reviewed['saved_prefix_sha256']) == 64
                and type(reviewed.get('reviewed_at')) in (int,float) and math.isfinite(reviewed['reviewed_at'])
                and reviewed.get('failure_sha256') == sha and reviewed.get('session_id') == session_id):
            continue
        try:
            data = json.loads(raw)
            if data['schema'] != 'blackjack-recording-failure-v1':
                raise ValueError('未知录牌核对格式')
            if data['session_id'] != session_id:
                continue
            if (data['status'] not in ('failed_before_commit','not_executed','unknown_commit_outcome')
                    or data['automatic_retry'] is not False
                    or type(data.get('archived_at', 0)) not in (int,float)
                    or not math.isfinite(data.get('archived_at', 0))):
                raise ValueError('待核对记录状态无效')
            intent = data['arguments']['args'][0] if data['operation'] == 'record_input' else {'kind': data['operation']}
            output.append(dict(status=data['status'], intent=intent, error=data['error'],
                               request_id=data['request_id'], failure_path=str(path), failure_sha256=sha,
                               archived_at=data.get('archived_at', 0)))
        except (ValueError, KeyError, IndexError, TypeError):
            output.append(dict(status='unknown_commit_outcome', intent={'kind': '核对文件'},
                               error='本地待核对记录损坏，不能推断已执行或已保存',
                               failure_path=str(path), failure_sha256=sha))
    return sorted(output, key=lambda fault: fault.get('archived_at', 0))


def acknowledge_review(faults, prefix):
    """Explicit readback review only; never changes or retries a recording command."""
    for fault in faults:
        if not fault.get('failure_path'):
            continue  # A UI publication error has no unexecuted raw command to archive.
        path = Path(fault['failure_path'])
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        if fault.get('failure_sha256') and fault['failure_sha256'] != sha:
            raise ValueError('待核对原记录已变化，请重新打开应用核对')
        data = dict(schema='blackjack-recording-review-v1', failure_sha256=sha,
                    session_id=prefix.session_id, saved_prefix_sha256=prefix.prefix_digest,
                    saved_through_seq=prefix.through_seq, reviewed_at=time(), automatic_retry=False)
        atomic_write(path.with_suffix('.reviewed.json'),
                     json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode('utf-8'))
