"""Frozen new-order request matrix and separately budgeted opening MC measurements."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import platform
import statistics
import sys
from time import perf_counter, sleep

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.native_backend import build_native
from blackjack_lab.analysis.opening import build_opening_input
from blackjack_lab.analysis.opening_service import OpeningService, validate_opening_result
from blackjack_lab.analysis.service import AnalysisService
from blackjack_lab.analysis.split_contracts import both_initial_das_rules, ace_peek_das_research_rules, BOTH_INITIAL_ENGINE, DAS_ENGINE
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.storage.analysis_snapshots import _validate_result
from scripts.process_metrics import ProcessMetrics
from scripts.source_identity import source_identity
from tests.test_analysis_integration import example

SPEC = ROOT / 'fixtures/acceptance/candidate_models_v1.json'
ACTIONS = dict(split='分牌', hit='补牌', double='加倍', stand='停牌')


def manifest():
    paths = [*list((ROOT/'blackjack_lab').rglob('*.py')), *list((ROOT/'blackjack_lab').rglob('*.cs')),
             SPEC, Path(__file__), ROOT/'scripts/process_metrics.py', ROOT/'tests/test_analysis_integration.py']
    return {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(paths)}


def cases(spec, mode):
    result = []
    for decks in spec['decks']:
        if mode == 'exact':
            for up in spec['upcards']:
                for stage in spec['stages']:
                    result.append(dict(stage, decks=decks, up=up['rank'], peek=up['peek'],
                                       id=f'{decks}d-{up["rank"]}-{stage["id"]}'))
        else:
            for order in spec['opening_orders']:
                for seat in spec['opening_seats']:
                    result.append(dict(seat, decks=decks, order=order,
                                       id=f'{decks}d-{order}-{seat["seats"]}p-f{seat["focal"]}'))
    assert len(result) == spec[mode + '_expected_count']
    assert len({case['id'] for case in result}) == len(result)
    return result


def construct(case, mode):
    rules = (both_initial_das_rules if case['order'] == 'both' else ace_peek_das_research_rules)(case['decks'])
    if mode == 'opening':
        ledger = EventLedger('opening-measurement-' + case['id'])
        ledger.start_session('独立固定策略开局测量；合成数据')
        ledger.create_shoe(rules)
        seats = tuple(f'玩家{i+1}' for i in range(case['seats']))
        return build_opening_input(ledger, seats, seats[case['focal']]), ledger
    ledger = example(case['decks'], cards=(case['pair'], case['pair']), up=case['up'],
                     rules=rules, peek=case['peek'])
    for action, index, *rank in case['steps']:
        hand = ledger.replay().current.table.players['玩家1'].hands[index]
        if action == 'deal':
            ledger.deal('玩家1', rank[0], hand_id=hand.hand_id, source='自建模拟器')
        else:
            ledger.player_action('玩家1', hand.hand_id, ACTIONS[action])
    snapshot = build_input(ledger, '玩家1')
    assert snapshot.engine_version == (BOTH_INITIAL_ENGINE if case['order'] == 'both' else DAS_ENGINE)
    assert snapshot.peek_negative == case['peek']
    return snapshot, ledger


def percentile(values, fraction=.95):
    return sorted(values)[math.ceil(len(values)*fraction)-1] if values else None


def cancellation_cases(output, spec):
    rows = []
    for decks in spec['decks']:
        case = dict(id=f'cancel-{decks}d', decks=decks, order='both', seats=7, focal=6)
        snapshot, _ = construct(case, 'opening')
        service = OpeningService()
        metrics = ProcessMetrics()
        owned = set()
        started = perf_counter()
        try:
            service.start(snapshot, spec['opening_budget_seconds'])
            while perf_counter()-started < 3:
                owned = metrics.descendants() - {metrics.root}
                if len(owned) >= 2:  # both Python worker and its native calculation are live
                    break
                if service.poll() is not None:
                    break
                sleep(.01)
            ready = len(owned) >= 2 and service.active is not None
            cancel_start = perf_counter()
            service.cancel()
            elapsed = perf_counter()-cancel_start
            remaining = [pid for pid in owned if metrics.running(pid)]
            until = perf_counter()+1
            while remaining and perf_counter()<until:
                sleep(.01)
                remaining = [pid for pid in remaining if metrics.running(pid)]
            row = dict(id=case['id'], worker_and_native_confirmed_live=ready,
                       cancellation_seconds=elapsed, surviving_owned_pids=remaining,
                       status=service.result.get('status') if service.result else None,
                       result=service.result)
            row['passed'] = ready and not remaining and row['status']=='cancelled' and 'histogram' not in (service.result or {})
            (output/(case['id']+'.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
            rows.append(row)
        finally:
            service.close()
    return rows


def run(output, mode):
    output.mkdir(parents=True, exist_ok=False)
    spec = json.loads(SPEC.read_text(encoding='utf-8'))
    frozen = cases(spec, mode)
    before = manifest()
    identity = source_identity(ROOT)
    (output/'frozen-cases.json').write_text(json.dumps(frozen, ensure_ascii=False, indent=2), encoding='utf-8')
    prep = perf_counter(); native = build_native(); prep = perf_counter()-prep
    rows = []
    for case in frozen:
        result = None
        metrics = ProcessMetrics()
        service = AnalysisService() if mode == 'exact' else OpeningService()
        budget = spec[mode + '_budget_seconds']
        started = None
        validation_error = None
        try:
            snapshot, ledger = construct(case, mode)
            started = perf_counter()
            service.start(snapshot, budget_seconds=budget)
            sampled = 0
            while result is None:
                result = service.poll()
                if perf_counter() - sampled >= .05:
                    metrics.sample(); sampled = perf_counter()
                if result is None:
                    if service.active is None:
                        raise RuntimeError('worker ended without a matching result')
                    sleep(.003)
            elapsed = perf_counter() - started
            if result['status'] == 'available':
                if mode == 'exact':
                    _validate_result(result)
                else:
                    validate_opening_result(result, snapshot)
                    assert result['samples'] == spec['opening_samples']
            envelope = dict(case=case, input=snapshot.to_dict(), event_prefix=ledger.to_list(), result=result)
        except Exception as error:
            elapsed = perf_counter()-started if started is not None else 0
            validation_error = f'{type(error).__name__}: {error}'
            envelope = dict(case=case, result=result, validation_error=validation_error)
        finally:
            service.close()
        raw = json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False).encode('utf-8')
        (output/(case['id']+'.json')).write_bytes(raw)
        row = dict(id=case['id'], order=case['order'], decks=case['decks'],
                   status=result.get('status') if result else 'failed', error=validation_error,
                   wall_seconds=elapsed, budget_seconds=budget, metrics=metrics.summary(),
                   result_sha256=hashlib.sha256(raw).hexdigest())
        rows.append(row)
        with (output/'progress.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
        if len(rows) % 10 == 0 or row['status'] != 'available' or row['error']:
            print(f'{len(rows)}/{len(frozen)} {row["id"]}: {row["status"]} {elapsed:.3f}s {validation_error or ""}', flush=True)
    groups = {}
    for row in rows:
        groups.setdefault(f'{row["order"]}-{row["decks"]}d', []).append(row['wall_seconds'])
    times = [row['wall_seconds'] for row in rows]
    p95 = {key: percentile(values) for key, values in groups.items()}
    complete = all(row['status'] == 'available' and not row['error'] for row in rows)
    within_budget = all(row['wall_seconds'] <= row['budget_seconds'] for row in rows)
    target = mode != 'exact' or all(value <= spec['exact_p95_target_seconds'] for value in p95.values())
    cancellations = cancellation_cases(output, spec) if mode == 'opening' else []
    unchanged = before == manifest()
    receipt = dict(schema='hakimi-candidate-model-measurement-v1', mode=mode, source=identity,
        platform=platform.platform(), python=sys.version, source_manifest=before, source_unchanged=unchanged,
        spec_sha256=hashlib.sha256(SPEC.read_bytes()).hexdigest(), expected_count=len(frozen), count=len(rows),
        cases=rows, statuses={s:sum(row['status']==s for row in rows) for s in sorted({r['status'] for r in rows})},
        preparation_seconds=prep, binary_sha256=hashlib.sha256(native.read_bytes()).hexdigest(),
        median_seconds=statistics.median(times), p95_seconds=percentile(times), max_seconds=max(times),
        p95_by_order_decks=p95, all_completed=complete, within_budget=within_budget, target_met=target,
        cancellations=cancellations,
        passed=complete and within_budget and target and unchanged and all(c['passed'] for c in cancellations),
        scope='One cold worker/native process per frozen case, source-bound executable prebuilt; no strategy/result cache reuse. Opening and exact budgets/statistics separate.')
    (output/'receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({key:receipt[key] for key in ('mode','count','statuses','p95_by_order_decks','max_seconds','passed')},ensure_ascii=False),flush=True)
    return receipt


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('exact','opening'), required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(0 if run(args.output, args.mode)['passed'] else 1)
