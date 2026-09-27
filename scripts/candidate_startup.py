"""Prepare the existing source-bound accelerator before rapid GUI recording starts."""
import argparse
import hashlib
import json
import multiprocessing
from pathlib import Path
import sys
from time import perf_counter,time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def prepare_runtime():
    from blackjack_lab.analysis.native_backend import build_native,native_cache_directory,source_digest
    start=perf_counter();cache=native_cache_directory()
    existed=(cache/'SplitEngine.exe').is_file() and (cache/'build.json').is_file()
    try:
        path=build_native()
        result=dict(status='available',source_sha256=source_digest(),binary_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    executable=str(path),cache_existed=existed)
    except Exception as error:result=dict(status='unavailable',reason=str(error),cache_existed=existed)
    result.update(schema='hakimi-startup-preparation-v1',created_at=time(),seconds=perf_counter()-start,
                  scope='Startup preparation only; existing exact-request 5-second and opening 20-second budgets unchanged')
    return result


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--db',type=Path,required=True);args=parser.parse_args()
    sys.dont_write_bytecode=True
    receipt=prepare_runtime()
    from blackjack_lab.storage.safe_files import atomic_write
    directory=Path(str(args.db.resolve())+'.startup')
    try:atomic_write(directory/(uuid.uuid4().hex+'.json'),json.dumps(receipt,ensure_ascii=False,indent=2).encode('utf-8'),overwrite=False)
    except OSError:pass  # A receipt disk error cannot make the recorder unavailable.
    from blackjack_lab.ui.app import BlackjackLabApp
    app=BlackjackLabApp(args.db)
    if receipt['status']!='available':
        app.set_status('数值程序尚未准备，仍可录牌；'+receipt['reason'])
    app.mainloop()


if __name__=='__main__':
    multiprocessing.freeze_support();main()
