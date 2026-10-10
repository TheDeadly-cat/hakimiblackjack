"""Separate real-time qualification from side-bet correctness on fixed-source IPC.

Reuse the existing frozen scale probe. Wrappers timestamp the real production
methods without skipping replay, recovery, baseline comparison or persistence.
One AVAILABLE request/prefix is one decision batch, regardless of input count.
"""
import argparse
from collections import Counter
import hashlib
import json
import multiprocessing.queues
from pathlib import Path
import sys
import threading
from time import perf_counter
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import recording_end_to_end as probe


def profiled_recording_process(db_path, source, inputs, outputs):
    from blackjack_lab.ui.recording_process import _InputBridge, run_recording_process
    from blackjack_lab.ui.controller import SessionController
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.ui.read_snapshot import PrefixSnapshot
    from blackjack_lab.storage.database import LocalStore
    from blackjack_lab.ui.recording_owner import RecordingReceipt
    path = Path(str(db_path) + '.stages.jsonl')
    commit_path = Path(str(db_path) + '.commit-times.jsonl')
    state = dict(request_id=None, active=None, stages={})
    real_get = _InputBridge.get
    real_recover = SessionController.recover
    real_replay = EventLedger.replay
    real_capture = PrefixSnapshot.capture
    real_append = LocalStore.append_validated
    real_put = outputs.put

    def get(bridge, *args, **kwargs):
        task = real_get(bridge, *args, **kwargs)
        state.update(request_id=task.request_id, active=None, stages={})
        return task

    def timed(name, work):
        if state['active'] is not None or state['request_id'] is None:
            return work()
        start = perf_counter(); state['active'] = name
        try:
            return work()
        finally:
            state['stages'][name] = state['stages'].get(name, 0) + (perf_counter() - start) * 1000
            state['active'] = None

    def recover(cls, *args, **kwargs):
        return timed('recovery_including_validation_ms', lambda: real_recover(*args, **kwargs))

    def replay(ledger, *args, **kwargs):
        return timed('command_replay_validation_ms', lambda: real_replay(ledger, *args, **kwargs))

    def capture(cls, ledger):
        return timed('prefix_capture_ms', lambda: real_capture(ledger))

    def append(store, candidate):
        result = timed('sqlite_suffix_baseline_validation_and_commit_ms', lambda: real_append(store, candidate))
        committed_at = perf_counter()
        with commit_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(through_seq=candidate.events[-1].seq, committed_at=committed_at)) + '\n')
        return result

    def put(receipt, *args, **kwargs):
        if isinstance(receipt, RecordingReceipt):
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(dict(request_id=receipt.request_id, through_seq=receipt.after.through_seq,
                                             stages=dict(state['stages']))) + '\n')
        return real_put(receipt, *args, **kwargs)

    with patch.object(_InputBridge, 'get', get), \
         patch.object(SessionController, 'recover', classmethod(recover)), \
         patch.object(EventLedger, 'replay', replay), \
         patch.object(PrefixSnapshot, 'capture', classmethod(capture)), \
         patch.object(LocalStore, 'append_validated', append), patch.object(outputs, 'put', put):
        run_recording_process(db_path, source, inputs, outputs)


def identity():
    value = probe.source_identity()
    value['component_probe_sha256'] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return value


