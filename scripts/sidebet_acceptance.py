"""Frozen dynamic-shoe cases, one fresh Python process per case; no old benchmark relabeling."""
import argparse
from dataclasses import replace
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
from time import perf_counter

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
from blackjack_lab.analysis.sidebets.exact import calculate
from blackjack_lab.analysis.dealer_blackjack import scalar
from scripts.source_identity import source_identity

SPEC=ROOT/'fixtures/acceptance/sidebet_performance_v1.json'


def manifest():
    paths=[*list((ROOT/'blackjack_lab').rglob('*.py')),*list((ROOT/'blackjack_lab').rglob('*.cs')),SPEC,Path(__file__)]
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def composition(decks,case):
    counts=[decks]*52;mode=case['mode']
    if mode=='random':
        physical=[i for i in range(52) for _ in range(decks)]
        random.Random(case['seed']).shuffle(physical)
        remove=case.get('remove',52*decks-case.get('remaining',52*decks))
        for i in physical[:remove]:counts[i]-=1
    elif mode=='cycle':
        for n in range(case['remove']):counts[n%52]-=1
    elif mode=='aces_zero':counts[:4]=[0]*4
    elif mode=='spades_zero':counts=[0 if i%4==0 else n for i,n in enumerate(counts)]
    elif mode=='tens_zero':counts[36:]=[0]*16
    elif mode=='skew':counts=[min(n,(i%4)+1) for i,n in enumerate(counts)]
    elif mode!='full':raise ValueError(mode)
    assert all(0<=n<=decks for n in counts)
    return counts


def run_case(decks,case):
    counts=composition(decks,case);profile=SidebetProfile()
    if 'pair_payout' in case:profile=replace(profile,perfect_pairs=tuple(case['pair_payout']))
    if 'ace_rules' in case:profile=replace(profile,**dict(zip(('a23','qka','ka2'),case['ace_rules'])))
    if 'confirmation' in case:profile=replace(profile,confirmation=case['confirmation'])
    start=perf_counter();result=calculate(counts,profile);seconds=perf_counter()-start
    expected={name:'available' if sum(counts)>=(2 if name=='perfect_pairs' else 3) else 'unavailable' for name in result['bets']}
    for name,bet in result['bets'].items():
        assert bet['status']==expected[name]
        if bet['status']=='available':
            probs={k:Fraction(p) for k,p in bet['fractions'].items()}
            assert sum(probs.values())==1 and min(probs.values())>=0
            assert sum(bet['ways'].values())==bet['combinations']
            assert profile.ev(name,probs)==(None if bet['ev_fraction'] is None else Fraction(bet['ev_fraction']))
    grouped=tuple(sum(counts[4*i:4*i+4]) for i in range(9))+(sum(counts[36:]),)
    bj=None if sum(counts)<2 else str(scalar(grouped))
    return dict(id=f'{decks}d-'+case['id'],decks=decks,pure_seconds=seconds,n=sum(counts),
                expected_statuses=expected,dealer_bj_fraction=bj,result=result,passed=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path)
    parser.add_argument('--case');parser.add_argument('--decks',type=int)
    args=parser.parse_args();spec=json.loads(SPEC.read_text(encoding='utf-8'))
    if args.case:
        case=next(c for c in spec['scenarios'] if c['id']==args.case)
        print(json.dumps(run_case(args.decks,case),ensure_ascii=True));return
    if args.output is None:parser.error('--output is required')
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    source=source_identity(ROOT);assert not source['dirty_worktree'],'freeze a clean commit before measurement'
    files=manifest();rows=[];began=perf_counter()
    for decks in spec['decks']:
        for case in spec['scenarios']:
            start=perf_counter()
            process=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__)),'--decks',str(decks),'--case',case['id']],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=10)
            case_id=f'{decks}d-'+case['id'];(out/(case_id+'.stdout')).write_text(process.stdout,encoding='utf-8')
            (out/(case_id+'.stderr')).write_text(process.stderr,encoding='utf-8')
            if process.returncode:
                rows.append(dict(id=case_id,decks=decks,passed=False,exit_code=process.returncode))
                continue
            row=json.loads(process.stdout);row['process_wall_seconds']=perf_counter()-start;rows.append(row)
    assert len(rows)==spec['expected_count']
    groups={str(d):[r['pure_seconds'] for r in rows if r['decks']==d and r['passed']] for d in spec['decks']}
    p95={d:sorted(values)[math.ceil(.95*len(values))-1] if values else None for d,values in groups.items()}
    assert manifest()==files,'source changed during benchmark'
    report=dict(schema='hakimi-sidebet-performance-v1',source=source,source_manifest=files,
        platform=platform.platform(),python=sys.version,count=len(rows),pure_p95_by_decks=p95,
        pure_target_seconds=spec['pure_p95_target_seconds'],
        max_pure_seconds=max(r.get('pure_seconds',0) for r in rows),elapsed_seconds=perf_counter()-began,
        passed=all(r['passed'] for r in rows) and all(v is not None and v<=spec['pure_p95_target_seconds'] for v in p95.values()),
        scope='One fresh Python process per frozen case; timed pure calculation includes cold classification table. Startup and UI/persistence excluded and not conflated.',cases=rows)
    (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('source_manifest','cases')},ensure_ascii=True))
    raise SystemExit(0 if report['passed'] else 1)


if __name__=='__main__':main()
