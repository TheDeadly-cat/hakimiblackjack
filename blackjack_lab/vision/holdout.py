# -*- coding: utf-8 -*-
"""synthetic-felt-v1 独立测试集生成。模板图与 holdout 原图分开，禁止把模板图当测试。"""
from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Dict, List

from .contracts import RANKS_13, default_layout
from .synthetic import (
    PlacedCard, UNSUPPORTED_FELT, render_table, smoke_all13_cards, smoke_backs,
    smoke_occluded, smoke_two_eights, write_templates,
)

HOLDOUT_SEED = 20260912
# R3 冻结：至少 1000 张可辨认实例、每类 ≥50、干扰 ≥200。双区各放一张，保证数量。
R3_PRIMARY_PER_RANK = 55
R3_IDENTIFIABLE_FRAMES = R3_PRIMARY_PER_RANK * 13
R3_EMPTY = 80
R3_BACK = 60
R3_OCCLUDED = 40
R3_UNSUPPORTED = 20
R3_INTERFERENCE = R3_EMPTY + R3_BACK + R3_OCCLUDED + R3_UNSUPPORTED


def _jpeg_roundtrip_png(path: Path, quality: int) -> None:
    from .deps import load_cv2, load_numpy
    from .image_io import write_png_rgb
    cv2 = load_cv2()
    np = load_numpy()
    data = np.fromfile(str(path), dtype=np.uint8)
    bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
    ok, encoded = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return
    decoded = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(decoded, cv2.COLOR_BGR2RGB)
    write_png_rgb(path, int(rgb.shape[1]), int(rgb.shape[0]), rgb.tobytes())


def write_smoke_bundle(root: Path) -> Dict[str, str]:
    root = Path(root)
    (root / "smoke").mkdir(parents=True, exist_ok=True)
    mapping = {
        "all13.png": smoke_all13_cards(),
        "two_eights.png": smoke_two_eights(),
        "empty.png": [],
        "backs.png": smoke_backs(),
        "occluded.png": smoke_occluded(),
    }
    labels = {}
    for name, cards in mapping.items():
        canvas, lab = render_table(cards)
        dest = root / "smoke" / name
        canvas.save_png(dest)
        labels[name] = lab
    (root / "smoke" / "labels.json").write_text(
        json.dumps(labels, ensure_ascii=False, indent=2), encoding="utf-8")
    return {name: str(root / "smoke" / name) for name in mapping}


def write_holdout_bundle(root: Path, *, n_identifiable: int | None = None,
                         n_empty: int = R3_EMPTY, n_back: int = R3_BACK,
                         n_occluded: int = R3_OCCLUDED, n_unsupported: int = R3_UNSUPPORTED,
                         seed: int = HOLDOUT_SEED) -> Path:
    """生成与模板图不同的整桌画面。同一原图不会再切进模板目录。"""
    root = Path(root)
    holdout = root / "holdout"
    images = holdout / "images"
    images.mkdir(parents=True, exist_ok=True)
    for old in images.glob("*.png"):
        old.unlink()
    if n_identifiable is None:
        n_identifiable = R3_IDENTIFIABLE_FRAMES
    rng = random.Random(seed)
    layout = default_layout()
    records: List[dict] = []

    def place_in(region_id: str, rank, **kwargs) -> PlacedCard:
        region = layout.region(region_id)
        scale = rng.uniform(0.92, 1.08)
        w = max(50, int(round(90 * scale)))
        h = max(70, int(round(126 * scale)))
        x = rng.randint(region.x + 8, region.x + region.w - w - 8)
        y = rng.randint(region.y + 8, region.y + region.h - h - 8)
        return PlacedCard(rank, x, y, region_id, scale=scale, **kwargs)

    idx = 0
    # 每类约 n_identifiable/13 张，分散在不同画面，避免与模板图相同构图。
    per_rank = max(1, n_identifiable // 13)
    for rank in RANKS_13:
        for _ in range(per_rank):
            region_id = rng.choice(list(layout.regions))
            other_region = "player_target" if region_id == "dealer" else "dealer"
            other = rng.choice(RANKS_13)
            cards = [
                place_in(region_id, rank, suit=rng.choice([None, "C", "D"])),
                place_in(other_region, other, suit=rng.choice([None, "S", "H"])),
            ]
            filtered = cards
            name = f"id_{idx:04d}.png"
            canvas, lab = render_table(filtered)
            path = images / name
            canvas.save_png(path)
            if rng.random() < 0.4:
                _jpeg_roundtrip_png(path, rng.randint(72, 90))
            records.append({"file": f"images/{name}", "split": "holdout", "labels": lab})
            idx += 1

    for kind, count, builder in (
        ("empty", n_empty, lambda: []),
        ("back", n_back, lambda: [place_in("dealer", None, face="back")]),
        ("occluded", n_occluded, lambda: [place_in("player_target", rng.choice(RANKS_13), occlude_index=True)]),
        ("unsupported", n_unsupported, lambda: [place_in("player_target", rng.choice(RANKS_13))]),
    ):
        for _ in range(count):
            cards = builder()
            name = f"{kind}_{idx:04d}.png"
            felt = UNSUPPORTED_FELT if kind == "unsupported" else None
            canvas, lab = render_table(cards, felt=felt)
            path = images / name
            canvas.save_png(path)
            records.append({"file": f"images/{name}", "split": "holdout", "kind": kind, "labels": lab})
            idx += 1

    manifest = {
        "style_id": "synthetic-felt-v1",
        "platform_claim": "none",
        "seed": seed,
        "r3_frozen_counts": {
            "identifiable_frames": n_identifiable,
            "empty": n_empty,
            "back": n_back,
            "occluded": n_occluded,
            "unsupported": n_unsupported,
        },
        "templates_are_isolated_index_crops": True,
        "holdout_uses_full_table_renders": True,
        "do_not_score_human_corrections_as_model": True,
        "records": records,
    }
    dest = holdout / "manifest.json"
    dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return dest


def write_bundle(root: Path) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    hashes = write_templates(root / "templates")
    write_smoke_bundle(root)
    (root / "templates" / "manifest.json").write_text(
        json.dumps({"style_id": "synthetic-felt-v1", "hashes": hashes,
                    "note": "模板只来自孤立角标，不来自 holdout 原图"},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
