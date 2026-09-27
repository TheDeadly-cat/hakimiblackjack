"""Real Tk callbacks + real workers on an immutable runtime and independent synthetic cards."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from time import perf_counter,sleep
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    runtime,out=args.runtime.resolve(),args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    build=json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for name,h in build['package_manifest'].items():assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==h,name
    sys.path.insert(0,str(runtime));sys.dont_write_bytecode=True
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.core.table import ACTION_SPLIT,ACTION_DOUBLE,ACTION_STAND
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.ledger.card_inventory import project,TYPES
    from scripts.process_metrics import ProcessMetrics
    db=out/'joint.db';app=None;errors=[];commands=[];rounds=[];saved_originals={}
    known=[];hidden=0;decks=0;metrics=ProcessMetrics();began=perf_counter()
    def fail(title,message,**kwargs):errors.append(message)
    def command(name,callback):
        start=perf_counter();callback();app.update();elapsed=perf_counter()-start
        assert not errors,(name,errors)
        commands.append(dict(name=name,seconds=elapsed,seq=len(app.ctrl.ledger.events)))
        metrics.sample()
    def pump(predicate,seconds=25):
        end=perf_counter()+seconds
        while perf_counter()<end:
            app.update();metrics.sample()
            assert not errors,errors
            if predicate():return
            sleep(.01)
        raise AssertionError(('background wait expired',app.var_opening_ev.get(),{k:v.get() for k,v in app.sidebets.lines.items()}))
    def counts():
        segment=app.ctrl.state().current
        assert segment.shoe.physical_remaining()==52*decks-len(known)-hidden
        assert segment.shoe.unrevealed_out==hidden
        assert segment.shoe.conservation_check()[0]
        if not hidden and all(r!='T' and s is not None for r,s in known):
            inventory=project(app.ctrl.ledger)
            assert inventory['status']=='available',inventory['reason']
            expected=tuple(decks-known.count(card) for card in TYPES)
            assert inventory['counts']==expected
    def draw(rank,suit,seat,*,hole=False,automatic_hole=False):
        nonlocal hidden
        assert app.var_target.get()==seat,(seat,app.var_target.get())
        app.var_suit.set(suit or '未知')
        command('rank:'+rank,lambda:app._key_rank(rank))
        known.append((rank,suit));hidden+=int(automatic_hole)-int(hole);counts()
    def prediction():
        view=app.sidebets
        pump(lambda:'玩家1' in view.forecasts)
        value=view.forecasts['玩家1'];assert value['saved_id']
        path=view.store.directory/(value['saved_id']+'.json')
        saved_originals[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        return value
    def observed(pp,plus):
        view=app.sidebets;pump(lambda:view.observed is not None)
        for name,category in (('perfect_pairs',pp),('21+3',plus)):
            bet=view.observed['result']['output']['bets'][name]
            assert bet['category']==category,bet
        assert view.sealed and all('已封盘' in v.get() for v in view.lines.values())
    def settle(expected):
        current=app.ctrl.state().current
        previous=current.table.round_no-1
        results=[r for r in current.settlements if r['round']==previous]
        assert sum(r['net_units'] for r in results)==expected,(results,expected)
        rounds.append(dict(decks=decks,round=previous,net=expected,remaining=current.shoe.physical_remaining()))
        prediction()
    try:
        with patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True), \
             patch('blackjack_lab.ui.app.messagebox.showerror',side_effect=fail), \
             patch('blackjack_lab.ui.app.messagebox.showinfo',return_value=None):
            app=BlackjackLabApp(db,recording_source=SOURCE_SIMULATOR)
            app.title('Hakimi · 边注联合验收（独立模拟记录）')
            for decks in (6,7,8):
                known=[];hidden=0
                app.var_decks.set(decks)
                command('new shoe',app.act_new_shoe)
                app.var_sidebet_suits.set(True);app.compact_panel.toggle_suit_input()
                command('new round',app.act_new_round);prediction()
                # Mixed pair; both split initials first; double each hand; independent +4 settlement.
                draw('8','S','玩家1')
                command('undo first visible card',app.act_undo);known.pop();counts()
                draw('8','S','玩家1');draw('6','D','庄家');draw('8','H','玩家1',automatic_hole=True)
                observed('mixed','loss')
                frozen=app.sidebets.forecasts['玩家1']['result']['input_digest']
                command('split',lambda:app.act_action(ACTION_SPLIT))
                draw('3','S','玩家1');draw('2','H','玩家1')
                command('double first',lambda:app.act_action(ACTION_DOUBLE));draw('10','C','玩家1')
                command('double second',lambda:app.act_action(ACTION_DOUBLE));draw('Q','H','玩家1')
                pump(lambda:app.sidebets.observed is not None)
                assert app.sidebets.forecasts['玩家1']['result']['input_digest']==frozen
                assert len(app.sidebets.observed['result']['input']['original_card_ids']['perfect_pairs'])==2
                draw('K','C','庄家',hole=True);draw('3','D','庄家');settle(4)
                command('undo automatic finish group',app.act_undo);known.pop();counts()
                draw('3','D','庄家');prediction()
                # A-only actual peek; BJ scalar is visible while player analysis is gated.
                draw('Q','H','玩家1');draw('A','D','庄家');draw('9','S','玩家1',automatic_hole=True)
                assert app.compact_panel.dealer_bj['probability']>0
                assert not app.ctrl.state().current.table.dealer_hole_checked_negative
                command('actual negative peek',app.act_peek_negative)
                assert app.compact_panel.dealer_bj['probability']==0
                pump(lambda:app.analysis_panel.last_result is not None)
                assert app.analysis_panel.last_result['status']=='available'
                command('stand',lambda:app.act_action(ACTION_STAND))
                draw('7','H','庄家',hole=True);settle(1)
                # Three physically distinct 7S cards are legal; highest 21+3 category wins once.
                draw('7','S','玩家1');draw('7','S','庄家');draw('7','S','玩家1',automatic_hole=True)
                observed('perfect','suited_trips')
                assert app.sidebets.observed['result']['output']['bets']['21+3']['net_units']==100
                command('stand',lambda:app.act_action(ACTION_STAND));draw('10','H','庄家',hole=True);settle(-1)
                # QKA suited straight; S17 on soft17.
                draw('Q','C','玩家1');draw('A','C','庄家');draw('K','C','玩家1',automatic_hole=True)
                observed('loss','straight_flush')
                command('actual negative peek',app.act_peek_negative)
                command('stand',lambda:app.act_action(ACTION_STAND));draw('6','H','庄家',hole=True);settle(1)
                # Legacy rank-only recording and T bucket still work; sidebet forecast becomes unavailable.
                app.var_sidebet_suits.set(False);app.compact_panel.toggle_suit_input()
                draw('8',None,'玩家1');draw('6',None,'庄家');draw('9',None,'玩家1',automatic_hole=True)
                pump(lambda:app.analysis_panel.last_result is not None)
                assert app.analysis_panel.last_result['status']=='available'
                assert app.compact_panel.dealer_bj['probability']==0
                command('stand',lambda:app.act_action(ACTION_STAND))
                draw('T',None,'庄家',hole=True);draw('2',None,'庄家');settle(-1)
                assert app.sidebets.forecasts['玩家1']['result']['status']=='unavailable'
                events=app.ctrl.ledger.to_list();plan=app.ctrl.entry_plan.to_dict()
                app.on_close();app=None
                app=BlackjackLabApp(db,recording_source=SOURCE_SIMULATOR)
                app.ctrl.recording_source=SOURCE_SIMULATOR
                app.title('Hakimi · 边注联合验收（恢复独立模拟记录）')
                assert app.ctrl.ledger.to_list()==events and app.ctrl.entry_plan.to_dict()==plan
                counts()
            pump(lambda:app.sidebets.worker.active is None and not app.sidebets.worker.pending)
            events=app.ctrl.ledger.to_list()
            assert all(e['source']==SOURCE_SIMULATOR for e in events)
            (out/'ledger.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            store=app.ctrl.sidebet_store;app.on_close();app=None
            records,damaged=store.list();assert not damaged,damaged
            for saved in records:store.verified_input(saved,db)
            for path,h in saved_originals.items():assert hashlib.sha256(Path(path).read_bytes()).hexdigest()==h
            for name,h in build['package_manifest'].items():assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==h,name
            durations=[c['seconds'] for c in commands]
            report=dict(passed=True,commit=build['source_commit'],kind='Real Tk callbacks and real workers; synthetic declared cards, setup confirmation preset; not native keys',
                cases=rounds,rounds=len(rounds),events=len(events),commands=len(commands),snapshots=len(records),
                original_snapshots_unchanged=len(saved_originals),command_seconds=dict(median=statistics.median(durations),
                    p95=sorted(durations)[math.ceil(.95*len(durations))-1],maximum=max(durations)),
                resources=metrics.summary(),resource_scope='Own process tree including independent checking, not whole-system usage',
                errors=errors,elapsed_seconds=perf_counter()-began)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            (out/'commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=True))
    except Exception as error:
        (out/'failure.json').write_text(json.dumps(dict(error=repr(error),errors=errors,cases=rounds,commands=commands),ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        if app is not None:app.on_close()


if __name__=='__main__':main()
