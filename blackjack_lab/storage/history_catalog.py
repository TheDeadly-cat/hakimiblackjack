"""Bounded, unverified file metadata pages. Full load/list validators stay authoritative."""
import json
import math
import re
from pathlib import Path


def metadata_page(directory, *, offset=0, limit=100, cancelled=lambda:False):
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('历史页码或页长无效')
    paths=[]
    for path in Path(directory).glob('*.json'):
        if cancelled():return dict(entries=[],damaged=[],total=0,offset=offset)
        try:paths.append((path.stat().st_mtime_ns,path.name,path))
        except FileNotFoundError:continue
    # File ordering is stated in the UI; it is not a claim about trusted result time.
    paths.sort(reverse=True)
    entries,damaged=[],[]
    for _,_,path in paths[offset:offset+limit]:
        if cancelled():break
        try:
            saved=json.loads(path.read_text(encoding='utf-8'))
            if (not isinstance(saved,dict) or saved.get('snapshot_id')!=path.stem
                    or not re.fullmatch('[a-f0-9]{32}',path.stem)):
                raise ValueError('快照根结构或文件身份格式不符')
            stamp=saved.get('saved_at')
            if type(stamp) not in (int,float) or not math.isfinite(stamp) or stamp<0:
                raise ValueError('保存时间格式不符')
            result=saved.get('result',{});snapshot=result.get('input',{})
            entries.append(dict(snapshot_id=path.stem,saved_at=stamp,metadata_only=True,
                through_seq=snapshot.get('through_seq','?'),seat=snapshot.get('seat',''),
                timing=saved.get('timing',''),status=result.get('status','')))
        except (ValueError,TypeError,AttributeError,OSError,RecursionError) as error:
            damaged.append(dict(file=path.name,error=str(error)))
    return dict(entries=entries,damaged=damaged,total=len(paths),offset=offset)
