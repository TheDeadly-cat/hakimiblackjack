"""Repeatable recording latency probe, isolated temporary database only."""
import cProfile
import argparse
import json
from pathlib import Path
import pstats
import statistics
import sys
import tempfile
from time import perf_counter

def run(output, source=None, profile_enabled=True):
    sys.path.insert(0, str(source or Path(__file__).resolve().parents[1]))
    from blackjack_lab.ui.app import BlackjackLabApp
    from blackjack_lab.analysis.split_contracts import same_value_das_research_rules
    with tempfile.TemporaryDirectory() as tmp:
        app = BlackjackLabApp(Path(tmp) / 'latency.db', auto_analysis=False)
        app.withdraw()
        try:
            ctrl = app.ctrl
            # Populate before attaching the measured UI context. No worker load.
            listeners, ctrl._context_listeners = ctrl._context_listeners, []
            seats = [f'玩家{i}' for i in range(1, 8)]
            ctrl.new_shoe(same_value_das_research_rules(8))
            for _ in range(6):
                ctrl.start_round(seats)
                for i, seat in enumerate(seats):
                    ctrl.deal_shown(seat, 'T')
                    ctrl.deal_shown(seat, str(9-i))
                    hand = ctrl.state().current.table.players[seat].hands[0]
                    ctrl.player_action(seat, hand.hand_id, '停牌')
                ctrl.deal_shown('庄家', 'T')
                ctrl.deal_shown('庄家', '7')
                ctrl.end_round()
            ctrl.start_round(seats, simple_hole=True)
            ctrl._context_listeners = listeners
            for seat in seats:
                app.var_participants[seat].set(True)
            app.var_simple_hole.set(True)
            app._sync_from_plan()
            app.refresh_all()
            profile = cProfile.Profile()
            elapsed = []
            # All first-pass slots and most of the second pass; no decision EV.
            for rank in ['T'] * 7 + ['6'] + ['2'] * 6:
                started = perf_counter()
                if profile_enabled:
                    profile.enable()
                app._key_rank(rank)
                app.update_idletasks()
                if profile_enabled:
                    profile.disable()
                elapsed.append(1000 * (perf_counter()-started))
            assert ctrl.state().current.table.round_no == 7
            assert len(ctrl.entry_plan.filled_slots) == 14
            result = dict(events=len(ctrl.ledger.events), seats=7, prior_rounds=6, profiler=profile_enabled,
                          milliseconds=elapsed, median_ms=statistics.median(elapsed), max_ms=max(elapsed))
            Path(output).write_text(json.dumps(result, indent=2), encoding='utf-8')
            if profile_enabled:
                with open(str(output)+'.profile.txt', 'w', encoding='utf-8') as stream:
                    pstats.Stats(profile, stream=stream).sort_stats('cumulative').print_stats(35)
            print(json.dumps(result))
        finally:
            app.on_close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output')
    parser.add_argument('--source', type=Path)
    parser.add_argument('--no-profile', action='store_true')
    args = parser.parse_args()
    run(args.output, args.source, not args.no_profile)
