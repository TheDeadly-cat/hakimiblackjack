"""Capture this app's real V0.2a calculations in a unique evidence directory."""
import argparse
import ctypes
from datetime import datetime
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.ui.app import BlackjackLabApp
from blackjack_lab.ledger.events import SOURCE_SIMULATOR


def main():
    from PIL import ImageGrab
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / ".local-evidence" / ("ui-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    output.mkdir(parents=True, exist_ok=False)
    errors, sizes, results = [], [], []
    with tempfile.TemporaryDirectory() as temp, patch("blackjack_lab.ui.app.messagebox.showerror", side_effect=lambda title, text, **kw: errors.append(text)):
        for scenario, cards, up in (("current", ("10", "6"), "10"), ("partial", ("8", "8"), "6")):
            app = BlackjackLabApp(Path(temp) / (scenario + ".db"), recording_source=SOURCE_SIMULATOR)
            try:
                app.var_decks.set(7)
                app.act_research_template()
                app.act_new_shoe()
                app.act_new_round()
                app.var_target.set("庄家")
                app.refresh_all()
                app.act_card(up)
                app.act_hidden_card()
                app.var_target.set("玩家1")
                app.refresh_all()
                for card in cards:
                    app.act_card(card)
                if up == "10":
                    app.act_peek_negative()
                app.analysis_panel.compute_button.invoke()
                until = time.perf_counter() + 7
                while app.analysis_panel.last_result is None and time.perf_counter() < until:
                    app.update()
                    time.sleep(0.01)
                result = app.analysis_panel.last_result
                assert result and result["status"] == "available", result
                assert app.analysis_panel.saved, "Analysis was not saved"
                assert app.ctrl.state().current.shoe.unrevealed_out == 1
                if scenario == "partial":
                    assert result["partial_comparison"] and result["highest_ev_action"] is None
                results.append(result)
                app.set_status("自建演示数据 · 当前底牌未揭示 · 真实有限牌靴计算 · 临时数据库，非平台记录")
                app.title("Hakimi Blackjack Lab V0.2a · 自建数据 / 真实计算验收")
                app.update()
                ctypes.windll.user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
                ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
                hwnd = ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
                for width, height in ((1360, 900), (1180, 800)):
                    app.geometry(f"{width}x{height}")
                    app.update()
                    capture = ImageGrab.grab(window=hwnd)
                    capture.save(output / f"{scenario}-{width}x{height}.png")
                    required = [app.lst_timeline, app.btn_stand, app.btn_double, app.btn_split,
                                app.btn_surr, app.analysis_panel.compute_button, app.analysis_panel.text]
                    outside, hidden = [], []
                    for widget in required:
                        if not widget.winfo_ismapped():
                            hidden.append(str(widget))
                            continue
                        x, y = widget.winfo_rootx() - app.winfo_rootx(), widget.winfo_rooty() - app.winfo_rooty()
                        if x < 0 or y < 0 or x + widget.winfo_width() > app.winfo_width() + 1 or y + widget.winfo_height() > app.winfo_height() + 1:
                            outside.append(str(widget))
                    sizes.append({"scenario": scenario, "width": width, "height": height, "outside": outside, "hidden_required_controls": hidden})
            finally:
                app.on_close()
    report = {"source": "synthetic observations marked SOURCE_SIMULATOR; actual engine result and temporary SQLite", "errors": errors,
              "layouts": sizes, "results": results,
              "passed": not errors and not any(s["outside"] or s["hidden_required_controls"] for s in sizes)}
    (output / "ui-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "passed": report["passed"], "layouts": sizes}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
