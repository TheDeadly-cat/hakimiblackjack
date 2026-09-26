"""Three cut-card shoe cycles through real Tk callbacks and workers; isolated synthetic truth."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
from time import perf_counter, sleep
from unittest.mock import patch


def score(cards):
    value = sum(11 if c=='A' else 10 if c=='T' else int(c) for c in cards)
    aces = cards.count('A')
    while value>21 and aces:
        value -= 10; aces -= 1
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--smoke-rounds',type=int,default=0)
    args = parser.parse_args()
    runtime, out = args.runtime.resolve(), args.output.resolve()
    out.mkdir(parents=True,exist_ok=False)
    build = json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for name, expected in build['package_manifest'].items():
        assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==expected,name
    sys.path.insert(0,str(runtime))
    sys.dont_write_bytecode = True
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ui.controller import SessionController
    from blackjack_lab.ledger.ledger import EventLedger
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from scripts.process_metrics import ProcessMetrics
    import blackjack_lab
    assert Path(blackjack_lab.__file__).resolve().is_relative_to(runtime)
    db = out/'continuous.db'
    metrics = ProcessMetrics()
    app = None
    operations, analyses, openings, shoes, recoveries, errors = [], [], [], [], [], []
    began = perf_counter()
    log = (out/'operations.jsonl').open('x',encoding='utf-8')

    def emit(value):
        log.write(json.dumps(value,ensure_ascii=False)+'\n'); log.flush()

    def current_results():
        last = app.ctrl.ledger.events[-1].seq
        panel = app.analysis_panel
        for result in [panel.last_result, *[r['result'] for r in panel.overview.rows.values()]]:
            if result:
                assert result['input']['through_seq']==last,'stale current hand result'
        if app.opening_estimate.result:
            assert app.opening_estimate.result['input']['through_seq']==last,'stale opening result'
            assert app.opening_estimate.saved is not None,'visible opening result not saved'
        assert not app.opening_estimate.pending_saves,'opening persistence error'

    def command(label, callback):
        before = len(app.ctrl.ledger.events)
        tick = perf_counter()
        callback(); app.update()
        elapsed = perf_counter()-tick
        added = app.ctrl.ledger.events[before:]
        assert not errors,(label,errors)
        current_results()
        metrics.sample()
        workers = sum(s.active is not None for s in (app.analysis_panel.service,
            app.analysis_panel.overview.service,app.opening_estimate.service))
        item = dict(label=label,seconds=elapsed,through_seq=len(app.ctrl.ledger.events),
                    event_types=[e.etype for e in added],active_workers=workers,
                    target=app.var_target.get(),hand=app._hand_ordinal(app.var_target.get()))
        operations.append(item); emit(item)
        return added

    def rank(value, seat, hand=1):
        assert app.var_target.get()==seat,('wrong target',seat,app.var_target.get())
        if seat!='庄家':
            assert app._hand_ordinal(seat)==hand,('wrong hand',seat,hand)
        return command('rank:'+value,lambda:app._key_rank(value))

    def pump_until(predicate, seconds):
        deadline = perf_counter()+seconds
        while perf_counter()<deadline:
            app.update(); current_results(); metrics.sample()
            if predicate():
                return True
            sleep(.02)
        return False

    def wait_opening():
        start = perf_counter()
        assert pump_until(lambda:app.opening_estimate.result is not None,22),app.var_opening_ev.get()
        result = app.opening_estimate.result
        assert result['samples']==2_000_000
        openings.append(dict(seq=result['input']['through_seq'],seconds=perf_counter()-start,
                             snapshot=app.opening_estimate.saved['snapshot_id'],samples=result['samples']))
        emit(dict(opening=openings[-1]))

    def wait_hands():
        start = perf_counter()
        def settled():
            panel = app.analysis_panel
            rows = panel.overview.rows
            return ((panel.current_input is None or panel.last_result is not None)
                    and all(r['snapshot'] is None or r['result'] is not None for r in rows.values()))
        assert pump_until(settled,45),'automatic hand requests did not finish'
        results = [app.analysis_panel.last_result,*[r['result'] for r in app.analysis_panel.overview.rows.values()]]
        results = [r for r in results if r]
        analyses.append(dict(seq=len(app.ctrl.ledger.events),seconds=perf_counter()-start,
                             statuses=[r['status'] for r in results]))
        emit(dict(analysis=analyses[-1]))

    def reopen():
        nonlocal app
        events, plan = app.ctrl.ledger.to_list(),app.ctrl.entry_plan.to_dict()
        sid = app.ctrl.session_id
        start = perf_counter()
        app.on_close()
        app = BlackjackLabApp(db,recording_source=SOURCE_SIMULATOR)
        app.withdraw(); app.ctrl.recording_source=SOURCE_SIMULATOR; app.update()
        assert app.ctrl.session_id==sid and app.ctrl.ledger.to_list()==events
        assert app.ctrl.entry_plan.to_dict()==plan
        assert app.analysis_panel.auto.get()
        recoveries.append(dict(seq=len(events),seconds=perf_counter()-start))
        emit(dict(recovery=recoveries[-1]))

    try:
        with patch('blackjack_lab.ui.app.messagebox.showerror',side_effect=lambda title,text,**kw:errors.append(text)), \
             patch('blackjack_lab.ui.app.messagebox.showinfo'), \
             patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True):
            app = BlackjackLabApp(db,recording_source=SOURCE_SIMULATOR)
            app.withdraw()
            for shoe_no, count in enumerate((1,3,7),1):
                if args.smoke_rounds and shoe_no>1:
                    break
                seats = [f'玩家{i+1}' for i in range(count)]
                deck = (['A']+[str(i) for i in range(2,10)])*32+['T']*128
                prefix = ['8','6','8','8','3','2','T','A','3'] if shoe_no==1 else []
                for value in prefix: deck.remove(value)
                random.Random(20260927+shoe_no).shuffle(deck)
                deck = prefix+deck
                (out/f'shoe-{shoe_no}-truth.json').write_text(json.dumps(deck),encoding='utf-8')
                for seat,var in app.var_participants.items():var.set(seat in seats)
                app.var_my_seat.set('玩家1'); app.var_auto_next.set(True)
                command('new shoe',app.act_new_shoe)
                command('start round',app.act_new_round)
                shoe = dict(number=shoe_no,players=count,rounds=0,cut_at_remaining=52)
                shoes.append(shoe)
                while True:
                    round_no = app.ctrl.state().current.table.round_no
                    if shoe['rounds'] % 15==0:wait_opening()
                    if shoe['rounds']==1:
                        command('restart opening for cancellation',lambda:app.opening_estimate.refresh(force=True))
                        command('cancel opening',app.opening_estimate.cancel)
                        assert app.opening_estimate.service.active is None
                    shadow = {seat:[[]] for seat in seats}
                    stakes = {seat:[1] for seat in seats}
                    for i,seat in enumerate(seats):
                        value = deck.pop(0); shadow[seat][0].append(value); rank(value,seat)
                        if i==0 and shoe['rounds']==1:
                            command('undo first card',app.act_undo)
                            rank(value,seat)
                        if i==0 and shoe_no==2 and shoe['rounds']==0:
                            command('add first-pass seat',lambda:app.change_player_count(1))
                            command('remove first-pass seat',lambda:app.change_player_count(-1))
                            assert app.ctrl.state().current.table.participants==seats
                    up = deck.pop(0); rank(up,'庄家')
                    for seat in seats:
                        value=deck.pop(0); shadow[seat][0].append(value); rank(value,seat)
                    hole=deck.pop(0); dealer=[up,hole]
                    assert app.ctrl.state().current.shoe.physical_remaining()==len(deck)
                    bj = score(dealer)==21
                    ended_early = False
                    if up=='A':
                        if bj:
                            if len(deck)<=52:app.var_auto_next.set(False)
                            rank(hole,'庄家'); ended_early=True
                        else:
                            command('actual A negative peek',app.act_peek_negative)
                    if not ended_early:
                        if shoe['rounds']%10==0:wait_hands()
                        if shoe['rounds']==2:
                            command('cancel current hand',app.analysis_panel.cancel)
                            command('restart current hand',app.analysis_panel.calculate_current)
                            if count>1:
                                command('switch analysis target',lambda:app.var_analysis_target.set(seats[-1]))
                                command('restore analysis target',lambda:app.var_analysis_target.set(seats[0]))
                        if shoe['rounds']==3:
                            before = app.ctrl.state().current.shoe.physical_remaining()
                            target = app.var_target.get()
                            command('temporary mid-round new shoe',app.act_new_shoe)
                            assert app.ctrl.state().current.shoe.physical_remaining()==416
                            command('undo grouped shoe replacement',app.act_undo)
                            assert app.ctrl.state().current.shoe.physical_remaining()==before
                            assert app.var_target.get()==target
                        if shoe['rounds']==4:reopen()
                        for seat in seats:
                            hand=shadow[seat][0]
                            if score(hand)==21:continue
                            split = hand[0]==hand[1] and hand[0] in ('8','A')
                            if split:
                                command('split',app._key_split)
                                shadow[seat]=[[hand[0]],[hand[1]]]; stakes[seat]=[1,1]
                                for index in range(2):
                                    value=deck.pop(0); shadow[seat][index].append(value); rank(value,seat,index+1)
                            for index,hand in enumerate(shadow[seat]):
                                if score(hand)>=21 or split and hand[0]=='A':continue
                                if len(hand)==2 and score(hand) in (10,11):
                                    stakes[seat][index]=2
                                    command('double',app._key_double)
                                    value=deck.pop(0); hand.append(value); rank(value,seat,index+1)
                                else:
                                    while score(hand)<12:
                                        value=deck.pop(0); hand.append(value); rank(value,seat,index+1)
                                    if score(hand)<21:
                                        command('stand',app._key_stand)
                        if len(deck)<=52 and score(dealer)>=17:app.var_auto_next.set(False)
                        rank(hole,'庄家')
                        while score(dealer)<17:
                            value=deck.pop(0); dealer.append(value)
                            if len(deck)<=52 and score(dealer)>=17:app.var_auto_next.set(False)
                            rank(value,'庄家')
                    seg=app.ctrl.state().current
                    if seg.table.round_no==round_no:
                        command('settle without next at cut',app.act_end_round)
                    ended = next(e for e in reversed(app.ctrl.ledger.events) if e.etype=='ROUND_ENDED'
                                 and not app.ctrl.ledger.is_voided(e.event_id))
                    old = EventLedger.from_list(app.ctrl.session_id,[e for e in app.ctrl.ledger.to_list()
                        if e['seq']<=ended.seq]).replay().current
                    assert old.table.dealer.hands[0].ranks==dealer
                    expected=[]
                    for seat in seats:
                        assert [h.ranks for h in old.table.players[seat].hands]==shadow[seat]
                        for hand,stake in zip(shadow[seat],stakes[seat]):
                            natural=len(hand)==2 and score(hand)==21 and len(shadow[seat])==1
                            value,ds=score(hand),score(dealer)
                            net=(-stake if value>21 else 0 if bj and natural else -stake if bj else
                                 1.5 if natural else stake if ds>21 or value>ds else 0 if value==ds else -stake)
                            expected.append((seat,net))
                    actual=[(r['seat'],r['net_units']) for r in old.settlements if r['round']==round_no]
                    assert actual==expected,(actual,expected)
                    assert old.shoe.physical_remaining()==len(deck)
                    assert old.shoe.conservation_check()[0] and not old.shoe.unrevealed_out
                    if shoe['rounds']==5 and app.var_auto_next.get():
                        trigger=next(e for e in reversed(app.ctrl.ledger.events) if e.etype in ('CARD_DEALT','CARD_REVEALED')
                                     and e.payload.get('seat')=='庄家' and not app.ctrl.ledger.is_voided(e.event_id))
                        command('undo automatic next group',app.act_undo)
                        self_round=app.ctrl.state().current.table.round_no
                        assert self_round==round_no
                        rank(trigger.payload['rank'],'庄家')
                        assert app.ctrl.state().current.table.round_no==round_no+1
                    shoe['rounds']+=1
                    shoe.update(drawn=416-len(deck),remaining=len(deck))
                    stop=len(deck)<=52 or args.smoke_rounds and shoe['rounds']>=args.smoke_rounds
                    if stop:
                        if app.ctrl.state().current.table.round_no>round_no:
                            # A smoke run can stop before the declared cut; do not claim a complete shoe.
                            app.ctrl.end_round_unsettled('smoke stopped at an empty next round','unknown')
                            app.refresh_all()
                        command('end shoe at declared cut',app.act_end_shoe)
                        shoe['closed']=True
                    if stop or shoe['rounds']%5==0:
                        progress=dict(shoes=shoes,events=len(app.ctrl.ledger.events),commands=len(operations),
                                      elapsed_seconds=perf_counter()-began)
                        (out/'progress.json').write_text(json.dumps(progress,ensure_ascii=False,indent=2),encoding='utf-8')
                        print(json.dumps(progress,ensure_ascii=False),flush=True)
                    if stop:break
            events=app.ctrl.ledger.to_list();sid=app.ctrl.session_id
            assert all(e['source']==SOURCE_SIMULATOR for e in events)
            assert len({e['event_id'] for e in events})==len(events)
            (out/'final-ledger.json').write_text(json.dumps(events,ensure_ascii=False,indent=2),encoding='utf-8')
            app.on_close();app=None
            recovered=SessionController.recover(db,sid)
            try:
                assert recovered.ledger.to_list()==events
                history_started=perf_counter()
                entries,damaged=recovered.opening_store.list()
                assert not damaged,damaged
                for saved in entries:
                    recovered.opening_store.verified_input(saved,db)
                history_seconds=perf_counter()-history_started
            finally:recovered.close()
            durations=[row['seconds'] for row in operations]
            for name,expected_hash in build['package_manifest'].items():
                assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==expected_hash,name
            report=dict(passed=True,source_commit=build['source_commit'],runtime=str(runtime),
                kind='Real Tk callbacks and background workers; automated independent synthetic truth, not native-key or human-speed acceptance',
                smoke=bool(args.smoke_rounds),shoes=shoes,rounds=sum(s['rounds'] for s in shoes),
                cards_drawn=sum(s['drawn'] for s in shoes),events=len(events),commands=len(operations),
                command_seconds=dict(median=statistics.median(durations),p95=sorted(durations)[__import__('math').ceil(.95*len(durations))-1],maximum=max(durations)),
                resources=metrics.summary(),resource_scope='Own test process tree, including truth-verification overhead; 50ms/operation samples can miss brief peaks',
                analyses=analyses,openings=openings,recoveries=recoveries,opening_records_verified=len(entries),
                opening_history_verification_seconds=history_seconds,errors=errors,
                cut_policy='416-card shoe, finish the current round after crossing 52 remaining, then close; unplayed cut cards not recorded as dealt',
                elapsed_seconds=perf_counter()-began)
            (out/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps({k:v for k,v in report.items() if k not in ('analyses','openings','recoveries')},ensure_ascii=False),flush=True)
    except Exception as error:
        (out/'failure.json').write_text(json.dumps(dict(error=repr(error),errors=errors,shoes=shoes,
            events=len(app.ctrl.ledger.events) if app else None,operations=len(operations)),ensure_ascii=False,indent=2),encoding='utf-8')
        raise
    finally:
        if app is not None:app.on_close()
        log.close()


if __name__=='__main__':
    main()
