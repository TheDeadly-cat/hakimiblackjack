"""Actual Tk DAS workflow, two window sizes, synthetic data only."""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.core.table import ACTION_HIT
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.storage.export import export_json
from blackjack_lab.ui.app import BlackjackLabApp
from scripts.source_identity import source_identity
from scripts.verify_release import source_manifest


def main():
    from PIL import ImageGrab
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or ROOT / ".local-evidence" / (
        "das-ui-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    if output.exists():
        raise SystemExit("输出目录已存在，拒绝覆盖: " + str(output))
    output.mkdir(parents=True, exist_ok=False)
    before = source_manifest()
    identity = source_identity(ROOT)
    errors = []
    records = []
    with tempfile.TemporaryDirectory() as temporary, patch(
            "blackjack_lab.ui.app.messagebox.showerror", side_effect=lambda *a, **kw: errors.append(a)):
        app = BlackjackLabApp(Path(temporary) / "das.db", recording_source=SOURCE_SIMULATOR)
        try:
            app.var_decks.set(6)
            app.act_research_template(das=True)
            app.act_new_shoe()
            app.act_new_round()
            app.var_target.set("庄家")
            app.refresh_all()
            app.act_card("6")
            app.act_hidden_card()
            app.var_target.set("玩家1")
            app.refresh_all()
            app.act_card("8")
            app.act_card("8")
            app.title("Hakimi Blackjack Lab · DAS研究数据 / 真实计算")

            def capture(name):
                panel = app.analysis_panel
                panel.compute_button.invoke()
                deadline = time.perf_counter() + 7
                while panel.last_result is None and time.perf_counter() < deadline:
                    app.update()
                    time.sleep(0.01)
                result = panel.last_result
                assert result and result["status"] == "available", result
                assert panel.saved, panel.persistence.get()
                export_json(app.ctrl.ledger, output / (name + ".json"))
                (output / (name + "-result.json")).write_text(
                    json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
                app.set_status("自建研究数据，非平台记录；未知底牌保持未揭示；本轮快照已独立保存")
                app.update()
                ctypes.windll.user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
                ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
                hwnd = ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
                for width, height in ((1360, 900), (1180, 800)):
                    app.geometry(f"{width}x{height}")
                    app.update()
                    screenshot = output / f"{name}-{width}x{height}.png"
                    ImageGrab.grab(window=hwnd).save(screenshot)
                    controls = [app.cmb_hand, app.btn_stand, app.btn_double, app.btn_split,
                                panel.compute_button, panel.text, app.lst_timeline]
                    outside = []
                    for widget in controls:
                        x = widget.winfo_rootx() - app.winfo_rootx()
                        y = widget.winfo_rooty() - app.winfo_rooty()
                        if (not widget.winfo_ismapped() or x < 0 or y < 0
                                or x + widget.winfo_width() > app.winfo_width() + 1
                                or y + widget.winfo_height() > app.winfo_height() + 1):
                            outside.append(str(widget))
                    records.append(dict(scenario=name, size=[width, height], screenshot=screenshot.name,
                                        engine=result.get("engine_version"),
                                        input_digest=result["input_digest"], outside=outside))
                return result

            before_split = capture("das-eight-before")
            assert "split" in before_split["actions"]
            app.btn_split.invoke()
            capture("das-forced-first")
            app.act_card("3")
            ready = capture("das-first-can-das")
            assert "double" in ready["actions"]
            hit_ev = ready["actions"]["hit"]["ev"]
            app.act_action(ACTION_HIT)
            pending = capture("das-first-hit-pending")
            assert set(pending["actions"]) == {"deal"}
            assert abs(pending["actions"]["deal"]["ev"] - hit_ev) < 1e-10
            app.act_card("6")
            after_hit = capture("das-first-three-no-das")
            assert "double" not in after_hit["actions"]
            app.btn_stand.invoke()
            capture("das-second-waiting")
            app.act_card("3")
            second = capture("das-second-can-das")
            assert "double" in second["actions"]
            app.btn_double.invoke()
            capture("das-second-unique-card")
            app.act_card("6")
            done = capture("das-complete")
            assert set(done["actions"]) == {"complete"}
        finally:
            app.on_close()
    report = dict(schema="hakimi-das-tk-capture-v1", identity=identity, source_manifest=before,
                  source_unchanged=before == source_manifest(), records=records, errors=errors,
                  test_data="Temporary SQLite; SOURCE_SIMULATOR; no user database",
                  passed=not errors and not any(r["outside"] for r in records) and before == source_manifest())
    (output / "receipt.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "artifact-hashes.json").write_text(json.dumps(
        {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir()) if p.is_file()},
        indent=2), encoding="utf-8")
    print(json.dumps(dict(output=str(output), passed=report["passed"], captures=len(records)),
                     ensure_ascii=False), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
