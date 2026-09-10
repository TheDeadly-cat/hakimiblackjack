"""Actual Tk split workflow, app-only captures and synthetic importable examples."""
import argparse
import ctypes
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import uuid
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.storage.export import export_json
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest


def main():
    from PIL import ImageGrab
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    output=args.output or ROOT/'.local-evidence'/('split-ui-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
    output.mkdir(parents=True,exist_ok=False)
    before=source_manifest()
    identity=source_identity(ROOT)
    errors=[];records=[]
    with tempfile.TemporaryDirectory() as temporary,patch('blackjack_lab.ui.app.messagebox.showerror',side_effect=lambda *a,**kw:errors.append(a)):
        for scenario,pair,decks in [('split-eight','8',7),('split-aces','A',6)]:
            app=BlackjackLabApp(Path(temporary)/(scenario+'.db'),recording_source=SOURCE_SIMULATOR)
            try:
                app.var_decks.set(decks)
                app.act_research_template(split=True);app.act_new_shoe();app.act_new_round()
                app.var_target.set('庄家');app.refresh_all();app.act_card('6');app.act_hidden_card()
                app.var_target.set('玩家1');app.refresh_all();app.act_card(pair);app.act_card(pair)
                app.title('Hakimi Blackjack Lab V0.2b1 · 自建测试数据 / 真实计算')

                def capture(name):
                    panel=app.analysis_panel
                    panel.compute_button.invoke()
                    deadline=time.perf_counter()+7
                    while panel.last_result is None and time.perf_counter()<deadline:
                        app.update();time.sleep(.01)
                    result=panel.last_result
                    assert result and result['status']=='available',result
                    assert panel.saved,panel.persistence.get()
                    assert result['input_digest']==app.ctrl.analysis_input('玩家1',app._selected_hand_id(app._current_seg())).input_digest
                    export_json(app.ctrl.ledger,output/(name+'.json'))
                    (output/(name+'-result.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
                    app.set_status('自建研究数据，非平台记录；未知底牌保持未揭示；本轮快照已独立保存')
                    app.update()
                    ctypes.windll.user32.GetAncestor.argtypes=(ctypes.c_void_p,ctypes.c_uint)
                    ctypes.windll.user32.GetAncestor.restype=ctypes.c_void_p
                    hwnd=ctypes.windll.user32.GetAncestor(app.winfo_id(),2)
                    for width,height in ((1360,900),(1180,800)):
                        app.geometry(f'{width}x{height}');app.update()
                        screenshot=output/f'{name}-{width}x{height}.png'
                        ImageGrab.grab(window=hwnd).save(screenshot)
                        controls=[app.cmb_hand,app.btn_stand,app.btn_split,panel.compute_button,panel.text,app.lst_timeline]
                        outside=[]
                        for widget in controls:
                            x=widget.winfo_rootx()-app.winfo_rootx();y=widget.winfo_rooty()-app.winfo_rooty()
                            if not widget.winfo_ismapped() or x<0 or y<0 or x+widget.winfo_width()>app.winfo_width()+1 or y+widget.winfo_height()>app.winfo_height()+1:
                                outside.append(str(widget))
                        records.append(dict(scenario=name,size=[width,height],screenshot=screenshot.name,
                                            input_digest=result['input_digest'],outside=outside))
                    return result

                capture(scenario+'-before')
                app.btn_split.invoke();capture(scenario+'-forced-first')
                app.act_card('10');capture(scenario+'-first-card')
                if pair=='8':
                    app.act_card('10');capture(scenario+'-first-bust')
                    app.act_card('9');capture(scenario+'-second-17')
                    app.btn_stand.invoke();capture(scenario+'-complete')
                else:
                    app.act_card('10');capture(scenario+'-ordinary-21')
            finally:
                app.on_close()
    report=dict(schema='hakimi-split-tk-capture-v1',identity=identity,source_manifest=before,
                source_unchanged=before==source_manifest(),records=records,errors=errors,
                test_data='Temporary SQLite; every event marked SOURCE_SIMULATOR; no user database',
                passed=not errors and not any(r['outside'] for r in records) and before==source_manifest())
    (output/'receipt.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    (output/'artifact-hashes.json').write_text(json.dumps({p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()},indent=2),encoding='utf-8')
    print(json.dumps(dict(output=str(output),passed=report['passed'],captures=len(records)),ensure_ascii=False),flush=True)
    return 0 if report['passed'] else 1


if __name__=='__main__':
    raise SystemExit(main())
