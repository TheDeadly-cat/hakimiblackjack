"""Felt-occupancy round pages from a local video. Never invents ranks or accepts."""
from __future__ import annotations

import json
from pathlib import Path

from .evidence import sha256_file
from .shoe_event_draft import add_event, empty_draft

SCHEMA = "hakimi-shoe-round-scan-v1"
NOTE = (
    "按绒面亮像素占用切出待核对轮次页。占用不是牌数，也不能当逐牌真值。"
    "NVIDIA 录制面板打开时绒面裁区不可用，记为遮挡。"
    "红色切牌卡若未被这套占用规则看到，必须保持未知，不得计入记牌。"
)
_STYLE = Path(__file__).resolve().parents[1] / "vision" / "styles" / "navy_live_felt_v1.json"


def load_navy_crops(style_path=_STYLE):
    data = json.loads(Path(style_path).read_text(encoding="utf-8"))
    source = data["source_crop"]
    felt = data["felt_crop"]
    return {
        "source": (int(source["x"]), int(source["y"]), int(source["w"]), int(source["h"])),
        "felt": (int(felt["x"]), int(felt["y"]), int(felt["w"]), int(felt["h"])),
        "frame": (int(data["source_frame"]["width"]), int(data["source_frame"]["height"])),
        "style_id": data.get("style_id"),
        "regions": data.get("regions") or {},
    }


def crop_style_region(still_path, region_id, dest_path, *, style_path=_STYLE):
    """Crop a named felt region. Ranks are not read."""
    from ..vision.deps import load_cv2

    still = Path(still_path)
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    crops = load_navy_crops(style_path)
    box = (crops.get("regions") or {}).get(region_id)
    if not box:
        raise KeyError(region_id)
    cv2 = load_cv2()
    bgr = cv2.imread(str(still))
    if bgr is None:
        raise RuntimeError(f"无法读取静帧: {still}")
    x, y, w, h = int(box["x"]), int(box["y"]), int(box["w"]), int(box["h"])
    crop = _crop(bgr, (x, y, w, h))
    if crop.size == 0:
        raise RuntimeError("区域裁切为空")
    cv2.imwrite(str(dest), crop)
    return {
        "accepted": False,
        "region_id": region_id,
        "seat_hint": box.get("seat_hint"),
        "path": str(dest),
        "box": [x, y, w, h],
        "ranks_invented": False,
    }


def _crop(bgr, box):
    x, y, w, h = box
    height, width = bgr.shape[:2]
    x2 = min(width, x + w)
    y2 = min(height, y + h)
    x = max(0, x)
    y = max(0, y)
    if x2 <= x or y2 <= y:
        return bgr[0:0, 0:0]
    return bgr[y:y2, x:x2]


