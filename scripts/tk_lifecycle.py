"""Pump cooperative shutdown in isolated acceptance/test harnesses, not UI callbacks."""
from time import perf_counter,sleep
import tkinter as tk
from unittest.mock import patch


def close_app(app,*,discard_fixture_results=False,timeout=15):
    app.on_close()
    end=perf_counter()+timeout
    while perf_counter()<end:
        try:
            if not app.winfo_exists():return
            app.update()
            flow=app.exit_flow
            if flow and flow.phase=='needs_save':
                if not discard_fixture_results:
                    raise AssertionError('Acceptance has unsaved results: '+str(flow.pending()))
                # Explicitly authorized by the caller only for an owned fault fixture.
                with patch('blackjack_lab.ui.shutdown.messagebox.askyesno',return_value=True):flow.discard()
            sleep(.005)
        except tk.TclError:
            if getattr(app.exit_flow,'phase',None)=='finished':return
            raise
    raise AssertionError('Owned fixture did not finish cooperative shutdown')
