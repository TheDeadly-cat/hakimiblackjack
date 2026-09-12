# -*- coding: utf-8 -*-
"""独立识牌演示：打开本地 PNG/JPEG，显示裁片与 13 点数候选。不写账本。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.vision.contracts import REVIEW_PENDING
from blackjack_lab.vision.deps import VisionDependencyError, cv2_available
from blackjack_lab.vision.evidence_store import EvidenceStore
from blackjack_lab.vision.image_io import load_image
from blackjack_lab.vision.pipeline import default_templates_dir, recognize_loaded, recognize_path


def _ensure_templates() -> Path:
    templates = default_templates_dir()
    if not (templates / "A.png").is_file():
        from blackjack_lab.vision.holdout import write_bundle
        write_bundle(templates.parent)
    return templates


def run_cli(image: Path, json_out: Path | None, evidence: Path | None) -> int:
    templates = _ensure_templates()
    loaded = load_image(image)
    result = recognize_loaded(loaded, templates_dir=templates)
    if evidence:
        EvidenceStore(evidence).save_result(loaded, result)
    text = result.to_json()
    if json_out:
        json_out.write_text(text, encoding="utf-8")
    print(text)
    print(f"review_status={result.review_status} writes_ledger=false observations={len(result.observations)}")
    return 0


def run_gui(initial: Path | None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    from blackjack_lab.vision.image_io import crop_rgb, write_png_rgb
    import tempfile

    templates = _ensure_templates()
    root = tk.Tk()
    root.title("Hakimi 识牌演示 V0.3a（图像待核对，不入账）")
    root.geometry("1100x760")
    status = tk.StringVar(value=REVIEW_PENDING)
    info = tk.StringVar(value="打开本地 PNG/JPEG。匹配度不是正确概率。不会写入账本。")

    preview = tk.Label(root, bg="#124e2c")
    preview.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
    crop_frame = ttk.Frame(root)
    crop_frame.pack(fill=tk.X, padx=8)
    log = tk.Text(root, height=12, wrap=tk.WORD)
    log.pack(fill=tk.BOTH, expand=False, padx=8, pady=8)
    ttk.Label(root, textvariable=status, font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=8)
    ttk.Label(root, textvariable=info, wraplength=1060).pack(anchor="w", padx=8, pady=(0, 8))

    photos = []

    def show_ppm(label, rgb, w, h, max_side=520):
        scale = max(1, max(w, h) // max_side)
        sw, sh = w // scale, h // scale
        # 最近邻缩小，避免引入额外图像库
        out = bytearray(sw * sh * 3)
        for y in range(sh):
            src_y = y * scale
            for x in range(sw):
                src = (src_y * w + x * scale) * 3
                dst = (y * sw + x) * 3
                out[dst:dst + 3] = rgb[src:src + 3]
        header = f"P6 {sw} {sh} 255\n".encode("ascii")
        img = tk.PhotoImage(data=header + bytes(out))
        photos.append(img)
        label.configure(image=img)

    def load_selected(path: Path):
        photos.clear()
        for child in crop_frame.winfo_children():
            child.destroy()
        log.delete("1.0", tk.END)
        try:
            loaded = load_image(path)
            result = recognize_loaded(loaded, templates_dir=templates)
        except (VisionDependencyError, Exception) as exc:
            messagebox.showerror("识牌失败", str(exc))
            return
        status.set(result.review_status)
        info.set("；".join(result.warnings[:3]))
        show_ppm(preview, loaded.rgb, loaded.width, loaded.height)
        log.insert(tk.END, result.to_json())
        for obs in result.observations:
            box = ttk.Frame(crop_frame, padding=4)
            box.pack(side=tk.LEFT)
            crop = crop_rgb(loaded, obs.bbox["x"], obs.bbox["y"], obs.bbox["w"], obs.bbox["h"])
            lbl = tk.Label(box)
            lbl.pack()
            show_ppm(lbl, crop, obs.bbox["w"], obs.bbox["h"], max_side=110)
            rank = obs.accepted_rank() or obs.face_state_candidate
            score = ""
            if obs.rank_candidates:
                score = f" 匹配度 {obs.rank_candidates[0].match_score:.3f}"
            ttk.Label(box, text=f"{rank}{score}\n{obs.reject_reason or '待确认'}").pack()

    def browse():
        chosen = filedialog.askopenfilename(
            title="选择本地牌桌截图",
            filetypes=[("图像", "*.png;*.jpg;*.jpeg"), ("全部", "*.*")],
        )
        if chosen:
            load_selected(Path(chosen))

    bar = ttk.Frame(root)
    bar.pack(fill=tk.X, padx=8, pady=4)
    ttk.Button(bar, text="打开本地图片", command=browse).pack(side=tk.LEFT)
    ttk.Label(bar, text="本窗口不会调用账本 deal() / 不会扣牌。").pack(side=tk.LEFT, padx=12)
    if initial and initial.is_file():
        root.after(100, lambda: load_selected(initial))
    root.mainloop()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="V0.3a 离线识牌演示（不入账）")
    parser.add_argument("image", nargs="?", type=Path, help="本地 PNG/JPEG")
    parser.add_argument("--gui", action="store_true", help="打开核对窗口")
    parser.add_argument("--json", type=Path, help="把候选 JSON 写到文件")
    parser.add_argument("--evidence", type=Path, help="保存裁片到目录（仍不写账本）")
    args = parser.parse_args()
    if args.gui:
        image = args.image
        if image is None:
            default_smoke = ROOT / "fixtures" / "vision" / "synthetic-v1" / "smoke" / "all13.png"
            image = default_smoke if default_smoke.is_file() else None
        return run_gui(image)
    if not args.image:
        parser.error("命令行模式需要图片路径；或加 --gui")
    if not cv2_available():
        print("缺少识牌依赖。pip install -r requirements-vision.txt", file=sys.stderr)
        return 2
    return run_cli(args.image, args.json, args.evidence)


if __name__ == "__main__":
    raise SystemExit(main())
