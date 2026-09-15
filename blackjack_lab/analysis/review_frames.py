"""Extract hashed review stills from a local video. Never attests unused or acceptance."""
from __future__ import annotations

from pathlib import Path

from .evidence import LEVEL_EVIDENCE_LINKED, LEVEL_MISSING, sha256_file, write_manifest

SCHEMA = "hakimi-review-frames-v1"
NOTE = (
    "待审帧只证明源录像与导出画面的字节身份，不是授权原片、未使用声明或已验收。"
    "软件不能把提取结果写成 unused_* / independent_video / accepted / human_run。"
)


def _frame_indexes(frame_count, max_frames):
    if type(max_frames) is not int or max_frames < 1:
        raise ValueError("max_frames 必须是正整数")
    if type(frame_count) is not int or frame_count < 1:
        raise ValueError("录像没有可定位帧")
    count = min(max_frames, frame_count)
    if count == 1:
        return [0]
    return [(index * (frame_count - 1)) // (count - 1) for index in range(count)]


def extract_review_frames(video_path, output_dir, *, max_frames=8, write_manifest_file=True):
    """Seek evenly spaced frames, write PNG stills, hash source and stills.

    Does not register unused video, does not bind an acceptance pack, and
    does not rewrite the source file.
    """
    from ..vision.image_io import write_png_rgb
    from ..vision.video_io import VideoReader

    source = Path(video_path)
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)
    before = source.read_bytes()
    frames_dir = dest / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    with VideoReader(source) as reader:
        indexes = _frame_indexes(reader.asset.frame_count, max_frames)
        for frame_index in indexes:
            loaded = reader.seek(frame_index)
            still = frames_dir / f"frame-{frame_index:06d}.png"
            write_png_rgb(still, loaded.width, loaded.height, loaded.rgb)
            frames.append({
                "path": str(still),
                "sha256": sha256_file(still),
                "rgb_sha256": loaded.sha256,
                "frame_index": frame_index,
                "time_ms": reader.asset.time_ms(frame_index),
                "width": loaded.width,
                "height": loaded.height,
                "role": "review-frame-not-accepted",
            })
        video_sha256 = reader.asset.sha256
        video_bytes = reader.asset.byte_size
    after = source.read_bytes()
    if after != before:
        raise RuntimeError("提取待审帧不得改写源录像")
    result = {
        "schema": SCHEMA,
        "accepted": False,
        "passed": False,
        "human_run": False,
        "independent_video": False,
        "unused_in_training": False,
        "unused_in_threshold_selection": False,
        "evidence_level": LEVEL_EVIDENCE_LINKED if frames else LEVEL_MISSING,
        "video_path": str(source),
        "video_sha256": video_sha256,
        "video_bytes": video_bytes,
        "frames": frames,
        "note": NOTE,
        "original_preserved": True,
    }
    if write_manifest_file:
        write_manifest(dest / "review-frames.json", result)
        result["manifest_path"] = str(dest / "review-frames.json")
    return result


def attach_review_frames(pack, review_result, *, item_id="authorized_shoe_video"):
    """Hash-match stills onto an unchecked M4 item. Never unused attestation or pass."""
    from .acceptance_pack import bind_item_artifact
    from .evidence import sha256_file

    if item_id == "unused_attestation":
        raise ValueError("待审帧不能写成未使用声明")
    video = Path(review_result["video_path"])
    if sha256_file(video) != review_result.get("video_sha256"):
        raise ValueError("待审帧源片摘要与当前文件不符")
    pack = bind_item_artifact(
        pack, item_id, video,
        role="review-source-video-not-accepted",
        notes="待审帧哈希只证明字节身份，人尚未核验，不能勾选")
    for frame in review_result.get("frames") or []:
        pack = bind_item_artifact(pack, item_id, frame["path"], role="review-frame-not-accepted")
    item = pack["items"][item_id]
    item["passed"] = False
    item["review_sha_match"] = True
    pack["accepted"] = False
    pack["passed"] = False
    return pack