def nvidia_overlay_likely(bgr):
    """Left charcoal NVIDIA panel against a brighter desktop. Not an F11 result."""
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return False
    height, width = bgr.shape[:2]
    if width < 80 or height < 40:
        return False
    left_w = min(width, max(24, width // 4))
    left = bgr[int(height * 0.12):int(height * 0.72), 0:left_w]
    right = bgr[int(height * 0.12):int(height * 0.72), width // 2:]
    if left.size == 0 or right.size == 0:
        return False
    left_mean = float(left.mean())
    right_mean = float(right.mean())
    return left_mean < 55 and right_mean > 80


def waiting_next_round_likely(felt_bgr):
    """Top-center dark pill with light text, as on Stake「等待下一局游戏」.

    Leftover cards on that screen are the previous round, not a new deal.
    """
    if felt_bgr is None or getattr(felt_bgr, "size", 0) == 0:
        return False
    height, width = felt_bgr.shape[:2]
    if height < 24 or width < 48:
        return False
    y2 = max(8, int(height * 0.12))
    x1, x2 = int(width * 0.32), int(width * 0.68)
    band = felt_bgr[0:y2, x1:x2]
    if band.size == 0:
        return False
    luma = (
        0.114 * band[:, :, 0]
        + 0.587 * band[:, :, 1]
        + 0.299 * band[:, :, 2]
    )
    dark = float((luma < 45).mean())
    bright = float((luma > 170).mean())
    return dark >= 0.18 and 0.015 <= bright <= 0.45


def chip_strip_likely(felt_bgr):
    """Stake chip-denomination overlay: a row of similar saturated circles.

    That UI appears with leftover cards and「立即发牌」, not as a new deal.
    """
    if felt_bgr is None or getattr(felt_bgr, "size", 0) == 0:
        return False
    height, width = felt_bgr.shape[:2]
    if height < 40 or width < 80:
        return False
    try:
        from ..vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
    except Exception:
        return False
    y1, y2 = int(height * 0.06), int(height * 0.34)
    x1, x2 = int(width * 0.22), int(width * 0.78)
    band = felt_bgr[y1:y2, x1:x2]
    if band.size == 0:
        return False
    blue = band[:, :, 0].astype(np.float32)
    green = band[:, :, 1].astype(np.float32)
    red = band[:, :, 2].astype(np.float32)
    maximum = np.maximum(np.maximum(red, green), blue)
    minimum = np.minimum(np.minimum(red, green), blue)
    sat = (maximum - minimum) / (maximum + 1.0)
    luma = 0.114 * blue + 0.587 * green + 0.299 * red
    mask = ((sat > 0.55) & (maximum > 100) & (luma > 50) & (luma < 210)).astype(np.uint8) * 255
    count, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    blobs = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        bw = int(stats[index, cv2.CC_STAT_WIDTH])
        bh = int(stats[index, cv2.CC_STAT_HEIGHT])
        if 40 <= area <= 900 and 8 <= bw and 8 <= bh and 0.55 <= bw / (bh + 1e-6) <= 1.8:
            blobs.append((float(cents[index, 0]), float(cents[index, 1])))
    if len(blobs) < 5:
        return False
    median_y = float(np.median([item[1] for item in blobs]))
    row = sum(abs(item[1] - median_y) < 12 for item in blobs)
    return row >= 5


def between_round_ui_likely(felt_bgr):
    """Waiting banner or chip strip: leftover cards, not a new occupancy deal."""
    return waiting_next_round_likely(felt_bgr) or chip_strip_likely(felt_bgr)


PLACEHOLDER_SOURCE = "felt_bright_region_v1"
_PLACEHOLDER_MAX = 12


def felt_card_placeholders(felt_bgr, *, max_cards=_PLACEHOLDER_MAX):
    """Bright face-up card-like boxes. Never assigns a rank.

    Between-round leftover UI must be filtered before calling this; the
    detector only looks below the chip-strip band so printed felt text
    and the waiting banner are less likely to become extra slots.
    """
    if felt_bgr is None or getattr(felt_bgr, "size", 0) == 0:
        return []
    height, width = felt_bgr.shape[:2]
    if height < 40 or width < 80:
        return []
    try:
        from ..vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
    except Exception:
        return []
    luma = (
        0.114 * felt_bgr[:, :, 0]
        + 0.587 * felt_bgr[:, :, 1]
        + 0.299 * felt_bgr[:, :, 2]
    )
    mask = (luma > 150).astype(np.uint8) * 255
    mask[: int(height * 0.36), :] = 0
    mask[int(height * 0.96):, :] = 0
    count, _labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    boxes = []
    for index in range(1, count):
        area = int(stats[index, cv2.CC_STAT_AREA])
        bw = int(stats[index, cv2.CC_STAT_WIDTH])
        bh = int(stats[index, cv2.CC_STAT_HEIGHT])
        x = int(stats[index, cv2.CC_STAT_LEFT])
        y = int(stats[index, cv2.CC_STAT_TOP])
        if area < 350 or area > 20000 or bw < 20 or bh < 18:
            continue
        aspect = bw / (bh + 1e-6)
        fill = area / (bw * bh + 1e-6)
        if not (0.4 <= aspect <= 3.2 and fill >= 0.28):
            continue
        boxes.append({
            "x": x,
            "y": y,
            "w": bw,
            "h": bh,
            "cx": float(cents[index, 0]),
            "cy": float(cents[index, 1]),
        })
    boxes.sort(key=lambda item: (item["cx"], item["cy"]))
    return boxes[: max(0, int(max_cards))]


def attach_unknown_placeholders(draft, *, repo_root=None):
    """Add extra unknown deal slots from in-play stills. Ranks stay None."""
    from ..vision.deps import load_cv2

    from .shoe_event_draft import add_event

    cv2 = load_cv2()
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    added = 0
    for event in list(draft.get("events") or []):
        if event.get("kind") != "deal":
            continue
        if event.get("placeholder_source") == PLACEHOLDER_SOURCE and event.get("slot", 0) > 0:
            continue
        path = event.get("still_path")
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_file():
            rooted = root / path
            candidate = rooted if rooted.is_file() else None
        if candidate is None:
            continue
        bgr = cv2.imread(str(candidate))
        if between_round_ui_likely(bgr):
            continue
        boxes = felt_card_placeholders(bgr)
        event["visible_card_regions"] = len(boxes)
        event["rank"] = None
        event["status"] = "unknown_kept"
        event["placeholder_source"] = PLACEHOLDER_SOURCE
        event["slot"] = 0
        if len(boxes) <= 1:
            continue
        existing = [
            item for item in draft["events"]
            if item.get("round_id") == event.get("round_id")
            and item.get("kind") == "deal"
            and item.get("placeholder_source") == PLACEHOLDER_SOURCE
            and int(item.get("slot") or 0) > 0
        ]
        if existing:
            continue
        for slot, box in enumerate(boxes[1:], start=1):
            add_event(
                draft, "deal",
                round_id=event.get("round_id"),
                rank=None,
                status="unknown_kept",
                frame_index=event.get("frame_index"),
                still_path=event.get("still_path"),
                still_sha256=event.get("still_sha256"),
                occupancy=event.get("occupancy"),
                placeholder_source=PLACEHOLDER_SOURCE,
                slot=slot,
                region=box,
                notes="占位：亮色牌位，点数未知；不是识别结果",
            )
            added += 1
    draft["accepted"] = False
    draft["placeholder_source"] = PLACEHOLDER_SOURCE
    draft["placeholder_slots_added"] = added
    return draft


def felt_occupancy(felt_bgr, *, luma_threshold=165):
    """Fraction of bright pixels on the navy felt. Not a card count."""
    if felt_bgr is None or felt_bgr.size == 0:
        return 0.0
    luma = (
        0.114 * felt_bgr[:, :, 0]
        + 0.587 * felt_bgr[:, :, 1]
        + 0.299 * felt_bgr[:, :, 2]
    )
    return float((luma > luma_threshold).mean())


def _segment_rounds(samples, *, occupy_on=0.065, occupy_off=0.052,
                    occupy_drop=0.022, min_samples=2):
    """Split on occupancy hysteresis plus a drop from the round peak.

    Printed felt text keeps a brightness floor, so a small absolute off
    threshold alone can glue the rest of the shoe into one page.
    """
    rounds = []
    current = None
    for sample in samples:
        if sample.get("overlay"):
            if current is not None and current["samples"] >= min_samples:
                rounds.append(current)
            current = None
            continue
        occupied = sample["occupancy"] >= occupy_on
        drop = 0.0 if current is None else current["peak"]["occupancy"] - sample["occupancy"]
        empty = sample["occupancy"] <= occupy_off or (
            current is not None and sample["occupancy"] < occupy_on and drop >= occupy_drop)
        if current is None:
            if occupied:
                current = {
                    "round_id": f"round-{len(rounds) + 1}",
                    "start_frame": sample["frame_index"],
                    "end_frame": sample["frame_index"],
                    "peak": sample,
                    "samples": 1,
                }
            continue
        current["end_frame"] = sample["frame_index"]
        current["samples"] += 1
        if sample["occupancy"] > current["peak"]["occupancy"]:
            current["peak"] = sample
        if empty and current["samples"] >= min_samples:
            rounds.append(current)
            current = None
        elif empty:
            current = None
    if current is not None and current["samples"] >= min_samples:
        rounds.append(current)
    return rounds


def scan_round_pages(video_path, output_dir, *, interval_s=8.0, style_path=_STYLE):
    """Sample felt occupancy without hashing the source file. Ranks stay unknown."""
    from ..vision.deps import load_cv2

    crops = load_navy_crops(style_path)
    source = Path(video_path)
    dest = Path(output_dir)
    felt_dir = dest / "felt"
    dest.mkdir(parents=True, exist_ok=True)
    felt_dir.mkdir(parents=True, exist_ok=True)
    cv2 = load_cv2()
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError("无法打开录像扫描轮次；不重试绕过")
    try:
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if width <= 0 or height <= 0 or count < 1:
            raise RuntimeError("录像没有有效画面或帧数")
        step = max(1, int(round(interval_s * fps)))
        samples = []
        overlay_count = 0
        sx, sy, sw, sh = crops["source"]
        fx, fy, fw, fh = crops["felt"]
        felt_abs = (sx + fx, sy + fy, fw, fh)
        for frame_index in range(0, count, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, bgr = cap.read()
            if not ok or bgr is None:
                continue
            overlay = nvidia_overlay_likely(bgr)
            felt = _crop(bgr, felt_abs)
            occupancy = 0.0 if overlay else felt_occupancy(felt)
            waiting = False if overlay else waiting_next_round_likely(felt)
            chip = False if overlay else chip_strip_likely(felt)
            between = waiting or chip
            still = None
            digest = None
            if felt.size and not overlay:
                still = felt_dir / f"felt-{frame_index:06d}.png"
                cv2.imwrite(str(still), felt)
                digest = sha256_file(still)
            sample = {
                "frame_index": int(frame_index),
                "time_ms": int(round(1000.0 * frame_index / fps)),
                "overlay": bool(overlay),
                "waiting_next_round": bool(waiting),
                "chip_strip": bool(chip),
                "between_round_ui": bool(between),
                "occupancy": occupancy,
                "still_path": str(still) if still else None,
                "still_sha256": digest,
            }
            if overlay:
                overlay_count += 1
            samples.append(sample)
    finally:
        cap.release()
    rounds = _segment_rounds(samples)
    return {
        "schema": SCHEMA,
        "accepted": False,
        "source_hashed": False,
        "video_path": str(source),
        "video_bytes": int(source.stat().st_size),
        "width": width,
        "height": height,
        "fps": fps,
        "frame_count": count,
        "interval_s": interval_s,
        "style_id": crops["style_id"],
        "felt_abs": list(felt_abs),
        "sample_count": len(samples),
        "overlay_samples": overlay_count,
        "round_count": len(rounds),
        "samples": samples,
        "rounds": rounds,
        "ranks_invented": False,
        "occupy_on": 0.065,
        "occupy_off": 0.052,
        "note": NOTE,
    }


def draft_from_round_scan(scan, *, filename=None, video_sha256=None, role="development"):
    """One unknown deal per occupancy round. Cut card stays a marker."""
    draft = empty_draft(
        video_path=scan.get("video_path"),
        video_sha256=video_sha256,
        video_bytes=scan.get("video_bytes"),
        role=role,
        filename=filename or Path(scan.get("video_path") or "clip").name,
    )
    samples = scan.get("samples") or []
    first = samples[0] if samples else None
    last = samples[-1] if samples else None
    add_event(
        draft, "shoe_open",
        frame_index=first["frame_index"] if first else None,
        still_path=first.get("still_path") if first and not first.get("overlay") else None,
        notes="开靴画面待核对；均匀/占用扫描不能证明从新靴第一张之前开始",
        status="draft" if not (first and first.get("overlay")) else "unknown_kept",
    )
    add_event(
        draft, "pre_first_card", status="unknown_kept",
        notes="第一张牌之前的画面尚未单独抽出",
    )
    add_event(
        draft, "burn", status="unknown_kept", count=None,
        notes="烧牌未知，不得默认为 0",
    )
    for item in scan.get("rounds") or []:
        peak = item["peak"]
        add_event(
            draft, "round_start", round_id=item["round_id"],
            frame_index=item["start_frame"], status="draft",
            notes=f"占用升高；采样 {item['samples']} 个点，不是牌数",
        )
        if peak.get("between_round_ui") or peak.get("waiting_next_round") or peak.get("chip_strip"):
            add_event(
                draft, "between_rounds", round_id=item["round_id"], rank=None,
                status="unknown_kept",
                frame_index=peak["frame_index"], still_path=peak.get("still_path"),
                still_sha256=peak.get("still_sha256"),
                occupancy=peak.get("occupancy"),
                notes="局间 UI（等待下一局 / 筹码条 / 立即发牌）；绒面牌是上一局遗留，不得记入本轮发牌",
            )
        else:
            add_event(
                draft, "deal", round_id=item["round_id"], rank=None, status="unknown_kept",
                frame_index=peak["frame_index"], still_path=peak.get("still_path"),
                still_sha256=peak.get("still_sha256"),
                occupancy=peak.get("occupancy"),
                notes="本轮有牌面占用，逐牌真值保留未知",
            )
        add_event(
            draft, "round_end", round_id=item["round_id"],
            frame_index=item["end_frame"], status="draft",
        )
    add_event(
        draft, "cut_card",
        frame_index=last["frame_index"] if last else None,
        notes="红牌是流程标志，不计入记牌；本扫描若未见红牌则保持未知",
        status="unknown_kept",
    )
    add_event(
        draft, "stop_play",
        frame_index=last["frame_index"] if last else None,
        still_path=last.get("still_path") if last and not last.get("overlay") else None,
        notes="停止时点待核对",
    )
    add_event(
        draft, "reshuffle_or_box_change", status="unknown_kept",
        notes="换盒未见则保留未知",
    )
    draft["round_scan"] = {
        "round_count": scan.get("round_count"),
        "overlay_samples": scan.get("overlay_samples"),
        "interval_s": scan.get("interval_s"),
        "waiting_pages": sum(
            1 for event in draft["events"] if event.get("kind") == "between_rounds"),
        "ranks_invented": False,
        "source_hashed": False,
    }
    draft["accepted"] = False
    return draft


def annotate_waiting_in_draft(draft, *, repo_root=None):
    """Re-read attached felt stills. Does not hash or reopen the source video."""
    from ..vision.deps import load_cv2
    cv2 = load_cv2()
    root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[2]
    waiting_pages = 0
    for event in draft.get("events") or []:
        path = event.get("still_path")
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_file():
            rooted = root / path
            candidate = rooted if rooted.is_file() else None
        if candidate is None:
            continue
        bgr = cv2.imread(str(candidate))
        waiting = waiting_next_round_likely(bgr)
        chip = chip_strip_likely(bgr)
        between = waiting or chip
        event["waiting_next_round"] = bool(waiting)
        event["chip_strip"] = bool(chip)
        event["between_round_ui"] = bool(between)
        if between and event.get("kind") == "deal":
            event["kind"] = "between_rounds"
            event["counted_in_remaining"] = False
            event["is_playing_card"] = False
            event["rank"] = None
            event["status"] = "unknown_kept"
            event["notes"] = "局间 UI（等待下一局 / 筹码条 / 立即发牌）；绒面牌是上一局遗留，不得记入本轮发牌"
            waiting_pages += 1
        elif between:
            waiting_pages += 1
    draft["accepted"] = False
    draft["waiting_pages"] = waiting_pages
    attach_unknown_placeholders(draft, repo_root=root)
    from .shoe_event_draft import ensure_standard_dealer_slots
    ensure_standard_dealer_slots(draft)
    return draft


def in_play_scan_rounds(scan):
    """Occupancy rounds whose peak is not waiting / chip-strip leftover UI."""
    rows = []
    for item in scan.get("rounds") or []:
        peak = item.get("peak") or {}
        if peak.get("between_round_ui") or peak.get("waiting_next_round") or peak.get("chip_strip"):
            continue
        rows.append(item)
    return rows


def sample_in_play_windows(video_path, scan, output_dir, *, step_s=1.0,
                           style_path=_STYLE, round_ids=None):
    """Denser felt stills inside in-play occupancy windows. Does not hash source."""
    from ..vision.deps import load_cv2

    crops = load_navy_crops(style_path)
    source = Path(video_path)
    dest = Path(output_dir)
    felt_dir = dest / "in-play"
    dest.mkdir(parents=True, exist_ok=True)
    felt_dir.mkdir(parents=True, exist_ok=True)
    cv2 = load_cv2()
    cap = cv2.VideoCapture(str(source))
    if not cap.isOpened():
        raise RuntimeError("无法打开录像抽对局序列；不重试绕过")
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0) or 30.0
        count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if count < 1:
            raise RuntimeError("录像没有有效帧数")
        step = max(1, int(round(float(step_s) * fps)))
        felt_abs = scan.get("felt_abs")
        if felt_abs and len(felt_abs) == 4:
            felt_box = tuple(int(v) for v in felt_abs)
        else:
            sx, sy, sw, sh = crops["source"]
            fx, fy, fw, fh = crops["felt"]
            felt_box = (sx + fx, sy + fy, fw, fh)
        windows = []
        wanted = set(round_ids) if round_ids is not None else None
        for item in in_play_scan_rounds(scan):
            if wanted is not None and item["round_id"] not in wanted:
                continue
            start = int(item["start_frame"])
            end = int(item["end_frame"])
            stills = []
            for frame_index in range(start, min(end, count - 1) + 1, step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    continue
                if nvidia_overlay_likely(bgr):
                    continue
                felt = _crop(bgr, felt_box)
                if felt.size == 0:
                    continue
                if between_round_ui_likely(felt):
                    continue
                still = felt_dir / f"{item['round_id']}-felt-{frame_index:06d}.png"
                cv2.imwrite(str(still), felt)
                stills.append({
                    "round_id": item["round_id"],
                    "frame_index": int(frame_index),
                    "time_ms": int(round(1000.0 * frame_index / fps)),
                    "still_path": str(still),
                    "still_sha256": sha256_file(still),
                    "occupancy": felt_occupancy(felt),
                    "rank": None,
                })
            windows.append({
                "round_id": item["round_id"],
                "start_frame": start,
                "end_frame": end,
                "still_count": len(stills),
                "stills": stills,
            })
    finally:
        cap.release()
    return {
        "schema": "hakimi-in-play-sequence-v1",
        "accepted": False,
        "source_hashed": False,
        "video_path": str(source),
        "video_bytes": int(source.stat().st_size),
        "step_s": float(step_s),
        "ranks_invented": False,
        "window_count": len(windows),
        "windows": windows,
        "note": "对局占用窗内的绒面序列，不是逐牌真值，也不能当验收通过。",
    }


def attach_in_play_sequence(draft, sample):
    """Attach sequence stills as uncounted markers. Ranks stay unknown."""
    seen = {
        (event.get("round_id"), event.get("frame_index"))
        for event in draft.get("events") or []
        if event.get("kind") == "in_play_still"
    }
    added = 0
    for window in sample.get("windows") or []:
        for still in window.get("stills") or []:
            key = (still.get("round_id") or window.get("round_id"), still.get("frame_index"))
            if key in seen:
                continue
            add_event(
                draft, "in_play_still",
                round_id=still.get("round_id") or window.get("round_id"),
                rank=None,
                status="unknown_kept",
                frame_index=still.get("frame_index"),
                still_path=still.get("still_path"),
                still_sha256=still.get("still_sha256"),
                occupancy=still.get("occupancy"),
                notes="对局序列静帧，点数未知，不能当逐牌真值",
            )
            seen.add(key)
            added += 1
    draft["accepted"] = False
    draft["in_play_sequence_stills"] = added
    draft["source_hashed"] = False
    from .shoe_event_draft import ensure_standard_dealer_slots
    ensure_standard_dealer_slots(draft)
    return draft


def write_scan(path, scan):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(scan)
    payload["accepted"] = False
    dest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
