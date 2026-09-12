# -*- coding: utf-8 -*-
"""原帧框选与漏检补样。标签来源显式记录，程序不会把建议升级成人工真值。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .contracts import ContractError
from .deps import ImageRejected, load_cv2, load_numpy
from .glyph_dataset import GlyphItem, SPLITS, crop_id_for, save_queue, validate_label
from .real_cards import manual_glyph

ANNOTATION_SCHEMA = "original-frame-annotations-1"


def read_frame(session_dir, file):
    root = (Path(session_dir) / "frames").resolve()
    path = (root / file).resolve()
    if not path.is_relative_to(root):
        raise ImageRejected("原帧路径越出会话 frames 目录")
    payload = path.read_bytes()
    cv2, np = load_cv2(), load_numpy()
    bgr = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ImageRejected(f"原帧无法解码: {file}")
    return bgr, hashlib.sha256(payload).hexdigest()


def material_identity(session_dir, manifest=None):
    """录像用完整录像摘要；捕获帧集用有序帧文件内容摘要，与目录名称无关。"""
    root = Path(session_dir)
    manifest = manifest or json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("source_sha256"):
        return str(manifest["source_sha256"])
    digest = hashlib.sha256()
    for frame in manifest["frames"]:
        path = (root / "frames" / frame["file"]).resolve()
        if not path.is_relative_to((root / "frames").resolve()):
            raise ContractError("帧路径越出会话目录")
        with path.open("rb") as handle:
            digest.update(hashlib.file_digest(handle, "sha256").digest())
    return digest.hexdigest()


def build_annotation_queue(session_dir, annotation_file, output):
    """把人工框或显式标记的待复核建议变成新队列，绝不覆盖原标签。"""
    root, out = Path(session_dir), Path(output)
    if out.exists():
        raise ContractError("补框输出目录已存在，拒绝覆盖")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    annotation = json.loads(Path(annotation_file).read_text(encoding="utf-8"))
    if annotation.get("schema") != ANNOTATION_SCHEMA:
        raise ContractError("未知原帧标注 schema")
    if annotation.get("session") != manifest.get("session"):
        raise ContractError("原帧标注会话不一致")
    if manifest.get("valid") is False or manifest.get("decode_errors") or manifest.get("truncated"):
        raise ContractError("原素材采样不完整，请修复来源后再生成补样队列")
    source_id = material_identity(root, manifest)
    if annotation.get("source_sha256") != source_id:
        raise ContractError("原帧标注来源摘要不一致")
    frame_map = {f["file"]: f for f in manifest["frames"]}
    prepared = []
    for record in annotation["frames"]:
        if (record.get("split", "train") not in SPLITS
                or type(record.get("round_id")) is not int or record["round_id"] < 0):
            raise ContractError("原帧标注 split 或局号无效")
        if record["file"] not in frame_map:
            raise ContractError("标注帧不在冻结会话清单中")
        bgr, digest = read_frame(root, record["file"])
        if digest != record.get("sha256"):
            raise ContractError("标注后的原帧已变化")
        for obj in record["objects"]:
            if obj["rank"] == "unreadable":
                continue  # 真值分母保留在原标注中，不强给不可读牌贴点数标签。
            rank = validate_label(obj["rank"])
            provenance = obj.get("label_provenance", "unspecified")
            if provenance not in ("unspecified", "assistant_proposed", "human_reviewed"):
                raise ContractError("未知标签来源")
            if provenance == "human_reviewed" and not obj.get("reviewed_by"):
                raise ContractError("人工复核标签缺少复核人")
            # A previously reviewed whole-card rank does not certify a newly
            # assistant-cropped glyph. The UI retains the old rank review, but
            # exports stay proposals until the user confirms the new crop.
            if obj.get("bbox_provenance") == "assistant_upper_corner_crop":
                provenance = "assistant_proposed"
            if not obj.get("physical_card_id") or not obj.get("rejection_reason"):
                raise ContractError("补框必须记录物理牌身份与漏检/补框原因")
            glyph = manual_glyph(bgr, obj["bbox"])
            cid = crop_id_for(manifest["session"], record["file"], glyph.bbox)
            x, y, w, h = glyph.bbox
            context = bgr[max(0,y-24):min(bgr.shape[0],y+h+24),
                          max(0,x-24):min(bgr.shape[1],x+w+24)]
            item = GlyphItem(crop_id=cid, session=manifest["session"], frame=record["file"],
                             frame_signature=frame_map[record["file"]].get("signature", ""),
                             round_id=int(record["round_id"]), split=record.get("split", "train"),
                             bbox=glyph.bbox, ink=glyph.ink, crop_file=f"crops/{cid}.png",
                             mask_file=f"masks/{cid}.png", label=rank,
                             elapsed_s=frame_map[record["file"]].get("elapsed_s"),
                             source_sha256=source_id, frame_sha256=digest,
                             origin_crop_id=cid, physical_card_id=obj["physical_card_id"],
                             label_provenance=provenance, extraction_method="manual_original_frame",
                             rejection_reason=obj["rejection_reason"], orientation_deg=obj.get("orientation_deg"))
            prepared.append((item, glyph.mask, context))
    if len({item.crop_id for item, _, _ in prepared}) != len(prepared):
        raise ContractError("重复人工框")
    cv2 = load_cv2()
    for sub in ("masks", "crops"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    for item, mask, context in prepared:
        for rel, pixels in ((item.mask_file, mask), (item.crop_file, context)):
            ok, encoded = cv2.imencode(".png", pixels)
            if not ok:
                raise ImageRejected("补框 PNG 编码失败")
            payload = encoded.tobytes()
            (out / rel).write_bytes(payload)
            if rel == item.crop_file:
                item.crop_sha256 = hashlib.sha256(payload).hexdigest()
    save_queue([item for item, _, _ in prepared], out)
    (out / "source-annotations.json").write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
    return [item for item, _, _ in prepared]
