"""Actual Windows controls for R1/R2/R3; generated data, isolated SQLite only."""
import argparse
import ctypes
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.ui.app import BlackjackLabApp, RoundObservationDialog
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from scripts.source_identity import source_identity


def main():
    from PIL import ImageGrab
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    output = parser.parse_args().output
    output.mkdir(parents=True, exist_ok=False)
    checks, errors = {}, []
    ctypes.windll.user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p

    def capture(widget, name):
        widget.update()
        hwnd = ctypes.windll.user32.GetAncestor(widget.winfo_id(), 2)
        ImageGrab.grab(window=hwnd).save(output / (name + ".png"))

    def deal(app, seat, cards):
        app.var_target.set(seat)
        for card in cards:
            app.act_hidden_card() if card is None else app.act_card(card)

    def setup(app):
        app.act_research_template()
        app.act_new_shoe()
        app.act_new_round()

    def pump(app, seconds):
        until = time.perf_counter() + seconds
        while time.perf_counter() < until:
            app.update()
            time.sleep(.01)

    with tempfile.TemporaryDirectory() as tmp, patch("blackjack_lab.ui.app.messagebox.showerror", side_effect=lambda title, text, **kw: errors.append(text)):
        app = BlackjackLabApp(Path(tmp)/"cross-round.db", recording_source=SOURCE_SIMULATOR)
        try:
            app.update()
            setup(app)
            deal(app, "庄家", ("9", "8"))
            deal(app, "玩家1", ("10",))
            def accept_unknown():
                dialog = next(w for w in app.winfo_children() if isinstance(w, RoundObservationDialog))
                assert dialog.choices[dialog.selection.get()] == "unknown"
                capture(dialog, "observation-default-unknown")
                dialog.ok()
            app.after(40, accept_unknown)
            with patch("blackjack_lab.ui.app.simpledialog.askstring", return_value="自建测试：上一轮漏录第二张初始牌"):
                app.act_end_unsettled()
            app.act_new_round()
            deal(app, "庄家", ("10", None))
            deal(app, "玩家1", ("10", "6"))
            app.act_peek_negative()
            panel = app.analysis_panel
            assert panel.compute_button.instate(["disabled"])
            assert "此前第1轮" in panel.status.get()
            capture(app, "cross-round-analysis-blocked")
            checks["cross_round"] = app.ctrl.state().current.round_observations
            assert not errors, errors
        finally:
            app.on_close()
        app = BlackjackLabApp(Path(tmp)/"lifecycle.db", recording_source=SOURCE_SIMULATOR)
        try:
            app.update()
            setup(app)
            deal(app, "庄家", ("2", None))
            deal(app, "玩家1", ("5", "6"))
            panel = app.analysis_panel
            panel.compute_button.invoke()
            deadline = time.perf_counter() + 7
            while panel.last_result is None and time.perf_counter() < deadline:
                pump(app, .02)
            assert panel.last_result and panel.saved
            count = app.ctrl.store.event_count()
            with patch.object(app, "refresh_hands", side_effect=RuntimeError("synthetic render failure")):
                app.act_card("2")
            assert panel.last_result is None and panel.service.active is None
            assert "EV单位" not in panel.text.get("1.0", "end")
            assert app.ctrl.store.event_count() == count + 1
            assert "数据已成功提交" in app.var_status.get()
            capture(app, "committed-redraw-failed-result-cleared")
            checks["refresh_failure"] = {"committed_once": True, "current_result_cleared": True, "message": app.var_status.get()}
            app.act_refresh()
            panel.auto_button.invoke()
            app.act_card("2")
            assert panel._auto_id
            panel.cancel_button.invoke()
            pump(app, .4)
            assert panel._auto_id is None and panel.service.active is None and panel.last_result is None
            capture(app, "automatic-cancelled-no-restart")
            checks["cancel_auto"] = {"auto_still_checked": panel.auto.get(), "no_pending_or_active_request": True}
            assert len(errors) == 1 and "synthetic render failure" in errors[0], errors
        finally:
            app.on_close()
    report = {"source_identity": source_identity(ROOT), "data_source": "self-generated observations via real Tk actions; temporary SQLite", "checks": checks,
              "expected_injected_errors": errors, "passed": True}
    (output/"review-ui-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": True}))


if __name__ == "__main__":
    main()
