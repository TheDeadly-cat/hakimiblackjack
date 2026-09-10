"""Frozen 300-case cold-request split matrix plus dynamic two-hand prefixes."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from blackjack_lab.analysis.native_backend import build_native
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import AnalysisService
from blackjack_lab.analysis.split_contracts import HARD_BUDGET_SECONDS, P95_TARGET_SECONDS, split_research_rules
from tests.test_analysis_integration import example
from scripts.source_identity import source_identity


def manifest():
    paths = list((ROOT/'blackjack_lab').rglob('*.py'))+list((ROOT/'blackjack_lab').rglob('*.cs'))
    paths += [Path(__file__),ROOT/'tests/test_analysis_integration.py']
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def cases():
    ranks=('A','2','3','4','5','6','7','8','9','10')
    for decks in (6,7,8):
        for pair in ranks:
            for up in ranks:
                yield f'pre-{decks}-{pair}-{up}',build_input(example(decks,cards=(pair,pair),up=up,rules=split_research_rules(decks)),'玩家1')
    for decks in (6,7,8):
        ledger=example(decks,cards=('8','8'),up='6',rules=split_research_rules(decks))
        first=build_input(ledger,'玩家1').hand_id
        ledger.player_action('玩家1',first,'分牌')
        yield f'dynamic-{decks}-forced-first',build_input(ledger,'玩家1')
        ledger.deal('玩家1','10',hand_id=first)
        yield f'dynamic-{decks}-first-18',build_input(ledger,'玩家1')
        ledger.deal('玩家1','10',hand_id=first)
        yield f'dynamic-{decks}-first-bust',build_input(ledger,'玩家1')
        second=build_input(ledger,'玩家1').active_hand_id
        ledger.deal('玩家1','9',hand_id=second)
        yield f'dynamic-{decks}-second-17',build_input(ledger,'玩家1')
        ledger.player_action('玩家1',second,'停牌')
        yield f'dynamic-{decks}-complete',build_input(ledger,'玩家1')
        ledger=example(decks,cards=('A','A'),up='10',rules=split_research_rules(decks))
        first=build_input(ledger,'玩家1').hand_id
        ledger.player_action('玩家1',first,'分牌')
        ledger.deal('玩家1','10',hand_id=first)
        second=build_input(ledger,'玩家1').active_hand_id
        ledger.deal('玩家1','10',hand_id=second)
        yield f'dynamic-{decks}-split-aces-21',build_input(ledger,'玩家1')


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--compare-dir',type=Path,help='完整旧搜索的318项回执目录；按实际模型字段对齐并逐格差分')
    args=parser.parse_args()
    output=args.output or ROOT/'.local-evidence'/('split-cold-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True,exist_ok=False)
    before=manifest()
    identity=source_identity(ROOT)
    start=time.perf_counter();exe=build_native()
    preparation=time.perf_counter()-start
    rows=[]
    for name,snapshot in cases():
        service=AnalysisService()
        started=time.perf_counter()
        try:
            service.start(snapshot,HARD_BUDGET_SECONDS)
            while True:
                result=service.poll()
                if result is not None:
                    break
                time.sleep(.003)
            elapsed=time.perf_counter()-started
            row=dict(name=name,status=result['status'],wall_seconds=elapsed,input_digest=snapshot.input_digest,
                     compute_seconds=result['elapsed_seconds'],reason=result['reason'],
                     peak_native_bytes=result.get('peak_memory'),peak_python_bytes=result.get('worker_peak_working_set_bytes'),
                     result_file=name+'.json')
            if args.compare_dir:
                baseline_path=args.compare_dir/row['result_file']
                baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
                def model(info):
                    return {**{k:info[k] for k in ('n_decks','rules_json','counts','dealer_up','peek_negative','legal_actions','uncertain_actions')},
                            'hands':[{k:h[k] for k in ('ranks','origin_ranks','closed','forced_draw','split_ace','bet_units')} for h in info['hands']]}
                # JSON-normalize tuple/list; opaque event IDs differ between runs.
                same=json.loads(json.dumps(model(result['input'])))==model(baseline['input'])
                differences=[]
                compatible=same and result['status']==baseline['status']=='available' and set(result['actions'])==set(baseline['actions'])
                if compatible:
                    for action,item in result['actions'].items():
                        previous=baseline['actions'][action]
                        compatible=compatible and item['status']==previous['status']
                        if item['status']=='available' and previous['status']=='available':
                            differences.append(abs(item['ev']-previous['ev']))
                            for field in ('net_distribution','joint_distribution'):
                                if field not in item and field not in previous:
                                    continue
                                compatible=compatible and set(item.get(field,{}))==set(previous.get(field,{}))
                                differences.extend(abs(p-previous[field][key]) for key,p in item.get(field,{}).items() if key in previous.get(field,{}))
                row.update(reference_result_sha256=hashlib.sha256(baseline_path.read_bytes()).hexdigest(),
                           same_model=same,comparison_passed=compatible and max(differences,default=1.)<=1e-10,
                           max_abs_error=max(differences,default=None))
            (output/row['result_file']).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            rows.append(row)
            with (output/'progress.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(row,ensure_ascii=False)+'\n')
            if len(rows)%10==0 or result['status']!='available':
                print(f"{len(rows)}/318 {name}: {result['status']} {elapsed:.3f}s",flush=True)
        finally:
            service.close()
    times=sorted(r['wall_seconds'] for r in rows)
    p95=times[math.ceil(.95*len(times))-1]
    receipt=dict(schema='hakimi-split-cold-matrix-v1',timestamp_utc=datetime.now(timezone.utc).isoformat(),
        identity=identity,python=sys.version,platform=platform.platform(),source_manifest=before,
        source_unchanged=before==manifest(),cases=rows,count=len(rows),p50_seconds=statistics.median(times),
        p95_seconds=p95,max_seconds=max(times),p95_method='nearest rank',target_p95_seconds=P95_TARGET_SECONDS,
        hard_budget_seconds=HARD_BUDGET_SECONDS,preparation_seconds=preparation,
        binary_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),
        statuses={s:sum(r['status']==s for r in rows) for s in sorted({r['status'] for r in rows})},
        target_met=p95<=P95_TARGET_SECONDS,all_completed=all(r['status']=='available' for r in rows),
        peak_combined_process_bytes=max((r['peak_native_bytes'] or 0)+(r['peak_python_bytes'] or 0) for r in rows),
        scope='Synthetic finite shared-shoe requests; artifact reuse only, no probability/policy cache between requests; no user data')
    receipt['passed']=receipt['target_met'] and receipt['all_completed'] and receipt['source_unchanged']
    if args.compare_dir:
        receipt['comparison_directory']=str(args.compare_dir.resolve())
        receipt['comparison_passed']=all(r['comparison_passed'] for r in rows)
        receipt['max_abs_error']=max((r['max_abs_error'] for r in rows if r['max_abs_error'] is not None),default=None)
        receipt['passed']=receipt['passed'] and receipt['comparison_passed']
    (output/'receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:receipt[k] for k in ('count','p50_seconds','p95_seconds','max_seconds','statuses','passed')}),flush=True)
    print(output,flush=True)
    return 0 if receipt['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
