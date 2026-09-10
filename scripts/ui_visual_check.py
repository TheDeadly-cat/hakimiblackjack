"""开发用窗口截图与布局核对。可选 Pillow，仅用于 QA，不是产品运行依赖。"""
import ctypes
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.ui.app import BlackjackLabApp


def main():
    from PIL import ImageGrab
    output = Path(__file__).resolve().parents[1] / "docs" / "review-20260910"
    output.mkdir(exist_ok=True)
    errors = []
    with tempfile.TemporaryDirectory() as tmp, patch("blackjack_lab.ui.app.messagebox.showerror", side_effect=lambda title, text, **kw: errors.append(text)):
        app = BlackjackLabApp(Path(tmp) / "synthetic-ui.db")
        try:
            app.var_decks.set(7)
            app.rule_details = {"remark": "自建演示数据：界面验收，非真实牌桌", "start_from_new_shoe": True, "burn_cards_known": True}
            app.act_new_shoe()
            app.act_new_round()
            app.act_card("8")
            app.act_card("8")
            app.btn_split.invoke()
            app.act_card("3")
            app.var_target.set("庄家")
            app.refresh_all()
            app.act_card("10")
            app.act_hidden_card()
            app.set_status("界面验收 · 自建演示数据 · 临时数据库 · 非真实牌桌")
            app.title("Hakimi Blackjack Lab · 自建演示数据 / 界面验收")
            app.update()
            ctypes.windll.user32.GetAncestor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            ctypes.windll.user32.GetAncestor.restype = ctypes.c_void_p
            hwnd = ctypes.windll.user32.GetAncestor(app.winfo_id(), 2)
            report = {"source": "self-built synthetic UI; temporary SQLite", "errors": errors, "sizes": []}
            for width, height in ((1360, 900), (1180, 800)):
                app.geometry(f"{width}x{height}")
                app.update()
                img = ImageGrab.grab(window=hwnd)
                img.save(output / f"ui-{width}x{height}.png")
                def walk(w):
                    yield w
                    for child in w.winfo_children():
                        yield from walk(child)
                outside = []
                unmapped = []
                for w in walk(app):
                    if not w.winfo_ismapped():
                        if w.winfo_class() in ("TButton", "Listbox", "TLabel", "Text"):
                            unmapped.append(str(w))
                        continue
                    x, y = w.winfo_rootx() - app.winfo_rootx(), w.winfo_rooty() - app.winfo_rooty()
                    if x < 0 or y < 0 or x + w.winfo_width() > app.winfo_width() + 1 or y + w.winfo_height() > app.winfo_height() + 1:
                        outside.append({"widget": str(w), "class": w.winfo_class(), "x": x, "y": y, "width": w.winfo_width(), "height": w.winfo_height()})
                report["sizes"].append({"requested": [width, height], "actual": [app.winfo_width(), app.winfo_height()], "outside": outside, "unmapped": unmapped, "captured": img.size})
            (output / "ui-layout.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False))
            if errors or any(s["outside"] or s["unmapped"] for s in report["sizes"]):
                return 1
            return 0
        finally:
            app.on_close()


if __name__ == "__main__":
    raise SystemExit(main())
