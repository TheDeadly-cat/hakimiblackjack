# -*- coding: utf-8 -*-
"""从采集会话抽出角标裁片，按局标成 train / holdout，供人工标注。

默认每局取白像素最多的一帧（牌最多的瞬间），避免把同一张牌的相邻帧两边都送进模型。
原视频/原帧只读。输出只含牌区裁片，仍放 .local-evidence/。

    python scripts/prepare_glyph_queue.py .local-evidence/material-train-20260912
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.vision.glyph_dataset import (  # noqa: E402
    GlyphItem, assign_splits, crop_id_for, detect_round_ids, load_queue, save_queue,
)
from blackjack_lab.vision.real_cards import extract_glyphs, extraction_diagnostics, EXTRACTION_VERSION  # noqa: E402
from blackjack_lab.vision.frame_annotations import material_identity, read_frame  # noqa: E402

CONTEXT_PAD = 18


def load_manifest(session_dir: Path) -> dict:
    path = session_dir / "manifest.json"
    if not path.is_file():
        raise SystemExit(f"找不到 {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def pick_frames(manifest: dict, round_ids: list, *, per_round: int) -> list:
    frames = manifest["frames"]
    buckets: dict = {}
    for frame, rid in zip(frames, round_ids):
        buckets.setdefault(rid, []).append(frame)
    chosen = []
    for rid in sorted(buckets):
        ranked = sorted(buckets[rid], key=lambda f: int(f.get("white_pixels") or 0), reverse=True)
        chosen.extend((rid, frame) for frame in ranked[:per_round])
    return chosen


def context_crop(bgr, bbox, pad: int = CONTEXT_PAD):
    x, y, w, h = bbox
    y0 = max(0, y - pad)
    x0 = max(0, x - pad)
    y1 = min(bgr.shape[0], y + h + pad)
    x1 = min(bgr.shape[1], x + w + pad)
    return bgr[y0:y1, x0:x1]


def draw_overlay(cv2, bgr, items: list):
    canvas = bgr.copy()
    for index, item in enumerate(items):
        x, y, w, h = item.bbox
        cv2.rectangle(canvas, (x, y), (x + w, y + h), (0, 255, 180), 1)
        cv2.putText(
            canvas, str(index), (x, max(12, y - 2)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    return canvas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="抽出角标标注队列")
    parser.add_argument("session", help="capture_material.py 产出的会话目录")
    parser.add_argument("--output", help="默认 <session>/glyph-queue")
    parser.add_argument("--per-round", type=int, default=1, help="每局取样帧数")
    parser.add_argument("--holdout-frac", type=float, default=0.30)
    parser.add_argument("--max-glyphs", type=int, default=0, help="单帧候选上限；0表示全部保留")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--append", action="store_true",
                        help="追加新帧，保留已有标签；默认只追加训练局")
    parser.add_argument("--include-holdout", action="store_true",
                        help="追加时也取样留出局（会冻住切分，但仍可能让留出更像调参）")
    args = parser.parse_args(argv)
    if args.per_round < 1 or args.max_glyphs < 0:
        parser.error("per-round 必须为正数；max-glyphs 必须为非负数")

    import cv2
    import numpy as np

    session_dir = Path(args.session)
    out = Path(args.output or (session_dir / "glyph-queue"))
    if out.exists() and (out / "queue.jsonl").exists() and not args.force and not args.append:
        print(f"已有 {out / 'queue.jsonl'}，拒绝覆盖。追加请加 --append，重建请加 --force。")
        return 1

    manifest = load_manifest(session_dir)
    frames = manifest.get("frames") or []
    if not frames:
        print("manifest 里没有帧")
        return 1
    round_ids = detect_round_ids(frames)
    existing: list[GlyphItem] = []
    previous_split = {}
    if args.append and (out / "queue.jsonl").exists():
        existing = load_queue(out)
        split_path = out / "split.json"
        if split_path.is_file():
            previous_split = json.loads(split_path.read_text(encoding="utf-8"))
            frozen = previous_split.get("rounds") or {}
            split_of = {int(k): v for k, v in frozen.items()}
        else:
            split_of = assign_splits(round_ids, holdout_frac=args.holdout_frac)
        print(f"追加到已有 {len(existing)} 个裁片，切分保持不变")
        # 保存追加前的队列与截断证据；默认只追加训练帧不能修复旧留出帧遗漏。
        history = out / "history" / uuid4().hex
        history.mkdir(parents=True, exist_ok=False)
        (history / "queue.jsonl").write_bytes((out / "queue.jsonl").read_bytes())
        if split_path.is_file():
            (history / "split.json").write_bytes(split_path.read_bytes())
    else:
        split_of = assign_splits(round_ids, holdout_frac=args.holdout_frac)

    picked = pick_frames(manifest, round_ids, per_round=args.per_round)
    if args.append and not args.include_holdout:
        picked = [(rid, frame) for rid, frame in picked if split_of.get(int(rid)) != "holdout"]

    crops_dir = out / "crops"
    masks_dir = out / "masks"
    overlays_dir = out / "overlays"
    for folder in (crops_dir, masks_dir, overlays_dir):
        folder.mkdir(parents=True, exist_ok=True)

    session_name = manifest.get("session") or session_dir.name
    try:
        source_sha256 = material_identity(session_dir, manifest)
    except (OSError, ValueError) as exc:
        (out / "preparation-error.json").write_text(json.dumps({
            "valid": False, "stage": "source_identity", "error": str(exc),
            "note": "来源缺失，未生成新队列；既有标签保持不变。"
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"来源不完整：{exc}")
        return 2
    source_complete = not (manifest.get("valid") is False or manifest.get("decode_errors") or manifest.get("truncated"))
    previous_complete = not (previous_split.get("valid") is False
                            or previous_split.get("unreadable_frames")
                            or previous_split.get("truncated_candidates"))
    by_id = {item.crop_id: item for item in existing}
    overlay_index = {}
    index_path = overlays_dir / "index.json"
    if index_path.is_file():
        overlay_index = json.loads(index_path.read_text(encoding="utf-8"))
    added = 0
    failures = []
    truncated = []
    for rid, frame in picked:
        try:
            bgr, frame_sha256 = read_frame(session_dir, frame["file"])
        except (OSError, ValueError) as exc:
            failures.append({"frame": frame["file"], "error": str(exc)})
            continue
        all_glyphs = extract_glyphs(bgr)
        if args.max_glyphs > 0 and len(all_glyphs) > args.max_glyphs:
            truncated.append({"frame": frame["file"], "omitted": len(all_glyphs) - args.max_glyphs})
        glyphs = all_glyphs[:args.max_glyphs] if args.max_glyphs > 0 else all_glyphs
        (overlays_dir / f"{Path(frame['file']).stem}-diagnostics.json").write_text(
            json.dumps(extraction_diagnostics(bgr), ensure_ascii=False, indent=2), encoding="utf-8")
        frame_items = []
        new_here = 0
        for glyph in glyphs:
            cid = crop_id_for(session_name, frame["file"], glyph.bbox)
            crop_name = f"{cid}.png"
            if cid in by_id:
                frame_items.append(by_id[cid])
                continue
            cv2.imwrite(str(crops_dir / crop_name), context_crop(bgr, glyph.bbox))
            cv2.imwrite(str(masks_dir / crop_name), glyph.mask)
            item = GlyphItem(
                crop_id=cid,
                session=session_name,
                frame=frame["file"],
                frame_signature=str(frame.get("signature") or ""),
                round_id=int(rid),
                split=split_of[int(rid)],
                bbox=tuple(int(v) for v in glyph.bbox),
                ink=glyph.ink,
                crop_file=f"crops/{crop_name}",
                mask_file=f"masks/{crop_name}",
                elapsed_s=frame.get("elapsed_s"),
                source_sha256=source_sha256,
                frame_sha256=frame_sha256,
                crop_sha256=hashlib.sha256((crops_dir / crop_name).read_bytes()).hexdigest(),
                origin_crop_id=cid,
                extraction_method=EXTRACTION_VERSION,
            )
            by_id[cid] = item
            frame_items.append(item)
            new_here += 1
            added += 1
        overlay = draw_overlay(cv2, bgr, frame_items)
        overlay_name = f"{Path(frame['file']).stem}.png"
        cv2.imwrite(str(overlays_dir / overlay_name), overlay)
        overlay_index[Path(frame["file"]).stem] = {
            "frame": frame["file"],
            "round_id": int(rid),
            "split": split_of[int(rid)],
            "crop_ids": [it.crop_id for it in frame_items],
        }
        print(f"{frame['file']}  round {rid}  {split_of[int(rid)]}  "
              f"glyphs {len(frame_items)}  +{new_here}")

    items = list(by_id.values())
    save_queue(items, out)
    (out / "overlays" / "index.json").write_text(
        json.dumps(overlay_index, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "split.json").write_text(json.dumps({
        "session": session_name,
        "holdout_frac": args.holdout_frac,
        "per_round": args.per_round,
        "n_frames_sampled": len({it.frame for it in items}),
        "n_glyphs": len(items),
        "n_train": sum(1 for i in items if i.split == "train"),
        "n_holdout": sum(1 for i in items if i.split == "holdout"),
        "source_sha256": source_sha256,
        "extraction_version": EXTRACTION_VERSION,
        "unreadable_frames": failures,
        "truncated_candidates": truncated,
        "valid": source_complete and previous_complete and not failures and not truncated,
        "previous_preparation_complete": previous_complete,
        "prior_unreadable_frames": previous_split.get("unreadable_frames", []) + previous_split.get("prior_unreadable_frames", []),
        "prior_truncated_candidates": previous_split.get("truncated_candidates", []) + previous_split.get("prior_truncated_candidates", []),
        "append_note": "旧的不完整证据保留在history；追加不能证明旧留出全部重提取，完整评测请使用新输出全量准备。" if not previous_complete else "",
        "source_complete": source_complete,
        "source_decode_errors": manifest.get("decode_errors", []),
        "source_truncated": bool(manifest.get("truncated")),
        "appended": added if args.append else 0,
        "rounds": {str(k): v for k, v in split_of.items()},
        "note": "按整局切分。同一局的帧不会同时出现在训练与留出。追加默认不碰留出局。",
    }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    n_train = sum(1 for i in items if i.split == "train")
    n_holdout = sum(1 for i in items if i.split == "holdout")
    unlabeled = [i for i in items if i.label is None]
    write_contact_sheets(cv2, np, out, items)
    write_contact_sheets(cv2, np, out, unlabeled, prefix="unlabeled")
    print(f"\n写出 {len(items)} 个裁片  训练 {n_train}  留出 {n_holdout}  未标 {len(unlabeled)}")
    if args.append:
        print(f"本轮新增 {added}")
    print(f"队列 {out / 'queue.jsonl'}")
    print(f"编号叠加图 {overlays_dir}，拼版图 {out / 'sheets'}")
    print("标注：python scripts/label_glyphs.py ui <队列目录>")
    return 0 if source_complete and previous_complete and not failures and not truncated else 2


def write_contact_sheets(cv2, np, out: Path, items: list, chunk: int = 40,
                         prefix: str | None = None) -> None:
    """大号编号拼版，方便人眼标；叠加全图上的编号太小。"""
    from math import ceil
    if not items:
        return
    sheets = out / "sheets"
    sheets.mkdir(parents=True, exist_ok=True)
    tile, cols = 110, 8
    groups: dict[str, list] = {}
    if prefix:
        groups[prefix] = list(items)
    else:
        for item in items:
            groups.setdefault(item.split, []).append(item)
    for name, subset in groups.items():
        for start in range(0, len(subset), chunk):
            part = subset[start:start + chunk]
            rows = max(1, ceil(len(part) / cols))
            canvas = np.full((rows * tile, cols * tile, 3), 30, dtype=np.uint8)
            mapping = []
            for index, item in enumerate(part):
                crop = cv2.imread(str(out / item.crop_file))
                r, c = divmod(index, cols)
                if crop is not None:
                    h, w = crop.shape[:2]
                    scale = min((tile - 22) / max(w, 1), (tile - 22) / max(h, 1))
                    small = cv2.resize(
                        crop,
                        (max(1, int(w * scale)), max(1, int(h * scale))),
                        interpolation=cv2.INTER_CUBIC,
                    )
                    sh, sw = small.shape[:2]
                    oy = r * tile + 18 + (tile - 22 - sh) // 2
                    ox = c * tile + (tile - sw) // 2
                    canvas[oy:oy + sh, ox:ox + sw] = small
                colour = (0, 255, 255) if item.split == "train" else (0, 180, 255)
                cv2.putText(
                    canvas, str(index), (c * tile + 4, r * tile + 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
                mapping.append({
                    "i": index,
                    "crop_id": item.crop_id,
                    "split": item.split,
                    "frame": item.frame,
                    "round_id": item.round_id,
                    "label": item.label,
                })
            sheet_name = f"{name}-{start // chunk:02d}"
            cv2.imwrite(str(sheets / f"{sheet_name}.png"), canvas)
            (sheets / f"{sheet_name}.json").write_text(
                json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