def one_case(out, mode, scale, fixture, *, point_value=False, visible=False):
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ui.controller import SessionController
    from blackjack_lab.ui.read_snapshot import PrefixSnapshot
    from blackjack_lab.ui.analysis_panel import AnalysisPanel
    from blackjack_lab.ui.recording_owner import RecordingReceipt
    raw_received, bridge_validation, ui_publication, ui_callbacks, requests, results = [], [], [], [], [], []
    local = threading.local()
    real_get = multiprocessing.queues.Queue.get
    real_capture = PrefixSnapshot.capture
    real_publish = SessionController.publish_recording_receipts
    real_poll = BlackjackLabApp._poll_recording
    real_start = AnalysisPanel.start
    real_analysis_poll = AnalysisPanel._poll
    seen_results = set()

    def queue_get(queue, *args, **kwargs):
        result = real_get(queue, *args, **kwargs)
        if isinstance(result, RecordingReceipt):
            local.request_id = result.request_id
            raw_received.append(dict(request_id=result.request_id, received_at=perf_counter(),
                                     worker_finished_at=result.finished_at, started_at=result.started_at,
                                     submitted_at=result.submitted_at))
        return result

    def capture(cls, ledger):
        start = perf_counter(); result = real_capture(ledger)
        if threading.current_thread().name == 'blackjack-recording-receipts':
            bridge_validation.append(dict(request_id=getattr(local, 'request_id', None),
                                          milliseconds=(perf_counter() - start) * 1000))
        return result

    def publish(controller, receipts, *args, **kwargs):
        start = perf_counter()
        result = real_publish(controller, receipts, *args, **kwargs)
        ui_publication.append(dict(request_ids=[r.request_id for r in receipts],
                                   milliseconds=(perf_counter() - start) * 1000))
        return result

    def poll(app):
        before = len(ui_publication); start = perf_counter()
        result = real_poll(app)
        if len(ui_publication) != before:
            ui_callbacks.append(dict(request_ids=[key for row in ui_publication[before:]
                                                  for key in row['request_ids']],
                                     milliseconds=(perf_counter() - start) * 1000))
        return result

    def start(panel, snapshot, *args, **kwargs):
        result = real_start(panel, snapshot, *args, **kwargs)
        if panel.service.active:
            requests.append(dict(request_id=panel.request_id, input_digest=snapshot.input_digest,
                                 start=panel.service.active['start']))
        return result

    def analysis_poll(panel):
        value = real_analysis_poll(panel)
        result = panel.last_result
        if (result and result['status'] == 'available' and not panel.app.recording_advice_pause()
                and result['request_id'] not in seen_results):
            seen_results.add(result['request_id'])
            results.append(dict(request_id=result['request_id'], input_digest=result['input_digest'],
                                engine_seconds=result['elapsed_seconds'], published_at=perf_counter()))
        return value

    with patch.object(probe, 'measured_recording_process', profiled_recording_process), \
         patch.object(multiprocessing.queues.Queue, 'get', queue_get), \
         patch.object(PrefixSnapshot, 'capture', classmethod(capture)), \
         patch.object(SessionController, 'publish_recording_receipts', publish), \
         patch.object(BlackjackLabApp, '_poll_recording', poll), \
         patch.object(AnalysisPanel, 'start', start), patch.object(AnalysisPanel, '_poll', analysis_poll):
        report = probe.run_case(out, mode, 7, fixture=fixture, point_value=point_value, visible=visible)
    stages = [json.loads(line) for line in Path(str(out / 'synthetic.db') + '.stages.jsonl').read_text().splitlines()]
    assert len(stages) == len(report['raw_inputs']) == len(raw_received)
    by_id = {r['request_id']: r for r in raw_received}
    publication_by_id = {key: r for r in report['raw_receipts'] for key in [r['request_id']]}
    starts = {r['input_digest']: r['start'] for r in requests}
    available = [d for d in report['raw_decisions'] if d['status'] == 'available']
    assert len({d['input_digest'] for d in available}) == len(available)
    decision_batches = []
    for decision in report['raw_decisions']:
        row = dict(decision)
        if decision['status'] == 'available':
            first = min(i['input_at'] for i in report['raw_inputs'] if i['request_id'] in decision['input_ids'])
            last = max(i['input_at'] for i in report['raw_inputs'] if i['request_id'] in decision['input_ids'])
            row.update(first_input_to_available_ms=(decision['decision_at'] - first) * 1000,
                       last_input_to_available_ms=(decision['decision_at'] - last) * 1000,
                       request_start_to_available_ms=(decision['decision_at'] - starts[decision['input_digest']]) * 1000)
        decision_batches.append(row)
    components = {
        'queue_wait_ms': probe.stats([(r['started_at'] - r['submitted_at']) * 1000 for r in raw_received]),
        'child_finish_to_parent_deserialized_ms': probe.stats([(r['received_at'] - r['worker_finished_at']) * 1000 for r in raw_received]),
        'bridge_full_content_validation_ms': probe.stats([r['milliseconds'] for r in bridge_validation]),
        'parent_deserialized_to_ui_receipt_ms': probe.stats([
            (publication_by_id[key]['ui_received_at'] - r['received_at']) * 1000 for key, r in by_id.items()]),
        'ui_validated_publication_ms': probe.stats([r['milliseconds'] for r in ui_publication]),
        'ui_receipt_callback_including_draw_ms': probe.stats([r['milliseconds'] for r in ui_callbacks]),
        'ev_engine_compute_ms': probe.stats([r['engine_seconds'] * 1000 for r in results]),
    }
    for name in ('recovery_including_validation_ms', 'command_replay_validation_ms', 'prefix_capture_ms',
                 'sqlite_suffix_baseline_validation_and_commit_ms'):
        components[name] = probe.stats([r['stages'].get(name, 0) for r in stages])
    result = dict(mode=mode, scale=scale, input_count=len(report['raw_inputs']),
        available_decision_batches=len(available), decision_batches=decision_batches,
        decision_statuses=dict(Counter(d['status'] for d in report['raw_decisions'])),
        metrics=report['metrics'], components=components, queue_max=report['queue_max'],
        oldest_wait_ms=report['oldest_wait_ms'], heartbeat_delay_ms=report['heartbeat_delay_ms'],
        durable_ledger_equal=report['durable_ledger_equal'], original_fixture_prefix_equal=report['original_fixture_prefix_equal'],
        pending_advice_hidden=report['pending_advice_hidden'],
        raw_component_data=dict(worker_stages=stages, deserialized_receipts=raw_received,
                                bridge_validation=bridge_validation, publication=ui_publication,
                                ui_callbacks=ui_callbacks, analysis_requests=requests, analysis_results=results),
        note='Recovery includes its nested validation; regular replay is measured outside recovery. IPC field combines queue serialization/transport/unpickle and scheduling. Do not label queue-inclusive input-to-commit as SQLite write time or sum overlapping parent timings.',
        product_scope={k:report[k] for k in ('point_value_scope','window_visible','main_auto_enabled',
            'card_identity_sidebets_allowed','sidebet_dispatches','old_enabled_true_sidebet_settings_preserved')},
        real_time_acceptance=False)
    (out / 'components.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: result[k] for k in ('mode', 'scale', 'input_count', 'available_decision_batches', 'components')},
                     ensure_ascii=False), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--batches-per-scale', type=int, default=3)
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    assert 1 <= args.batches_per_scale <= 5
    args.output.mkdir(parents=True, exist_ok=False)
    before = identity()
    from scripts.candidate_startup import prepare_runtime
    assert prepare_runtime()['status'] == 'available'
    scales = (1000,) if args.smoke else (1000, 5000, 10000)
    cases = [one_case(args.output / f'{mode}-{scale}-batch-{index}', mode, scale,
                      args.fixtures / f'events-{scale}.json')
             for mode in ('pragmatic', 'bclc') for scale in scales
             for index in range(1, args.batches_per_scale + 1)]
    assert identity() == before
    summary = dict(schema='sidebet-closeout-fixed-stage-measurement-v1', source=before,
        batches_per_scale=args.batches_per_scale, cases=[{k: v for k, v in r.items() if k != 'raw_component_data'} for r in cases],
        available_decision_batches=sum(r['available_decision_batches'] for r in cases),
        input_count=sum(r['input_count'] for r in cases),
        real_time_acceptance=False, synthetic_only=True,
        scope='Distinct requests and prefixes in separate repeated synthetic batches, not statistically independent live situations. UI/recording correctness and real-time readiness are separate gates; no validation shortcut or budget change.')
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
