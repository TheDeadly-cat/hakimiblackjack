"""Bounded K-F5 rehearsal using real Tk events and an isolated synthetic ledger.
This is an automated scenario, not evidence of human following speed.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.core.table import PHASE_SETTLED
from blackjack_lab.analysis.service import calculate


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    db = output / 'rehearsal.db'
    records, errors, intentional_errors = [], [], []
    correction_seconds = []
    app = None
    started = time.perf_counter()
    with patch('blackjack_lab.ui.app.messagebox.showerror', side_effect=lambda title, text, **kw: errors.append(text)), \
         patch('blackjack_lab.ui.app.messagebox.showinfo'), \
         patch('blackjack_lab.ui.app.messagebox.askyesno', return_value=True):
        try:
            app = BlackjackLabApp(db, recording_source=SOURCE_SIMULATOR)
            app.act_research_template(das=True, same_value=True)
            app.var_decks.set(8)
            app.act_new_shoe()
            app.update()
            raw_events = []
            def observe(event):
                raw_events.append({'type': str(event.type), 'keysym': event.keysym,
                    'keycode': event.keycode, 'state': event.state, 'focus': str(app.focus_get())})
            tag = 'KF5RehearsalTrace'
            for sequence in ('<KeyPress>', '<KeyRelease>'):
                app.bind_class(tag, sequence, observe)
            app.bindtags((tag, *app.bindtags()))
            def press(key, expected_type=None, seat=None):
                raw_events.clear()
                guard_before = sorted(app._key_binder.guard.pressed)
                before = len(app.ctrl.ledger.events)
                target_before = app.var_target.get()
                app.focus_force()
                focus_deadline = time.perf_counter() + 2
                while True:
                    app.update()
                    if app.focus_get() == app:
                        break
                    assert time.perf_counter() < focus_deadline, 'Tk rehearsal focus did not become ready'
                    time.sleep(0.01)
                tick = time.perf_counter()
                generated_key = 'Tab' if key == 'ISO_Left_Tab' else key
                modifiers = {'state': 1} if key == 'ISO_Left_Tab' else {}
                app.event_generate('<KeyPress-' + generated_key + '>', when='tail', **modifiers)
                app.update()
                app.event_generate('<KeyRelease-' + generated_key + '>', when='tail', **modifiers)
                app.update()
                added = app.ctrl.ledger.events[before:]
                records.append({'key': key, 'raw_events': list(raw_events),
                    'guard_before': guard_before, 'guard_after': sorted(app._key_binder.guard.pressed),
                    'target_before': target_before, 'target_after': app.var_target.get(),
                    'event_ids': [e.event_id for e in added], 'event_types': [e.etype for e in added],
                    'mode': app.ctrl.entry_plan.mode, 'elapsed_seconds': time.perf_counter() - tick,
                    'errors_seen': len(errors)})
                assert not app._key_binder.guard.pressed, records[-1]
                if expected_type is None:
                    assert added == [], (records[-1], errors)
                else:
                    assert len(added) == 1 and added[0].etype == expected_type, (records[-1], errors)
                    if seat:
                        assert added[0].payload.get('seat') == seat, (key, added[0].payload)
            def select_players(names, mine):
                for name, var in app.var_participants.items():
                    var.set(name in names)
                app.var_my_seat.set(mine)
                app.act_new_round()
                assert app.var_target.get() == names[0]
            def dealer_projection():
                seg = app._current_seg()
                assert app.ctrl.entry_plan.mode == 'dealer_phase'
                assert app.var_target.get() == '庄家'
                assert app._selected_hand_id(seg) == seg.table.dealer.hands[0].hand_id
                assert '下一张给：庄家' in app.var_entry_prompt.get()
                assert '录入目标：庄家' in app.var_entry_prompt.get()
                assert not seg.table.dealer_hole_checked_negative
            def finish_dealer():
                dealer_projection()
                before = app.ctrl.state().current.shoe.physical_remaining()
                app.var_mode.set('揭示')
                press('9', 'CARD_REVEALED', '庄家')
                assert app.ctrl.state().current.shoe.physical_remaining() == before
                app.var_mode.set('新发牌')
                press('2', 'CARD_DEALT', '庄家')
                app.act_end_round()
                assert app.ctrl.state().current.table.phase == PHASE_SETTLED, app.ctrl.state().current.table.phase
            seats = ('玩家1', '玩家3', '玩家5')
            order = [*seats, '庄家', *seats, '庄家']
            keys = ('0', '7', '1', '6', '0', '0', '9', 'period')
            for round_no in range(13):
                select_players(seats, '玩家5')
                for index, (key, target) in enumerate(zip(keys, order)):
                    if round_no == 0 and index == 3:
                        press('space')
                        assert app.ctrl.entry_plan.input_paused
                        count = len(errors)
                        press('3')
                        assert len(errors) == count + 1
                        intentional_errors.extend(errors[count:])
                        errors[count:] = []
                        press('space')
                        assert not app.ctrl.entry_plan.input_paused
                    if round_no == 1 and index == 2:
                        tick = time.perf_counter()
                        press('2', 'CARD_DEALT', target)
                        press('BackSpace', 'UNDO')
                        press(key, 'CARD_DEALT', target)
                        correction_seconds.append(time.perf_counter() - tick)
                    else:
                        press(key, 'CARD_DEALT', target)
                table = app.ctrl.state().current.table
                assert [table.players[s].hands[0].ranks for s in seats] == [['T', 'T'], ['7', 'T'], ['A', '9']]
                assert not table.players['玩家2'].hands and not table.players['玩家4'].hands
                assert app.var_analysis_target.get() == '玩家5'
                for seat in seats:
                    press('minus', 'PLAYER_ACTION', seat)
                finish_dealer()
                assert errors == [], errors
            select_players(('玩家1',), '玩家1')
            for key, target in zip(('0', '6', '0', 'period'), ('玩家1', '庄家', '玩家1', '庄家')):
                press(key, 'CARD_DEALT', target)
            snapshot = app.ctrl.analysis_input('玩家1')
            result = calculate(snapshot)
            assert result['status'] == 'available', result.get('reason')
            saved = app.ctrl.analysis_store.save(result)
            press('slash', 'PLAYER_ACTION', '玩家1')
            first, second = app._current_seg().table.players['玩家1'].hands
            press('Tab')
            assert app._selected_hand_id(app._current_seg()) == second.hand_id
            press('ISO_Left_Tab')
            assert app._selected_hand_id(app._current_seg()) == first.hand_id
            press('2', 'CARD_DEALT', '玩家1')
            press('minus', 'PLAYER_ACTION', '玩家1')
            press('3', 'CARD_DEALT', '玩家1')
            press('asterisk', 'PLAYER_ACTION', '玩家1')
            press('8', 'CARD_DEALT', '玩家1')
            dealer_projection()
            assert '录入 玩家1 <- 8' in app.var_status.get()
            finish_dealer()
            assert errors == [], errors
            app.act_new_round()
            assert app.ctrl.entry_plan.cursor_slot_id == 's1'
            sid = app.ctrl.session_id
            final_events = app.ctrl.ledger.to_list()
            final_plan = app.ctrl.entry_plan.to_dict()
            source_sidecars = [Path(str(db) + suffix) for suffix in ('.analysis', '.deal_plans')]
            voided = app.ctrl.ledger._voided_ids()
            live = [e for e in app.ctrl.ledger.events if e.event_id not in voided]
            visible_deals = [e for e in live if e.etype == 'CARD_DEALT' and e.payload.get('face_state') == 'shown']
            assert len(visible_deals) >= 100
            assert len({e.event_id for e in app.ctrl.ledger.events}) == len(app.ctrl.ledger.events)
            app.on_close()
            app = None
            recovered = SessionController.recover(db, sid)
            try:
                assert recovered.ledger.to_list() == final_events
                assert recovered.entry_plan.to_dict() == final_plan
                backup = output / 'backup' / db.name
                recovered.store.backup(backup)
            finally:
                recovered.close()
            for source in source_sidecars:
                shutil.copytree(source, Path(str(backup) + source.name[len(db.name):]))
            restored = SessionController.recover(backup, sid)
            try:
                assert restored.ledger.to_list() == final_events
                assert restored.entry_plan.to_dict() == final_plan
                loaded = restored.analysis_store.load(saved['snapshot_id'])
                assert restored.recompute_input(loaded).rules_digest == snapshot.rules_digest
            finally:
                restored.close()
            report = {'passed': True, 'kind': 'automated Tk event rehearsal; not human efficiency evidence',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'setup_and_mode_actions': 'app callbacks; card/action keys use actual Tk event_generate',
                'completed_rounds': 14, 'active_next_round': 15, 'visible_card_deals': len(visible_deals),
                'reveals': sum(e.etype == 'CARD_REVEALED' for e in live),
                'key_operations': len(records), 'unexpected_errors': errors,
                'missing_or_duplicate_or_wrong_target_events': 0,
                'intentional_misrecord_and_undo': 1, 'intentional_pause_rejection': intentional_errors,
                'automated_correction_seconds': correction_seconds, 'unexpected_interruptions': 0,
                'duration_seconds': time.perf_counter() - started,
                'session_id': sid, 'analysis_snapshot_id': saved['snapshot_id'],
                'backup_verified': ['SQLite ledger', '.analysis snapshot + historical prefix', '.deal_plans position'],
                'backup_sha256': {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                                  for p in backup.parent.rglob('*') if p.is_file()}, 'trace': records}
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({k: v for k, v in report.items() if k not in ('trace', 'backup_sha256', 'intentional_pause_rejection')}, ensure_ascii=False))
        finally:
            (output / 'attempt-trace.json').write_text(json.dumps({'operations': records, 'errors': errors}, ensure_ascii=False, indent=2), encoding='utf-8')
            if app is not None:
                app.on_close()


if __name__ == '__main__':
    main()
