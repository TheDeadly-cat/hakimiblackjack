# -*- coding: utf-8 -*-
"""建真实点数模板库，并在留出素材上评测。

build    : 系统字体模板起步 → 在真实画面上找高分样本 → 回炉成真实模板
evaluate : 在**另一个会话**的素材上跑真实模板，出带预测标注的拼版图供人工核对

素材分离是硬要求：build 与 evaluate 必须指向不同会话目录，
不能把同一张牌的相邻帧分到两边制造高准确率。

    python scripts/rank_bank.py build    .local-evidence/material-train-20260912
    python scripts/rank_bank.py evaluate .local-evidence/material-holdout-xxx --bank fixtures/vision/navy-live-felt-v1/ranks
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from blackjack_lab.vision.contracts import RANKS_13  # noqa: E402
from blackjack_lab.vision.real_cards import (  # noqa: E402
    TEMPLATE_H, Glyph, RankBank, extract_glyphs, match_glyph, matched_patch,
    render_font_templates, template_min_width,
)

SEED_MIN_SCORE = 0.55
SEED_MIN_MARGIN = 0.06
SEED_PER_RANK = 40
TILE = 96


def load_frames(directory: Path, limit: int, seed: int = 12345):
    frames = sorted((directory / "frames").glob("card-*.jpg"))
    if not frames:
        raise SystemExit(f"{directory}/frames 下没有 card-*.jpg")
    if len(frames) <= limit:
        return frames
    # 均匀抽样，避免只取到开头几轮
    step = len(frames) / float(limit)
    return [frames[int(i * step)] for i in range(limit)]


def collect_glyphs(cv2, frames, max_per_frame=40):
    out = []
    for path in frames:
        bgr = cv2.imread(str(path))
        if bgr is None:
            continue
        for glyph in extract_glyphs(bgr)[:max_per_frame]:
            out.append((path.name, glyph))
    return out


def cmd_build(args) -> int:
    import cv2
    import numpy as np

    directory = Path(args.material)
    frames = load_frames(directory, args.frames)
    print(f"起步模板：系统字体。素材 {directory.name}，取 {len(frames)} 帧")

    font_templates = render_font_templates()
    min_w = template_min_width(font_templates)

    glyphs = collect_glyphs(cv2, frames)
    print(f"抽出字形候选 {len(glyphs)} 个，开始旋转穷举匹配（较慢）…")

    buckets = {rank: [] for rank in RANKS_13}
    for index, (frame_name, glyph) in enumerate(glyphs):
        matches = match_glyph(glyph, font_templates)
        if not matches:
            continue
        best = matches[0]
        second = matches[1].score if len(matches) > 1 else -1.0
        if best.score < SEED_MIN_SCORE or (best.score - second) < SEED_MIN_MARGIN:
            continue
        patch = matched_patch(glyph, font_templates[best.rank],
                              best.angle, best.scale, min_w)
        if patch is None or patch.shape[0] != TEMPLATE_H:
            continue
        buckets[best.rank].append((best.score, patch))
        if (index + 1) % 100 == 0:
            got = sum(len(v) for v in buckets.values())
            print(f"  {index + 1}/{len(glyphs)} 已收 {got} 个种子")

    counts = {r: len(v) for r, v in buckets.items() if v}
    print(f"\n各点数种子数：{counts}")
    missing = [r for r in RANKS_13 if not buckets[r]]
    if missing:
        print(f"没有种子的点数：{missing}（这些点数将沿用字体模板，必须标为未验证）")

    real_templates = {}
    sample_counts = {}
    for rank in RANKS_13:
        samples = sorted(buckets[rank], key=lambda sv: sv[0], reverse=True)[:SEED_PER_RANK]
        if len(samples) < args.min_samples:
            real_templates[rank] = font_templates[rank]
            sample_counts[rank] = 0
            continue
        widths = [p.shape[1] for _s, p in samples]
        width = int(np.median(widths))
        stack = [cv2.resize(p, (width, TEMPLATE_H), interpolation=cv2.INTER_AREA)
                 for _s, p in samples]
        mean = np.mean(np.stack(stack).astype(np.float32), axis=0)
        real_templates[rank] = (mean >= 128).astype(np.uint8) * 255
        sample_counts[rank] = len(samples)

    bank = RankBank(real_templates,
                    origin=f"bootstrapped from {directory.name}",
                    sample_counts=sample_counts)
    out = Path(args.bank)
    bank.save(out)

    # 核对图：上排系统字体，下排真实回炉，附样本数
    sheet = np.full((2 * TILE + 30, len(RANKS_13) * TILE, 3), 30, dtype=np.uint8)
    for column, rank in enumerate(RANKS_13):
        for row, mask in enumerate((font_templates[rank], real_templates[rank])):
            rgb = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
            h, w = rgb.shape[:2]
            scale = min((TILE - 12) / w, (TILE - 12) / h)
            small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))))
            sh, sw = small.shape[:2]
            oy = row * TILE + (TILE - sh) // 2
            ox = column * TILE + (TILE - sw) // 2
            sheet[oy:oy + sh, ox:ox + sw] = small
        cv2.putText(sheet, f"{rank}:{sample_counts[rank]}",
                    (column * TILE + 4, 2 * TILE + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (0, 255, 255), 1, cv2.LINE_AA)
    cv2.imwrite(str(out / "bank-check.png"), sheet)

    print(f"\n模板库写入 {out}，digest {bank.digest}")
    print(f"核对图 {out / 'bank-check.png'}（上排字体起步，下排真实回炉）")
    return 0


def cmd_evaluate(args) -> int:
    import cv2
    import numpy as np

    directory = Path(args.material)
    bank = RankBank.load(args.bank)
    min_w = template_min_width(bank.templates)
    frames = load_frames(directory, args.frames)
    print(f"留出素材 {directory.name}，取 {len(frames)} 帧，模板 digest {bank.digest}")

    glyphs = collect_glyphs(cv2, frames)
    random.Random(args.seed).shuffle(glyphs)
    glyphs = glyphs[:args.sample]
    print(f"随机抽 {len(glyphs)} 个字形候选评测（较慢）…")

    rows = []
    tiles = []
    for frame_name, glyph in glyphs:
        matches = match_glyph(glyph, bank.templates)
        if not matches:
            rows.append({"frame": frame_name, "predicted": None, "score": None})
            continue
        best = matches[0]
        second = matches[1].score if len(matches) > 1 else -1.0
        accepted = best.score >= args.accept and (best.score - second) >= args.margin
        rows.append({
            "frame": frame_name,
            "bbox": list(glyph.bbox),
            "ink": glyph.ink,
            "predicted": best.rank,
            "score": round(best.score, 4),
            "margin": round(best.score - second, 4),
            "angle": best.angle,
            "accepted": accepted,
        })
        tiles.append((glyph, best, accepted))

    columns = 10
    rows_n = (len(tiles) + columns - 1) // columns
    sheet = np.full((rows_n * (TILE + 22), columns * TILE, 3), 30, dtype=np.uint8)
    for index, (glyph, best, accepted) in enumerate(tiles):
        rgb = cv2.cvtColor(glyph.mask, cv2.COLOR_GRAY2BGR)
        h, w = rgb.shape[:2]
        scale = min((TILE - 10) / w, (TILE - 10) / h)
        small = cv2.resize(rgb, (max(1, int(w * scale)), max(1, int(h * scale))))
        sh, sw = small.shape[:2]
        r, c = divmod(index, columns)
        oy = r * (TILE + 22) + (TILE - sh) // 2
        ox = c * TILE + (TILE - sw) // 2
        sheet[oy:oy + sh, ox:ox + sw] = small
        colour = (0, 255, 0) if accepted else (0, 165, 255)
        cv2.putText(sheet, f"{index}:{best.rank} {best.score:.2f}",
                    (c * TILE + 3, r * (TILE + 22) + TILE + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.36, colour, 1, cv2.LINE_AA)
    out = directory / "predicted-sheet.png"
    cv2.imwrite(str(out), sheet)

    accepted_rows = [r for r in rows if r.get("accepted")]
    print(f"\n候选 {len(rows)}，达到接受门槛 {len(accepted_rows)} "
          f"（score>={args.accept} 且分差>={args.margin}）")
    dist = {}
    for r in accepted_rows:
        dist[r["predicted"]] = dist.get(r["predicted"], 0) + 1
    print(f"接受项点数分布：{dict(sorted(dist.items()))}")
    print(f"\n预测拼版图 {out}")
    print("准确率必须由人工核对该图得出；本脚本不自称准确率。")

    (directory / "evaluation.json").write_text(json.dumps({
        "material": directory.name,
        "bank_digest": bank.digest,
        "bank_origin": bank.origin,
        "accept_threshold": args.accept,
        "margin_threshold": args.margin,
        "sampled": len(rows),
        "accepted": len(accepted_rows),
        "distribution": dist,
        "rows": rows,
        "note": "预测未经人工核对前不得当作准确率。",
    }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="真实点数模板库")
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="从训练素材回炉真实模板")
    build.add_argument("material")
    build.add_argument("--frames", type=int, default=40)
    build.add_argument("--min-samples", type=int, default=3)
    build.add_argument("--bank", default="fixtures/vision/navy-live-felt-v1/ranks")
    build.set_defaults(func=cmd_build)

    evaluate = sub.add_parser("evaluate", help="在留出素材上评测")
    evaluate.add_argument("material")
    evaluate.add_argument("--bank", default="fixtures/vision/navy-live-felt-v1/ranks")
    evaluate.add_argument("--frames", type=int, default=30)
    evaluate.add_argument("--sample", type=int, default=100)
    evaluate.add_argument("--accept", type=float, default=0.60)
    evaluate.add_argument("--margin", type=float, default=0.06)
    evaluate.add_argument("--seed", type=int, default=7)
    evaluate.set_defaults(func=cmd_evaluate)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
