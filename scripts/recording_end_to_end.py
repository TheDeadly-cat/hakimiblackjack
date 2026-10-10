"""Default-process Tk measurement on frozen scale fixtures and seeded long shoes.

The probe wraps append_validated only to timestamp its successful return after
SQLite's commit. It adds no transaction, skips no validation and changes no
request budget. Timing JSON is owned synthetic evidence, never user data.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import random
import subprocess
import sys
from time import perf_counter, sleep
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def source_identity():
    paths = [*ROOT.joinpath('blackjack_lab').rglob('*.py'),
             *ROOT.joinpath('blackjack_lab').rglob('*.cs'),
             ROOT / 'scripts/recording_end_to_end.py', ROOT / '启动BCLC候选.cmd']
    manifest = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(paths)}
    digest = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    return dict(commit=revision, runtime_and_probe_sha256=digest, files=manifest)


def stats(values):
    ordered = sorted(values)
    return dict(count=len(values), **{name: ordered[math.ceil(len(values) * p) - 1]
                for name, p in (('p50', .5), ('p95', .95), ('p99', .99))}, maximum=max(values)) if values else dict(count=0)


def measured_recording_process(db_path, source, inputs, outputs):
    from blackjack_lab.storage.database import LocalStore
    from blackjack_lab.ui.recording_process import run_recording_process
    original = LocalStore.append_validated
    path = Path(str(db_path) + '.commit-times.jsonl')

    def append(store, candidate):
        result = original(store, candidate)
        committed_at = perf_counter()
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(dict(through_seq=candidate.events[-1].seq,
                                         committed_at=committed_at)) + '\n')
        return result

    with patch.object(LocalStore, 'append_validated', append):
        run_recording_process(db_path, source, inputs, outputs)


def total(ranks):
    values = [1 if r == 'A' else 10 if r in ('10', 'T', 'J', 'Q', 'K') else int(r) for r in ranks]
    hard = sum(values)
    return hard + 10 if 'A' in ranks and hard <= 11 else hard


def run_case(out, mode, players, fixture=None, long_shoe=None, *, point_value=False, visible=False):
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ui.table_modes import default_settings
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.analysis.contracts import InputUnavailable
    from blackjack_lab.core.table import ACTION_HIT, ACTION_STAND
    from scripts.tk_lifecycle import close_app
    out.mkdir(parents=True, exist_ok=False)
    db = out / 'synthetic.db'
    old_sidebet_settings = {}
    if point_value:
        from blackjack_lab.analysis.sidebets.contracts import SidebetProfile
        for suffix in ('.sidebet-profile.json', '.bclc-sidebet-profile.json'):
            path = Path(str(db) + suffix)
            raw = json.dumps(dict(schema=1, profile=SidebetProfile().to_dict(), enabled=True), indent=3).encode()
            path.write_bytes(raw); old_sidebet_settings[path] = raw
    errors, inputs, receipts, decisions, heartbeat, queue_samples = [], [], [], [], [], []
    app = None
    beat_id = None
    original_events = []
    started = perf_counter()
    last_beat = started

    def tick():
        nonlocal beat_id, last_beat
        now = perf_counter()
        heartbeat.append(max(0, (now - last_beat) * 1000 - 20))
        last_beat = now
        pending = app._recording_inputs
        queue_samples.append(dict(at=now, length=len(pending),
            oldest_wait_ms=max((now - i['input_at']) * 1000 for i in inputs
                               if i['request_id'] in pending) if pending else 0))
        beat_id = app.after(20, tick)

    def pump(predicate, timeout=120):
        deadline = perf_counter() + timeout
        while perf_counter() < deadline:
            app.update()
            if errors:
                raise AssertionError(errors)
            if predicate():
                return
            sleep(.002)
        raise AssertionError('Default-process input or result did not settle')

    def submit(callback, label):
        input_at = perf_counter()
        request_id = callback()
        accepted_at = perf_counter()
        if not request_id or not app.recording_busy:
            raise AssertionError((label, 'input was not accepted', app.var_recording_save.get()))
        inputs.append(dict(request_id=request_id, label=label, input_at=input_at,
                           accepted_at=accepted_at, input_to_accepted_ms=(accepted_at - input_at) * 1000))
        queue_samples.append(dict(at=accepted_at, length=len(app._recording_inputs),
            oldest_wait_ms=max((accepted_at - i['input_at']) * 1000 for i in inputs
                               if i['request_id'] in app._recording_inputs)))
        if app.compact_panel.model.choices or app.compact_panel.decision_evs.get():
            raise AssertionError('Pending input still presented current advice')
        app.update()  # Let the real event loop dispatch heartbeat/receipts between inputs.
        return request_id

    def rank(value, index):
        label = value[0] if isinstance(value, tuple) else value
        if index % 2:
            button_label = 'T 未细分' if label == 'T' else label
            button = next(b for b in app.workbench_card_buttons if b.cget('text') == button_label)
            # .invoke has no request return; collect the newly accepted ID.
            def click():
                before = set(app._recording_inputs)
                button.invoke()
                added = set(app._recording_inputs) - before
                return next(iter(added)) if len(added) == 1 else None
            return submit(click, 'mouse:' + label)
        return submit(lambda: app._key_rank(label), 'key:' + label)

    def saved():
        pump(lambda: not app.recording_busy)
        if app._recording_faults:
            raise AssertionError(app.recording_fault_message())

    def current_decision(batch):
        prefix = app.ctrl.read_prefix()
        try:
            app.ctrl.current_decision_input(app.var_analysis_target.get(), app._analysis_hand_id(app._current_seg()))
        except InputUnavailable as error:
            decisions.append(dict(through_seq=prefix.through_seq, status=error.status,
                                  reason_code=error.code, input_ids=batch))
            return
        pump(lambda: app.analysis_panel.last_result is not None and
             app.analysis_panel.last_result['input']['prefix_digest'] == prefix.prefix_digest, timeout=12)
        result = app.analysis_panel.last_result
        decision_at = perf_counter()
        decisions.append(dict(through_seq=prefix.through_seq, status=result['status'],
            reason_code=result['reason_code'], input_digest=result['input_digest'],
            decision_at=decision_at if result['status'] == 'available' else None, input_ids=batch))

    try:
        with patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=True), \
             patch('blackjack_lab.ui.app.messagebox.showinfo'), \
             patch('blackjack_lab.ui.app.messagebox.showerror', side_effect=lambda *a, **k: errors.append(str(a))), \
             patch('blackjack_lab.ui.recording_process.run_recording_process', measured_recording_process):
            app = BlackjackLabApp(db, recording_source=SOURCE_SIMULATOR, auto_analysis=True,
                                  background_recording=True, recording_process=True)
            if visible:
                app.title(app.title() + ' · 独立合成测量')
                app.deiconify(); app.update()
            else:app.withdraw()
            sidebet_dispatches = []
            original_side_submit = app.sidebets.worker.submit
            def side_submit(channel, *args, **kwargs):
                sidebet_dispatches.append(channel)
                return original_side_submit(channel, *args, **kwargs)
            app.sidebets.worker.submit = side_submit
            if point_value:
                assert app.auto_analysis and app.analysis_panel.auto.get()
                assert not app.sidebet_research and not app.sidebets.live_enabled()
            if fixture:
                original_events = json.loads(fixture.read_text(encoding='utf-8-sig'))
                assert original_events[0]['payload']['note'] == 'Synthetic scale fixture'
                assert all(e['source'] == SOURCE_SIMULATOR for e in original_events[1:])
                ledger = EventLedger.from_list(original_events[0]['session_id'], original_events)
                assert ledger.replay().current.closed
                app.ctrl.store.save_ledger(ledger); app.ctrl.load_session(ledger.session_id)
            app.select_table_mode(mode)
            settings = default_settings(mode)
            rules = settings.rules
            # Explicit truth of this owned synthetic shoe, never real-table attestation.
            rules.start_from_new_shoe = True; rules.burn_cards_known = True; rules.initial_burn_count = 0
            app._set_rule_form(rules)
            for seat, variable in app.var_participants.items():
                variable.set(int(seat[-1]) <= players)
            seats = [f'玩家{i}' for i in range(1, players + 1)]
            if settings.deal_direction == 'reverse':
                seats.reverse()
            app.var_my_seat.set(seats[0]); app.var_analysis_target.set(seats[0])
            app.var_auto_next.set(False)
            app.act_new_shoe(); app.act_new_round(); app.update()
            original_publish = app.ctrl.publish_recording_receipts

            def publish(values, *parameters):
                received_at = perf_counter()
                original_publish(values, *parameters)
                published_at = perf_counter()
                receipts.extend(dict(request_id=r.request_id, through_seq=r.after.through_seq,
                    ui_received_at=received_at, published_at=published_at,
                    worker_finished_at=r.finished_at, submitted_at=r.submitted_at) for r in values)
            app.ctrl.publish_recording_receipts = publish
            last_beat = perf_counter()  # Startup/context preparation is outside this heartbeat interval.
            beat_id = app.after(20, tick)
            rounds = 0
            if not long_shoe:
                batch = []
                values = (['A','2','3','4','5','6','7','8','9','T','2','3','4','5','6']
                          if point_value and players == 7 else ['9'] * players + ['6'] + ['2'] * players)
                for index, value in enumerate(values):
                    batch.append(rank(value, index))
                if mode == 'bclc':
                    batch.append(submit(app._key_hole, 'hole'))
                saved(); current_decision(batch)
                assert app.ctrl.state().current.shoe.physical_remaining() == 416 - 2 * (players + 1)
            else:
                # Reuse the existing frozen long-shoe seed/cut plan at the current 8-deck preset.
                deck = [(r, s) for r in ('A', '2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K')
                        for s in ('S', 'H', 'D', 'C')] * 8
                random.Random(long_shoe['seed']).shuffle(deck)
                (out / 'truth.json').write_text(json.dumps(deck), encoding='utf-8')
                index = 0
                while len(deck) > long_shoe['cut_remaining']:
                    batch = []
                    for _seat in seats:
                        batch.append(rank(deck.pop(0), index)); index += 1
                    up = deck.pop(0); batch.append(rank(up, index)); index += 1
                    for _seat in seats:
                        batch.append(rank(deck.pop(0), index)); index += 1
                    hole = deck.pop(0)
                    if mode == 'bclc':
                        batch.append(submit(app._key_hole, 'hole'))
                    saved()
                    dealer = [up[0], hole[0]]
                    if up[0] == 'A':
                        if total(dealer) == 21:
                            rank(hole, index); index += 1; saved()
                        else:
                            submit(app.act_peek_negative, 'actual-negative-peek'); saved()
                    current_decision(batch)
                    if not (up[0] == 'A' and total(dealer) == 21):
                        for seat in seats:
                            hand = app.ctrl.state().current.table.players[seat].hands[0]
                            while not hand.is_closed and total(hand.ranks) < 17:
                                submit(lambda: app.act_action(ACTION_HIT), 'hit')
                                rank(deck.pop(0), index); index += 1; saved()
                                hand = app.ctrl.state().current.table.players[seat].hands[0]
                            if not hand.is_closed and total(hand.ranks) < 21:
                                submit(lambda: app.act_action(ACTION_STAND), 'stand'); saved()
                        rank(hole, index); index += 1; saved()
                        while total(dealer) < 17:
                            value = deck.pop(0); dealer.append(value[0])
                            rank(value, index); index += 1; saved()
                    current = app.ctrl.state().current
                    assert current.shoe.physical_remaining() == len(deck)
                    assert current.table.dealer.hands[0].ranks == dealer
                    assert app.dealer_recording_finished()
                    rounds += 1
                    if len(deck) > long_shoe['cut_remaining']:
                        app.act_complete_and_next(current.round_id); app.update()
            saved()
            actual = app.ctrl.store.load_ledger(app.ctrl.session_id)
            assert actual.to_list() == app.ctrl.ledger.to_list()
            assert actual.to_list()[:len(original_events)] == original_events
            assert [r['request_id'] for r in receipts] == [i['request_id'] for i in inputs]
            if point_value:
                assert not sidebet_dispatches and app.sidebets.worker.thread is None
                assert app.sidebets.poll_id is None
                assert not list(app.sidebets.store.directory.glob('*.json'))
                assert all(path.read_bytes() == raw for path, raw in old_sidebet_settings.items())
                if visible:assert app.winfo_ismapped()
                assert app.auto_analysis and app.analysis_panel.auto.get()
            commits = {r['through_seq']: r for r in
                       (json.loads(line) for line in Path(str(db) + '.commit-times.jsonl').read_text().splitlines())}
            by_id = {i['request_id']: i for i in inputs}
            for receipt in receipts:
                item = by_id[receipt['request_id']]
                commit = commits[receipt['through_seq']]['committed_at']
                assert item['input_at'] <= commit <= receipt['ui_received_at']
                item.update(through_seq=receipt['through_seq'], input_to_db_commit_ms=(commit - item['input_at']) * 1000,
                            db_commit_to_ui_receipt_ms=(receipt['ui_received_at'] - commit) * 1000)
            for decision in decisions:
                for request_id in decision['input_ids']:
                    by_id[request_id]['latest_decision_status'] = decision['status']
                    if decision.get('decision_at'):
                        by_id[request_id]['input_to_latest_available_decision_ms'] = (
                            decision['decision_at'] - by_id[request_id]['input_at']) * 1000
            report = dict(schema='pr38-process-end-to-end-v1', mode=mode, players=players,
                background_recording=True, recording_process=True, auto_analysis=True,
                fixture_events=len(original_events), fixture_sha256=hashlib.sha256(fixture.read_bytes()).hexdigest() if fixture else None,
                long_shoe=long_shoe, rounds=rounds, remaining=app.ctrl.state().current.shoe.physical_remaining(),
                metrics={name: stats([i[name] for i in inputs if name in i]) for name in
                    ('input_to_accepted_ms', 'input_to_db_commit_ms', 'db_commit_to_ui_receipt_ms',
                     'input_to_latest_available_decision_ms')},
                heartbeat_delay_ms=stats(heartbeat), queue_max=max(q['length'] for q in queue_samples),
                oldest_wait_ms=stats([q['oldest_wait_ms'] for q in queue_samples]),
                elapsed_seconds=perf_counter() - started, raw_inputs=inputs, raw_receipts=receipts,
                raw_decisions=decisions, raw_heartbeat_delay_ms=heartbeat, raw_queue=queue_samples,
                durable_ledger_equal=True, original_fixture_prefix_equal=True, pending_advice_hidden=True,
                daily_database_accessed=False, errors=errors,
                point_value_scope=point_value, window_visible=bool(app.winfo_ismapped()),
                main_auto_enabled=app.analysis_panel.auto.get(),
                card_identity_sidebets_allowed=app.sidebet_research,
                sidebet_dispatches=sidebet_dispatches,
                old_enabled_true_sidebet_settings_preserved=all(path.read_bytes() == raw for path, raw in old_sidebet_settings.items()),
                measurement_note='Commit time is successful append_validated return after SQLite context exit; tiny timestamp/log wrapper overhead is included. Latest AVAILABLE latency is observed for eligible batch prefixes, not every transient input prefix.',
                native_keyboard_or_ime_acceptance=False, universal_performance_pass=False)
            (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: report[k] for k in ('mode', 'players', 'fixture_events', 'rounds', 'metrics',
                'queue_max', 'heartbeat_delay_ms', 'elapsed_seconds')}, ensure_ascii=False), flush=True)
            return report
    except Exception as error:
        (out / 'failure.json').write_text(json.dumps(dict(error=repr(error), inputs=inputs, receipts=receipts,
                                                        errors=errors), ensure_ascii=False, indent=2), encoding='utf-8')
        raise
    finally:
        if app:
            if beat_id:
                app.after_cancel(beat_id)
            close_app(app, discard_fixture_results=True, timeout=120)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--part', choices=('scale', 'long', 'all'), default='all')
    parser.add_argument('--smoke', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    identity = source_identity()
    from scripts.candidate_startup import prepare_runtime
    preparation = prepare_runtime()
    assert preparation['status'] == 'available', preparation
    reports = []
    spec = json.loads((ROOT / 'fixtures/acceptance/responsiveness_v1.json').read_text())
    scales = spec['event_scales'][:1] if args.smoke else spec['event_scales']
    for mode in ('pragmatic', 'bclc'):
        if args.part in ('scale', 'all'):
            for scale in scales:
                reports.append(run_case(args.output / f'{mode}-events-{scale}', mode, 7,
                                        fixture=args.fixtures / f'events-{scale}.json'))
        if args.part in ('long', 'all'):
            shoes = [spec['long_shoes'][0]] if args.smoke else [spec['long_shoes'][0], spec['long_shoes'][2]]
            for shoe in shoes:
                reports.append(run_case(args.output / f"{mode}-long-{shoe['players']}", mode,
                                        shoe['players'], long_shoe=shoe))
    assert source_identity() == identity, 'Source changed during measurement'
    summary = dict(source=identity, preparation=preparation, cases=[
        {k: r[k] for k in ('mode', 'players', 'fixture_events', 'long_shoe', 'rounds', 'remaining',
                           'metrics', 'heartbeat_delay_ms', 'queue_max', 'oldest_wait_ms', 'elapsed_seconds')}
        for r in reports], synthetic_only=True, all_durable_checks_passed=True,
        all_timing_targets_passed=False, native_keyboard_or_ime_acceptance=False)
    (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
