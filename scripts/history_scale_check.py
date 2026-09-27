"""Supplement the frozen edge history probe with main/opening file-count scales."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
from time import perf_counter,sleep
import uuid
from unittest.mock import patch


def write(path,data):path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
def hashfile(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--fixtures',type=Path,required=True);parser.add_argument('--prepare',action='store_true')
    parser.add_argument('--output',type=Path);args=parser.parse_args()
    runtime=args.runtime.resolve();fixtures=args.fixtures.resolve()
    build=json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for name,h in build['package_manifest'].items():assert hashfile(runtime/name)==h,name
    sys.path.insert(0,str(runtime));sys.dont_write_bytecode=True
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
    from blackjack_lab.analysis.information import build_input
    from blackjack_lab.analysis.service import calculate
    from blackjack_lab.analysis.opening import build_opening_input
    from blackjack_lab.analysis.opening_service import terminal_opening_result
    from blackjack_lab.analysis.contracts import digest
    from blackjack_lab.storage.database import LocalStore
    from blackjack_lab.storage.analysis_snapshots import AnalysisSnapshots
    from blackjack_lab.storage.opening_snapshots import OpeningSnapshots
    from blackjack_lab.ui.app import BlackjackLabApp
    from scripts.process_metrics import ProcessMetrics
    if args.prepare:
        fixtures.mkdir(parents=True,exist_ok=False)
        b=EventLedger('three-history-scale-fixture');b.start_session('Synthetic supplemental history-count fixture')
        b.create_shoe(ace_peek_das_research_rules(8))
        for e in b.events:e.source=SOURCE_SIMULATOR
        opening=build_opening_input(b,['玩家1'],'玩家1')
        first=OpeningSnapshots(fixtures/'opening-template').save(terminal_opening_result(opening,'scale-cancelled','cancelled','Synthetic terminal history fixture'),b.to_list())
        b.start_round(['玩家1']);b.deal('玩家1','10',suit='S');b.deal('庄家','7',suit='D')
        b.deal('玩家1','9',suit='H');b.deal('庄家',hidden=True)
        for e in b.events:e.source=SOURCE_SIMULATOR
        store=LocalStore(fixtures/'history.db');store.save_ledger(b);store.close()
        second=AnalysisSnapshots(fixtures/'main-template').save(calculate(build_input(b,'玩家1')))
        assert second['result']['status']=='available'
        for kind,template in [('opening',first),('main',second)]:
            directory=fixtures/kind;directory.mkdir()
            for i in range(5000):
                saved=copy.deepcopy(template);saved['snapshot_id']=uuid.uuid5(uuid.NAMESPACE_URL,f'hakimi-{kind}-history:{i}').hex
                saved['saved_at']+=i*.001;saved['content_digest']=digest({k:v for k,v in saved.items() if k!='content_digest'})
                write(directory/(saved['snapshot_id']+'.json'),saved)
        manifest={p.relative_to(fixtures).as_posix():hashfile(p) for p in fixtures.rglob('*') if p.is_file()}
        write(fixtures/'MANIFEST.json',dict(source_commit=build['source_commit'],scales=[100,1000,5000],files=manifest,
            note='Additional immutable inputs: available main result and cancelled opening result. Includes full original prefixes and validated original templates; does not measure successful opening Monte Carlo here.'))
        print(json.dumps(dict(prepared=True,files=len(manifest))));return
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False);rows=[];app=None
    manifest=json.loads((fixtures/'MANIFEST.json').read_text(encoding='utf-8'))
    def close():
        nonlocal app
        if app is None:return
        if hasattr(app,'exit_flow'):
            from scripts.tk_lifecycle import close_app
            close_app(app)
        else:app.on_close()
        app=None
    try:
        for kind in ('main','opening'):
            for count in manifest['scales']:
                case=out/f'{kind}-{count}';case.mkdir();db=case/'history.db';shutil.copy2(fixtures/'history.db',db)
                directory=Path(str(db)+('.analysis' if kind=='main' else '.opening'));directory.mkdir()
                for path in sorted((fixtures/kind).glob('*.json'))[:count]:shutil.copy2(path,directory/path.name)
                originals={p.name:hashfile(p) for p in directory.glob('*.json')}
                with patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True):
                    app=BlackjackLabApp(db,auto_analysis=False)
                app.update();metrics=ProcessMetrics()
                start=perf_counter()
                owner=app.analysis_panel if kind=='main' else app.opening_estimate
                owner.show_history();app.update_idletasks();first=perf_counter()-start
                window=getattr(owner,'history',None)
                deadline=perf_counter()+180
                while window is not None and perf_counter()<deadline:
                    app.update();metrics.sample()
                    if (kind=='main' and window.saved is not None or kind=='opening' and not window._loading and not getattr(window,'_verifying',False)):break
                    sleep(.005)
                assert perf_counter()<deadline
                elapsed=perf_counter()-start
                if window is None:window=next(w for w in app.winfo_children() if w.winfo_class()=='Toplevel')
                listing=next(w for w in window.winfo_children() if w.winfo_class()=='Listbox')
                row=dict(kind=kind,snapshot_files=count,first_feedback_seconds=first,selected_complete_seconds=elapsed,
                    displayed_rows=listing.size(),resources=metrics.summary())
                close();assert all(hashfile(directory/n)==h for n,h in originals.items())
                row['original_files_unchanged']=True;rows.append(row);write(out/'progress.json',rows);print(json.dumps(row),flush=True)
        for n,h in manifest['files'].items():assert hashfile(fixtures/n)==h,n
        for n,h in build['package_manifest'].items():assert hashfile(runtime/n)==h,n
        write(out/'report.json',dict(passed=True,source_commit=build['source_commit'],fixture_manifest_sha256=hashfile(fixtures/'MANIFEST.json'),rows=rows))
    except Exception as error:write(out/'failure.json',dict(error=repr(error),rows=rows));raise
    finally:close()


if __name__=='__main__':main()
