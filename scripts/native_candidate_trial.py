"""Owned native-input fixture; only Computer Use or the human sends recording keys."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from time import time
from unittest.mock import patch


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--runtime',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--resume',action='store_true');parser.add_argument('--short',action='store_true');args=parser.parse_args()
    runtime=args.runtime.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=args.resume)
    build=json.loads((runtime/'BUILD_INFO.json').read_text(encoding='utf-8'))
    for name,h in build['package_manifest'].items():assert hashlib.sha256((runtime/name).read_bytes()).hexdigest()==h,name
    sys.path.insert(0,str(runtime));sys.dont_write_bytecode=True
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.ledger.events import SOURCE_SIMULATOR
    from scripts.candidate_startup import prepare_runtime
    preparation=prepare_runtime();assert preparation['status']=='available',preparation
    with patch('blackjack_lab.ui.app.messagebox.askyesno',return_value=True):
        app=BlackjackLabApp(out/'native.db',recording_source=SOURCE_SIMULATOR)
        app.ctrl.recording_source=SOURCE_SIMULATOR
        if not args.resume:app.act_common_settings();app.act_new_shoe();app.act_new_round()
    app.geometry('660x460' if args.short else '720x850');app.update()
    name='resume' if args.resume else 'initial';log=(out/(name+'-keys.jsonl')).open('x',encoding='utf-8')
    original=app._key_binder.guard.accept_press
    def press(key):
        accepted=original(key)
        log.write(json.dumps(dict(time=time(),key=key,accepted=accepted,seq=len(app.ctrl.ledger.events)))+'\n');log.flush()
        return accepted
    app._key_binder.guard.accept_press=press
    last=[None];timer=[None]
    def capture():
        if app._closing:return
        token=(app.ctrl.context_token,app.var_target.get(),app.var_hand.get(),app.winfo_width(),app.winfo_height())
        if token!=last[0]:
            last[0]=token
            data=dict(pid=os.getpid(),commit=build['source_commit'],runtime=str(runtime),db=str(out/'native.db'),
                time=time(),target=app.var_target.get(),hand=app.var_hand.get(),title=app.title(),
                geometry=app.geometry(),plan=app.ctrl.entry_plan.to_dict() if app.ctrl.entry_plan else None,
                ledger=app.ctrl.ledger.to_list())
            (out/(name+'-state.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        timer[0]=app.after(150,capture)
    finish=app._finalize_close
    def final():
        if timer[0]:app.after_cancel(timer[0])
        (out/(name+'-final-ledger.json')).write_text(json.dumps(app.ctrl.ledger.to_list(),ensure_ascii=False,indent=2),encoding='utf-8')
        finish()
    app._finalize_close=final;capture();app.focus_force();app.mainloop();log.close()
    assert app.sidebets.worker.stopped
    (out/(name+'-closed.json')).write_text(json.dumps(dict(closed=True,commit=build['source_commit'],pid=os.getpid(),source_files_unchanged=all(hashlib.sha256((runtime/n).read_bytes()).hexdigest()==h for n,h in build['package_manifest'].items()))),encoding='utf-8')


if __name__=='__main__':main()
