# -*- coding: utf-8 -*-
"""原帧盲标/漏检补框：init 新清单，ui 人工框选，queue 导出独立补样队列。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.frame_annotations import (
    ANNOTATION_SCHEMA, build_annotation_queue, material_identity, read_frame,
)
from blackjack_lab.vision.glyph_dataset import detect_round_ids, assign_splits, LABEL_RANKS


def initialize(session, output, *, step=60):
    out = Path(output)
    if out.exists() or step <= 0:
        raise ValueError("请选择新输出文件和正数步长")
    root = Path(session)
    m = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    rounds = detect_round_ids(m["frames"])
    splits = assign_splits(rounds) if len(set(rounds)) >= 3 else {r: "train" for r in rounds}
    records = []
    for i in range(0, len(m["frames"]), step):
        f = m["frames"][i]
        _, digest = read_frame(root, f["file"])
        records.append({"file": f["file"], "sha256": digest, "round_id": rounds[i],
                        "split": splits[rounds[i]], "complete": False, "objects": []})
    data = {"schema": ANNOTATION_SCHEMA, "session": m["session"],
            "source_sha256": material_identity(root, m), "role": "development",
            "source_complete": not (m.get("valid") is False or m.get("decode_errors") or m.get("truncated")),
            "selection": {"method": "fixed_manifest_step", "step": step},
            "note": "未展示检测器预测。complete 表示该抽样帧全部目标已人工检查；不代表整段录像逐帧覆盖。",
            "frames": records}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def review_ui(session, annotation_file):
    import tkinter as tk
    from tkinter import ttk, messagebox
    from PIL import Image, ImageTk
    path = Path(annotation_file)
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != ANNOTATION_SCHEMA or data["source_sha256"] != material_identity(session):
        raise ValueError("标注格式或来源摘要不符")
    if not data["frames"]:
        raise ValueError("标注清单没有原帧")
    root = tk.Tk()
    root.title("原帧核对与漏检补框 · 原素材只读")
    index, state = [0], {}
    bar = ttk.Frame(root); bar.pack(fill="x")
    reviewer = tk.StringVar(); rank = tk.StringVar(value="Q")
    physical = tk.StringVar(); reason = tk.StringVar(value="检测器漏候选")
    region = tk.StringVar(value="unassigned")
    for label, var, width in (("复核人", reviewer, 14), ("物理牌ID（跨帧同牌保持一致）", physical, 20),
                              ("归属区域", region, 16), ("补框原因", reason, 22)):
        ttk.Label(bar, text=label).pack(side="left")
        ttk.Entry(bar, textvariable=var, width=width).pack(side="left")
    ttk.Combobox(bar, textvariable=rank, values=LABEL_RANKS + ("unreadable",), width=10, state="readonly").pack(side="left")
    status = tk.StringVar(); ttk.Label(root, textvariable=status).pack()
    selection_bar = ttk.Frame(root); selection_bar.pack(fill="x")
    selected = tk.StringVar()
    selector = ttk.Combobox(selection_bar, textvariable=selected, width=85, state="readonly")
    selector.pack(side="left")
    ttk.Label(root, text="拖动框选可读点数角标，保留其原方向；每张物理牌一框。非牌选 junk。"
                        "先删旧框再修正；完成整帧核对后才点“本帧全部核对完成”。").pack()
    canvas = tk.Canvas(root, width=1200, height=500, background="#202020"); canvas.pack()
    footer = ttk.Frame(root); footer.pack(fill="x")

    def current():
        return data["frames"][index[0]]

    def save():
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def show():
        record = current()
        bgr, digest = read_frame(session, record["file"])
        if record["sha256"] != digest:
            raise ValueError("原帧已改变，停止标注")
        im = Image.fromarray(bgr[:, :, ::-1]); scale = min(1.0, 1200/im.width, 700/im.height)
        state["scale"] = scale
        im = im.resize((round(im.width*scale), round(im.height*scale)))
        state["photo"] = ImageTk.PhotoImage(im)
        canvas.configure(width=im.width, height=im.height)
        canvas.delete("all"); canvas.create_image(0, 0, anchor="nw", image=state["photo"])
        for obj in record["objects"]:
            x,y,w,h = obj["bbox"]
            canvas.create_rectangle(x*scale, y*scale, (x+w)*scale, (y+h)*scale, outline="yellow", width=2)
            canvas.create_text(x*scale, max(8,y*scale-8), text=f"{obj['rank']} {obj['physical_card_id']}", fill="yellow", anchor="w")
        selector.configure(values=[f"{i} · {o['rank']} · {o['physical_card_id']} · {o.get('label_provenance','unspecified')}"
                                   for i,o in enumerate(record["objects"])])
        selected.set("")
        status.set(f"{index[0]+1}/{len(data['frames'])} · {record['file']} · round {record['round_id']} · "
                   f"{record['split']} · {len(record['objects'])}框 · 全帧核对 {record['complete']}")

    def begin(event):
        state["start"] = (event.x, event.y)
        canvas.delete("drag")

    def drag(event):
        if "start" in state:
            canvas.delete("drag")
            canvas.create_rectangle(*state["start"], event.x, event.y, outline="cyan", tags="drag")

    def finish(event):
        if "start" not in state:
            return
        sx, sy = state.pop("start"); scale = state["scale"]
        if not reviewer.get().strip() or not physical.get().strip():
            messagebox.showinfo("填写身份", "请先填写复核人和物理牌 ID。", parent=root); return
        x,y = round(min(sx,event.x)/scale), round(min(sy,event.y)/scale)
        w,h = round(abs(sx-event.x)/scale), round(abs(sy-event.y)/scale)
        if min(w,h) < 3:
            return
        current()["objects"].append({"bbox": [x,y,w,h], "rank": rank.get(),
                                     "physical_card_id": physical.get().strip(), "region_id": region.get(),
                                     "rejection_reason": reason.get(), "label_provenance": "human_reviewed",
                                     "reviewed_by": reviewer.get().strip(),
                                     "reviewed_at": datetime.now(timezone.utc).isoformat()})
        current()["complete"] = False; save(); show()

    def move(delta):
        index[0] = min(len(data["frames"])-1, max(0,index[0]+delta)); show()

    def undo():
        if current()["objects"]:
            current()["objects"].pop(); current()["complete"] = False; save(); show()

    def complete():
        if not reviewer.get().strip():
            messagebox.showinfo("填写身份", "请填写复核人。", parent=root); return
        if any(o.get("label_provenance") != "human_reviewed" for o in current()["objects"]):
            messagebox.showinfo("仍有待复核框", "请逐个核对现有框及点数，再确认整帧。", parent=root); return
        current()["complete"] = True
        current()["reviewed_by"] = reviewer.get().strip()
        current()["reviewed_at"] = datetime.now(timezone.utc).isoformat()
        save(); show()

    def select_existing(_event=None):
        if selector.current() >= 0:
            obj = current()["objects"][selector.current()]
            rank.set(obj["rank"]); physical.set(obj["physical_card_id"])
            region.set(obj.get("region_id", "unassigned")); reason.set(obj.get("rejection_reason", ""))

    def confirm_existing():
        position = selector.current()
        if position < 0 or not reviewer.get().strip() or not physical.get().strip():
            messagebox.showinfo("选择待复核框", "请选择现有框，并填写复核人、物理牌 ID。", parent=root); return
        current()["objects"][position].update(rank=rank.get(), physical_card_id=physical.get().strip(),
            region_id=region.get(), rejection_reason=reason.get(), label_provenance="human_reviewed",
            reviewed_by=reviewer.get().strip(), reviewed_at=datetime.now(timezone.utc).isoformat())
        current()["complete"] = False; save(); show()

    selector.bind("<<ComboboxSelected>>", select_existing)
    ttk.Button(selection_bar, text="确认／修正所选框", command=confirm_existing).pack(side="left", padx=8)

    for title, command in (("上一帧", lambda: move(-1)), ("下一帧", lambda: move(1)),
                            ("撤销最后一框", undo), ("本帧全部核对完成", complete), ("保存并关闭", root.destroy)):
        ttk.Button(footer, text=title, command=command).pack(side="left", padx=8)
    canvas.bind("<ButtonPress-1>", begin); canvas.bind("<B1-Motion>", drag); canvas.bind("<ButtonRelease-1>", finish)
    show(); root.mainloop(); save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init"); init.add_argument("session"); init.add_argument("annotations"); init.add_argument("--step", type=int, default=60)
    ui = sub.add_parser("ui"); ui.add_argument("session"); ui.add_argument("annotations")
    queue = sub.add_parser("queue"); queue.add_argument("session"); queue.add_argument("annotations"); queue.add_argument("output")
    args = parser.parse_args(argv)
    if args.command == "init":
        print(f"初始化 {len(initialize(args.session,args.annotations,step=args.step)['frames'])} 帧")
    elif args.command == "ui":
        review_ui(args.session, args.annotations)
    else:
        print(f"导出 {len(build_annotation_queue(args.session,args.annotations,args.output))} 个补框样本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
