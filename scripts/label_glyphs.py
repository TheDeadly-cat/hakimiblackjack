# -*- coding: utf-8 -*-
"""角标人工标注。

ui     键盘标一张看一张（2-9 / 0=10 / a j q k / n=junk / 空格跳过 / Backspace 撤销）
apply  按叠加图编号批量写入标签（智能体或离线核对用）

留出集也要标：标签是真值，训练不会用这些裁片。不要把模型分数回写成标签。

    python scripts/label_glyphs.py ui     .local-evidence/material-train-20260912/glyph-queue
    python scripts/label_glyphs.py apply  .local-evidence/material-train-20260912/glyph-queue labels.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.vision.glyph_dataset import (  # noqa: E402
    LABEL_RANKS, load_queue, save_queue, validate_label,
)

KEY_MAP = {
    "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7", "8": "8", "9": "9",
    "0": "10", "a": "A", "j": "J", "q": "Q", "k": "K",
    "n": "junk",
}


def cmd_apply(args) -> int:
    queue_dir = Path(args.queue)
    items = load_queue(queue_dir)
    by_id = {item.crop_id: item for item in items}
    payload = json.loads(Path(args.labels).read_text(encoding="utf-8"))
    overlay_index = {}
    index_path = queue_dir / "overlays" / "index.json"
    if index_path.is_file():
        overlay_index = json.loads(index_path.read_text(encoding="utf-8"))

    written = 0
    if isinstance(payload, dict) and payload and not _looks_like_crop_map(payload):
        # { "card-00000": {"0": "A", "1": "junk"}, ... }
        for stem, mapping in payload.items():
            if stem.startswith("_"):
                continue
            info = overlay_index.get(stem) or overlay_index.get(Path(stem).stem)
            if not info:
                print(f"叠加图索引里没有 {stem}")
                continue
            ids = info["crop_ids"]
            for key, label in mapping.items():
                idx = int(key)
                if idx < 0 or idx >= len(ids):
                    print(f"{stem} 编号 {idx} 超出范围")
                    continue
                validate_label(label)
                by_id[ids[idx]].label = label
                written += 1
    else:
        for crop_id, label in payload.items():
            if crop_id not in by_id:
                print(f"未知 crop_id {crop_id}")
                continue
            validate_label(label)
            by_id[crop_id].label = label
            written += 1

    save_queue(items, queue_dir)
    labeled = sum(1 for i in items if i.label)
    print(f"写入 {written} 条，队列里已标注 {labeled}/{len(items)}")
    return 0


def _looks_like_crop_map(payload: dict) -> bool:
    keys = [k for k in payload.keys() if not k.startswith("_")]
    if not keys:
        return False
    return all(len(k) == 16 and all(ch in "0123456789abcdef" for ch in k) for k in keys[:8])


def cmd_ui(args) -> int:
    try:
        import tkinter as tk
        from PIL import Image, ImageTk
    except ImportError:
        print("需要 tkinter 与 Pillow 才能开标注窗。也可用 apply 子命令写入 JSON。")
        return 2

    queue_dir = Path(args.queue)
    items = load_queue(queue_dir)
    if args.split:
        items = [i for i in items if i.split == args.split]
    if not items:
        print("队列为空")
        return 1

    cursor = {"i": 0}
    history = []

    def current():
        return items[cursor["i"]]

    def save():
        save_queue(items, queue_dir)

    root = tk.Tk()
    root.title("角标标注")
    photo_label = tk.Label(root)
    photo_label.pack()
    status = tk.StringVar()
    tk.Label(root, textvariable=status, font=("Segoe UI", 12)).pack()
    tk.Label(
        root,
        text="2-9  0=10  A J Q K  N=不是点数  空格=跳过  Backspace=撤销",
        font=("Segoe UI", 10),
    ).pack()
    image_holder = {"photo": None}

    def show():
        item = current()
        path = queue_dir / item.crop_file
        image = Image.open(path)
        image.thumbnail((360, 360))
        photo = ImageTk.PhotoImage(image)
        image_holder["photo"] = photo
        photo_label.configure(image=photo)
        labeled = sum(1 for i in items if i.label)
        status.set(
            f"{cursor['i'] + 1}/{len(items)}  {item.split}  round {item.round_id}  "
            f"{item.frame}  已标 {labeled}  当前 {item.label or '未标'}"
        )

    def assign(label: str):
        item = current()
        history.append((cursor["i"], item.label))
        item.label = label
        save()
        if cursor["i"] + 1 < len(items):
            cursor["i"] += 1
        show()

    def skip(_event=None):
        if cursor["i"] + 1 < len(items):
            cursor["i"] += 1
        show()

    def undo(_event=None):
        if not history:
            return
        idx, old = history.pop()
        items[idx].label = old
        cursor["i"] = idx
        save()
        show()

    def on_key(event):
        key = event.keysym.lower() if event.keysym else ""
        char = (event.char or "").lower()
        if key == "backspace":
            undo()
            return
        if key in ("space", "right"):
            skip()
            return
        if key == "left":
            cursor["i"] = max(0, cursor["i"] - 1)
            show()
            return
        label = KEY_MAP.get(char)
        if label:
            assign(label)

    root.bind("<Key>", on_key)
    show()
    root.mainloop()
    save()
    labeled = sum(1 for i in items if i.label)
    print(f"已标注 {labeled}/{len(items)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="角标人工标注")
    sub = parser.add_subparsers(dest="command", required=True)

    ui = sub.add_parser("ui", help="键盘标注窗")
    ui.add_argument("queue")
    ui.add_argument("--split", choices=("train", "holdout"))
    ui.set_defaults(func=cmd_ui)

    apply_cmd = sub.add_parser("apply", help="从 JSON 写入标签")
    apply_cmd.add_argument("queue")
    apply_cmd.add_argument("labels")
    apply_cmd.set_defaults(func=cmd_apply)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
