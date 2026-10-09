"""Isolated UI preview with an explicit synthetic database and bounded lifetime."""
import argparse
from pathlib import Path

from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ui.table_modes import BCLC
from scripts.tk_lifecycle import close_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=120)
    args = parser.parse_args()
    if args.db.exists():
        raise SystemExit('Preview needs a new synthetic database; preserve existing files.')
    app = BlackjackLabApp(args.db, recording_source=SOURCE_SIMULATOR, auto_analysis=False)
    app.title('BCLC / Pragmatic 隔离界面核对 · 合成库')
    app.select_table_mode(BCLC)
    app.after(args.seconds * 1000, lambda: close_app(app, discard_fixture_results=True))
    app.mainloop()


if __name__ == '__main__':
    main()
