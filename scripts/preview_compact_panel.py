"""Isolated real-result preview for native UI review; never opens the default DB."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.core.table import ACTION_SPLIT, ACTION_DOUBLE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', choices=('empty', 'single', 'partial', 'split', 'das', 'forced', 'complete'), default='single')
    parser.add_argument('--size', default='720x500')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    app = BlackjackLabApp(output / 'preview.db', recording_source=SOURCE_SIMULATOR)
    if args.scenario != 'empty':
        app.var_decks.set(8)
        app.act_research_template(split=args.scenario == 'split', das=args.scenario in ('das', 'forced', 'complete'))
        app.act_new_shoe()
        app.act_new_round()
        cards = ('T', '6', '6') if args.scenario == 'single' else ('8', '6', '8')
        for rank in cards:
            app._key_rank(rank)
        app._key_hole()
        if args.scenario in ('split', 'das', 'forced', 'complete'):
            app.act_action(ACTION_SPLIT)
            app._key_rank('9')
            app._key_stand()
            app._key_rank('3')
        if args.scenario in ('forced', 'complete'):
            app.act_action(ACTION_DOUBLE)
        if args.scenario == 'complete':
            app._key_rank('T')
    app.title(f'Hakimi · 简洁面板验收 · {args.scenario}（自建临时数据）')
    app.geometry(args.size)
    starts = []
    original_start = app.analysis_panel.service.start
    def start(*args, **kwargs):
        request = original_start(*args, **kwargs)
        starts.append(request)
        return request
    app.analysis_panel.service.start = start
    if args.scenario != 'empty':
        app.analysis_panel.calculate_current()

    def snapshot(closed=False):
        panel, view = app.analysis_panel, app.compact_panel
        widgets = [view.identity_label, view.status_label, view.message_label, view.details_button,
                   view.record_button, *[w for row in view.rows for w in row]]
        payload = dict(scenario=args.scenario, time=time.time(), closed=closed,
                       screen=[app.winfo_screenwidth(), app.winfo_screenheight()],
                       window=[app.winfo_width(), app.winfo_height()], dpi=app.winfo_fpixels('1i'),
                       summary=asdict(view.model), result=panel.last_result, requests=starts,
                       saved=panel.saved, events=app.ctrl.ledger.to_list(),
                       entry_prompt=app.var_entry_prompt.get(), recording_target=app.var_target.get(),
                       detail_text=panel.text.get('1.0', 'end-1c'),
                       controls=[dict(name=str(w), mapped=bool(w.winfo_ismapped()),
                           x=w.winfo_rootx()-app.winfo_rootx(), y=w.winfo_rooty()-app.winfo_rooty(),
                           width=w.winfo_width(), height=w.winfo_height(), requested_height=w.winfo_reqheight()) for w in widgets])
        temporary = output / 'state.tmp'
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output / 'state.json')
    def poll():
        snapshot()
        app.after(500, poll)
    def close():
        snapshot(True)
        app.on_close()
    app.protocol('WM_DELETE_WINDOW', close)
    app.after(500, poll)
    app.mainloop()


if __name__ == '__main__':
    main()
