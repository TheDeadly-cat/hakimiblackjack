"""Full research-folder backup and independent-directory recovery on owned synthetic data."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from time import perf_counter,sleep
from unittest.mock import patch


def hashes(root):return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
def write(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();runtime=args.runtime.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    info=json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for name,h in info['package_manifest'].items():assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==h,name
    sys.path.insert(0,str(runtime));sys.dont_write_bytecode=True
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from blackjack_lab.analysis.sidebets.information import execute
    from blackjack_lab.analysis.service import calculate
    from blackjack_lab.analysis.opening_service import OpeningService
    from scripts.tk_lifecycle import close_app
    from scripts.candidate_startup import prepare_runtime
    preparation=prepare_runtime();assert preparation['status']=='available'
    source=out/'source';source.mkdir();db=source/'practice.db';app=None;errors=[];report=dict(source_commit=info['source_commit'])
    def pump(predicate,seconds=25):
        deadline=perf_counter()+seconds
        while perf_counter()<deadline:
            app.update()
            assert not errors,errors
            if predicate():return
            sleep(.01)
        raise AssertionError('backup fixture wait expired')
    def signature():
        seg=app.ctrl.state().current
        return dict(session=app.ctrl.session_id,shoe=seg.shoe_id,round=seg.round_id,
                    remaining=seg.shoe.physical_remaining(),plan=app.ctrl.entry_plan.to_dict(),
                    target=app.var_target.get(),hand=app.var_hand.get(),analysis_target=app.var_analysis_target.get(),
                    profile=app.sidebets.profile.to_dict(),cards_visible=app.window_layout.cards_visible)
    try:
        with patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True),patch('blackjack_lab.ui.app.messagebox.showinfo'),patch('blackjack_lab.ui.app.messagebox.showerror',side_effect=lambda title,text,**kwargs:errors.append(text)):
            app=BlackjackLabApp(db,recording_source=SOURCE_SIMULATOR);app.act_common_settings()
            app.var_decks.set(6);app.act_new_shoe()
            for seat,v in app.var_participants.items():v.set(seat in ('玩家1','玩家2','玩家3'))
            app.var_sidebet_suits.set(True);app.compact_panel.toggle_suit_input()
            app.sidebets.apply_profile(app.sidebets.profile)
            app.act_new_round();pump(lambda:app.opening_estimate.result is not None and '玩家1' in app.sidebets.forecasts)
            assert app.opening_estimate.result['status']=='available'
            for rank,suit in [('10','S'),('4','H'),('6','C'),('7','D'),('9','H'),('7','S'),('5','C')]:
                app.var_suit.set(suit);app._key_rank(rank);app.update()
            pump(lambda:app.analysis_panel.last_result is not None and app.sidebets.observed is not None
                and all(row['snapshot'] is None or row['result'] is not None for row in app.analysis_panel.overview.rows.values()))
            assert app.analysis_panel.last_result['status']=='available'
            app._key_stand();app.update();assert app.var_target.get()=='玩家2'
            before=signature();events=app.ctrl.ledger.to_list();close_app(app);app=None
            config=source/'experiment-config.json'
            config.write_bytes((runtime/'fixtures/experiments/config-das-eight.json').read_bytes())
            run=subprocess.run([sys.executable,'-B',str(runtime/'scripts/run_experiment.py'),'--config',str(config),'--output',str(source/'experiment-output')],capture_output=True,text=True,encoding='utf-8',errors='replace')
            (out/'experiment.log').write_text(run.stdout+run.stderr,encoding='utf-8');assert run.returncode==0,run.stdout+run.stderr
            assert list((source/'experiment-output').glob('*.csv')) and list((source/'experiment-output').glob('*.json'))
            originals=hashes(source)
            for suffix in ('.analysis/','.opening/','.sidebets/','.deal_plans/'):
                assert any(name.startswith('practice.db'+suffix) for name in originals),suffix
            for name in ('practice.db','practice.db.sidebet-profile.json','practice.db.ui-preferences.json'):
                assert name in originals,name
            write(out/'original-manifest.json',originals);write(out/'before.json',before)
            backup=out/'backup';restored=out/'restored';shutil.copytree(source,backup);shutil.copytree(backup,restored)
            assert hashes(backup)==hashes(restored)==originals
            restored_db=restored/'practice.db';app=BlackjackLabApp(restored_db,auto_analysis=False,recording_source=SOURCE_SIMULATOR);app.update()
            app.ctrl.recording_source=SOURCE_SIMULATOR
            after=signature();assert after==before,(before,after)
            assert app.ctrl.ledger.to_list()==events
            counts={};chosen={}
            for kind,store in [('main',app.ctrl.analysis_store),('opening',app.ctrl.opening_store),('sidebets',app.ctrl.sidebet_store)]:
                entries,damaged=store.list();assert entries and not damaged,(kind,damaged)
                for saved in entries:
                    if kind=='main':app.ctrl.recompute_input(saved)
                    else:store.verified_input(saved,restored_db)
                counts[kind]=len(entries)
                chosen[kind]=next(saved for saved in entries if saved['result']['status']=='available')
            # Original-prefix computations create new snapshots under restored only.
            saved=chosen['main'];main_result=calculate(app.ctrl.recompute_input(saved));assert main_result['status']=='available'
            main_new=app.ctrl.analysis_store.save(main_result,saved['snapshot_id'])
            saved=chosen['sidebets'];edge_result=execute(app.ctrl.sidebet_store.verified_input(saved,restored_db))
            edge_new=app.ctrl.sidebet_store.save(edge_result,saved['event_prefix'],'historical_recompute',recomputed_from=saved['snapshot_id'])
            saved=chosen['opening'];service=OpeningService();start=perf_counter()
            try:
                snapshot=app.ctrl.opening_store.verified_input(saved,restored_db);service.start(snapshot)
                result=None
                while perf_counter()-start<23 and result is None:result=service.poll();app.update();sleep(.01)
                assert result and result['status']=='available',result
                opening_new=app.ctrl.opening_store.save(result,saved['event_prefix'],recomputed_from=saved['snapshot_id'])
            finally:service.close()
            assert edge_result['output']==chosen['sidebets']['result']['output']
            assert main_result['actions']==chosen['main']['result']['actions']
            assert result['ev']==chosen['opening']['result']['ev'] and result['histogram']==chosen['opening']['result']['histogram']
            new_before=len(app.ctrl.ledger.events);app.var_suit.set('D');app._key_rank('2');app.update()
            assert len(app.ctrl.ledger.events)==new_before+1 and not errors,errors
            assert app.ctrl.ledger.to_list()[:len(events)]==events
            close_app(app);app=None
            assert hashes(source)==hashes(backup)==originals
            for name,h in originals.items():
                if any(name.startswith('practice.db'+suffix) for suffix in ('.analysis/','.opening/','.sidebets/')) or name.startswith('experiment-output/'):
                    assert hashlib.sha256((restored/name).read_bytes()).hexdigest()==h,name
            for name,h in info['package_manifest'].items():assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==h,name
            report.update(passed=True,source_folder=str(source),backup_folder=str(backup),restored_folder=str(restored),
                original_files=len(originals),original_bytes=sum((source/n).stat().st_size for n in originals),
                recovered_identity=after,records_verified=counts,new_main=main_new['snapshot_id'],new_opening=opening_new['snapshot_id'],new_sidebet=edge_new['snapshot_id'],
                original_results_equal_on_recompute=True,new_recorded_events=1,original_source_and_backup_unchanged=True,
                scope='Independent owned synthetic app state, actual experiment JSON/CSV, complete manual folder-copy workflow; no user database opened.')
            write(out/'report.json',report);print(json.dumps(report,ensure_ascii=False),flush=True)
    except Exception as error:report.update(passed=False,error=repr(error),ui_errors=errors);write(out/'failure.json',report);raise
    finally:
        if app is not None:close_app(app)


if __name__=='__main__':main()
