"""Frozen-input before/after probes; independent synthetic fixtures only."""
import argparse
import copy
import cProfile
import hashlib
import json
import math
from pathlib import Path
import platform
import pstats
import re
import shutil
import sys
import threading
from time import perf_counter,sleep
from unittest.mock import patch
import uuid


def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def stats(values):
    values=sorted(values)
    return dict(count=len(values),p50=values[math.ceil(len(values)*.50)-1],p95=values[math.ceil(len(values)*.95)-1],
                p99=values[math.ceil(len(values)*.99)-1],maximum=max(values)) if values else dict(count=0)


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True)
    p.add_argument('--fixtures',type=Path,required=True);p.add_argument('--output',type=Path)
    p.add_argument('--prepare',action='store_true');p.add_argument('--part',choices=('events','history','all'),default='all')
    args=p.parse_args();runtime=args.runtime.resolve();fixtures=args.fixtures.resolve()
    build=json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for n,h in build['package_manifest'].items():assert hashlib.sha256((runtime/n).read_bytes()).hexdigest()==h,n
    sys.path.insert(0,str(runtime));sys.dont_write_bytecode=True
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from blackjack_lab.analysis.split_contracts import ace_peek_das_research_rules
    from blackjack_lab.storage.database import LocalStore
    from blackjack_lab.analysis.contracts import canonical,digest
    from blackjack_lab.analysis.sidebets.information import build_input,execute
    from blackjack_lab.storage.sidebet_snapshots import SidebetSnapshots
    from scripts.process_metrics import ProcessMetrics
    specification=json.loads((Path(__file__).resolve().parents[1]/'fixtures/acceptance/responsiveness_v1.json').read_text(encoding='utf-8'))
    if args.prepare:
        fixtures.mkdir(parents=True,exist_ok=False)
        b=EventLedger('responsiveness-fixture-session');b.start_session('Synthetic scale fixture')
        b.create_shoe(ace_peek_das_research_rules(8));b.start_round(['玩家1'])
        b.deal('玩家1','10',suit='S');b.deal('庄家','7',suit='D');b.deal('玩家1','9',suit='H')
        hole=b.deal('庄家',hidden=True);hand=b.replay().current.table.players['玩家1'].hands[0].hand_id
        b.player_action('玩家1',hand,'停牌');b.reveal(hole.event_id,'10',suit='C')
        b.end_round(settle=True,observation_status='complete');b.end_shoe()
        original=b.to_list();block=original[1:]
        hexids=set(re.findall(r'[0-9a-f]{32}',canonical(block)))
        prefix=[copy.deepcopy(original[0])]
        def mapped(value,mapping):
            if isinstance(value,dict):return {k:mapped(v,mapping) for k,v in value.items()}
            if isinstance(value,list):return [mapped(v,mapping) for v in value]
            if isinstance(value,str):
                for old,new in mapping.items():value=value.replace(old,new)
            return value
        for target in specification['event_scales']:
            while len(prefix)<target:
                offset=len(prefix);mapping={s:uuid.uuid5(uuid.NAMESPACE_URL,f'hakimi-bench:{offset}:{s}').hex for s in hexids}
                for event in mapped(block,mapping):
                    event.update(seq=len(prefix)+1,event_time=1790500000+len(prefix)*.001,observed_at=1790500000+len(prefix)*.001,source=SOURCE_SIMULATOR)
                    prefix.append(event)
            frozen=EventLedger.from_list(b.session_id,prefix);assert frozen.replay().current.closed
            write(fixtures/f'events-{target}.json',prefix)
        # One real, validated full-shoe forecast; distinct immutable fixture records
        # share this original information prefix to isolate history file-count cost.
        b=EventLedger('history-scale-fixture-session');b.start_session('Synthetic history count fixture')
        b.create_shoe(ace_peek_das_research_rules(8))
        for event in b.events:event.source=SOURCE_SIMULATOR
        db=LocalStore(fixtures/'history.db');db.save_ledger(b);db.close()
        store=SidebetSnapshots(fixtures/'history');first=store.save(execute(build_input(b,'玩家1')),b.to_list(),'captured_predeal')
        # Keep the actual template separately; generated records have their own IDs.
        write(fixtures/'history-template.json',first)
        generated=fixtures/'history-generated';generated.mkdir()
        for i in range(max(specification['snapshot_scales'])):
            saved=copy.deepcopy(first);saved['snapshot_id']=uuid.uuid5(uuid.NAMESPACE_URL,f'hakimi-history-scale:{i}').hex
            saved['saved_at']+=i*.001
            saved['content_digest']=digest({k:v for k,v in saved.items() if k!='content_digest'})
            write(generated/(saved['snapshot_id']+'.json'),saved)
        manifest={str(path.relative_to(fixtures)):hashlib.sha256(path.read_bytes()).hexdigest() for path in fixtures.rglob('*') if path.is_file()}
        write(fixtures/'MANIFEST.json',dict(source_commit=build['source_commit'],specification=specification,files=manifest))
        print(json.dumps(dict(prepared=True,files=len(manifest),specification=specification),ensure_ascii=False));return
    assert args.output is not None
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    fixture_info=json.loads((fixtures/'MANIFEST.json').read_text(encoding='utf-8'))
    assert fixture_info['specification']==specification
    report=dict(source_commit=build['source_commit'],fixture_manifest_sha256=hashlib.sha256((fixtures/'MANIFEST.json').read_bytes()).hexdigest(),
                specification=specification,python=sys.version,platform=platform.platform(),events=[],history=[])
    app=None;began=perf_counter()
    def close():
        nonlocal app
        if app is None:return
        if hasattr(app,'exit_flow'):
            from scripts.tk_lifecycle import close_app
            close_app(app)
        else:app.on_close()
        app=None
    def pump(predicate,timeout=300):
        end=perf_counter()+timeout
        while perf_counter()<end:
            app.update()
            if predicate():return
            sleep(.005)
        raise AssertionError('scale benchmark wait expired')
    try:
        with patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True):
            if args.part in ('all','events'):
                for target in specification['event_scales']:
                    case=out/f'events-{target}';case.mkdir();db=case/'probe.db'
                    prefix=json.loads((fixtures/f'events-{target}.json').read_text(encoding='utf-8'))
                    ledger=EventLedger.from_list(prefix[0]['session_id'],prefix)
                    store=LocalStore(db);store.save_ledger(ledger);store.close()
                    app=BlackjackLabApp(db,auto_analysis=False,recording_source=SOURCE_SIMULATOR)
                    app.ctrl.recording_source=SOURCE_SIMULATOR
                    app.title(f'Hakimi · {target}事件响应基线（独立模拟）')
                    app.act_common_settings();app.act_new_shoe()
                    for v in app.var_participants.values():v.set(True)
                    app.act_new_round();app.update()
                    values=[];segments={};beats=[];beat_id=[None];last=[perf_counter()]
                    def heartbeat():
                        now=perf_counter();beats.append(max(0,now-last[0]-.05));last[0]=now;beat_id[0]=app.after(50,heartbeat)
                    heartbeat();metrics=ProcessMetrics()
                    for rank in ['T']*7+['7']+['9']*6:
                        start=perf_counter();app._key_rank(rank);app.update_idletasks();values.append(perf_counter()-start)
                        app.update();metrics.sample();sleep(.01)
                    # Separate profiler sample: the last initial player card also
                    # creates the simple hidden slot. Never mix this with the 14 latencies.
                    profiles=[]
                    def profiled(label,command):
                        profiler=cProfile.Profile();start=perf_counter();profiler.enable()
                        command();app.update_idletasks();profiler.disable()
                        elapsed=perf_counter()-start
                        profiler.dump_stats(str(case/(label+'.prof')))
                        measured=pstats.Stats(profiler);rows={}
                        for (file,line,name),(primitive,calls,total,cumulative,callers) in measured.stats.items():
                            if name in ('replay','deepcopy','to_list','digest','canonical','save_event','save_ledger',
                                        'append_validated','_insert','save_entry_plan','_write_entry_plan',
                                        'refresh_timeline','refresh_table','refresh_composition','refresh_all',
                                        '_refresh_all','project','project_prepared','capture'):
                                key=f'{Path(file).name}:{line}:{name}'
                                rows[key]=dict(primitive_calls=primitive,calls=calls,self_seconds=total,cumulative_seconds=cumulative)
                        profiles.append(dict(command=label,wall_seconds=elapsed,segments=rows))
                    profiled('simple-hole',lambda:app._key_rank('9'))
                    for i in range(7):profiled(f'stand-{i+1}',app._key_stand)
                    profiled('reveal-settle-next',lambda:app._key_rank('T'))
                    profiled('grouped-undo',app.act_undo)
                    profiled('reveal-again',lambda:app._key_rank('T'))
                    profiled('single-card',lambda:app._key_rank('8'))
                    recent=next(e for e in reversed(app.ctrl.ledger.events) if e.etype=='CARD_DEALT')
                    profiled('suit-correction',lambda:(app.ctrl.correct(recent.event_id,{'suit':'S'},'Synthetic profiler correction'),app.refresh_all()))
                    profiled('replace-shoe',app.act_new_shoe)
                    profiled('undo-shoe',app.act_undo)
                    app.after_cancel(beat_id[0]);events=app.ctrl.ledger.to_list()
                    row=dict(requested_prior_events=target,actual_prior_events=len(prefix),final_events=len(events),
                             callback_and_idle_paint_seconds=stats(values),heartbeat_delay_seconds=stats(beats),raw_callbacks=values,
                             segments_separately_profiled=segments,per_command_profiles=profiles,resources=metrics.summary(),
                             scope='Main-thread initial-deal probe; auto numeric and sidebet workers disabled to isolate UI/ledger costs. All-services long-shoe probe is separate.')
                    write(case/'events.json',events);write(case/'report.json',row);report['events'].append(row);close()
                    write(out/'progress.json',report);print(json.dumps(dict(event_scale=target,latency=row['callback_and_idle_paint_seconds']),ensure_ascii=False),flush=True)
            if args.part in ('all','history'):
                files=sorted((fixtures/'history-generated').glob('*.json'))
                for count in specification['snapshot_scales']:
                    case=out/f'history-{count}';case.mkdir();db=case/'history.db';shutil.copy2(fixtures/'history.db',db)
                    directory=Path(str(db)+'.sidebets');directory.mkdir()
                    for source in files[:count]:shutil.copy2(source,directory/source.name)
                    originals={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.glob('*.json')}
                    app=BlackjackLabApp(db,auto_analysis=False,recording_source=SOURCE_SIMULATOR);app.update()
                    metrics=ProcessMetrics();start=perf_counter();app.sidebets.show_history();app.update_idletasks()
                    first_feedback=perf_counter()-start;h=app.sidebets.history
                    pump(lambda:not h.loading and h.verified is not None)
                    elapsed=perf_counter()-start
                    row=dict(snapshot_files=count,first_feedback_seconds=first_feedback,loaded_and_selected_verified_seconds=elapsed,
                             displayed_rows=len(h.entries),resources=metrics.summary(),status=h.status.get())
                    close()
                    assert all(hashlib.sha256((directory/n).read_bytes()).hexdigest()==v for n,v in originals.items())
                    row['original_files_unchanged']=True;report['history'].append(row);write(case/'report.json',row)
                    write(out/'progress.json',report);print(json.dumps(dict(history_scale=count,seconds=elapsed,first_feedback=first_feedback)),flush=True)
        for n,h in fixture_info['files'].items():assert hashlib.sha256((fixtures/n).read_bytes()).hexdigest()==h,n
        for n,h in build['package_manifest'].items():assert hashlib.sha256((runtime/n).read_bytes()).hexdigest()==h,n
        report.update(passed=True,elapsed_seconds=perf_counter()-began)
        write(out/'report.json',report)
    except Exception as error:
        report.update(passed=False,error=repr(error));write(out/'failure.json',report);raise
    finally:close()


if __name__=='__main__':main()
