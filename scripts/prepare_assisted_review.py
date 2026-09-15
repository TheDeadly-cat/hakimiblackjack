"""Pre-annotate local review pages; never overwrite human originals or train the runtime model."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.frame_annotations import read_frame, material_identity
from blackjack_lab.vision.glyph_dataset import detect_round_ids, assign_splits
from blackjack_lab.vision.real_cards import extract_glyphs, EXTRACTION_VERSION
from blackjack_lab.vision.rank_classifier import RankClassifier


def unchanged_background(image, reference, bbox):
    """Annotation aid only: an unchanged patch of a supplied empty-table reference."""
    from blackjack_lab.vision.deps import load_cv2
    cv2 = load_cv2()
    if image.shape != reference.shape:
        raise ValueError("背景参考图与素材尺寸不一致")
    x, y, w, h = bbox
    left, top = max(0, x-4), max(0, y-4)
    right, bottom = min(image.shape[1], x+w+4), min(image.shape[0], y+h+4)
    if right <= left or bottom <= top:
        return False
    patch = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2GRAY)
    search = cv2.cvtColor(reference[max(0, top-4):bottom+4, max(0, left-4):right+4], cv2.COLOR_BGR2GRAY)
    if patch.std() < 12:
        return False
    return bool(cv2.matchTemplate(search, patch, cv2.TM_CCOEFF_NORMED).max() >= .94)


def prepare(session, output, model, *, original_annotations=None, step=20, background=None):
    root, dest = Path(session).resolve(), Path(output).resolve()
    if dest.exists() or step < 1:
        raise ValueError("请选择新输出文件和正数采样步长")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if not manifest.get("frames") or manifest.get("valid") is False or manifest.get("decode_errors") or manifest.get("truncated"):
        raise ValueError("素材会话为空或不完整")
    source_sha = material_identity(root, manifest)
    original = json.loads(Path(original_annotations).read_text(encoding="utf-8")) if original_annotations else None
    if original and original.get("source_sha256") != source_sha:
        raise ValueError("已有人审标注与素材来源不匹配")
    old = {f["file"]: f for f in original["frames"]} if original else {}
    if original is not None and (original.get("schema") != "original-frame-annotations-1" or not old):
        raise ValueError("原标注格式不符或没有页面")
    if set(old) - {f["file"] for f in manifest["frames"]}:
        raise ValueError("原标注含素材清单以外的帧")
    rounds = detect_round_ids(manifest["frames"])
    splits = assign_splits(rounds) if len(set(rounds)) >= 3 else {r: "train" for r in rounds}
    frames = []
    chosen = [f for i, f in enumerate(manifest["frames"]) if f["file"] in old or (not original and i % step == 0)]
    indices = {f["file"]: i for i, f in enumerate(manifest["frames"])}
    for source in chosen:
        name = source["file"]
        previous = old.get(name)
        bgr, digest = read_frame(root, name)
        if previous and previous.get("sha256") != digest:
            raise ValueError("原标注帧已变化")
        # Entire human record, including drawing geometry and completion, is preserved byte-semantically.
        if previous and (previous.get("complete") or previous.get("objects")):
            frames.append(copy.deepcopy(previous))
            continue
        rid = rounds[indices[name]]
        record = copy.deepcopy(previous) if previous else {
            "file": name, "sha256": digest, "frame_index": source.get("frame_id"),
            "elapsed_s": source.get("elapsed_s"), "round_id": rid,
            "round_provenance": "automatic_white_pixel_segmentation", "split": splits[rid],
            "material_rgb_sha256": hashlib.sha256(bgr[:, :, ::-1].tobytes()).hexdigest(),
            "source_rgb_sha256": source.get("source_rgb_sha256"), "complete": False,
        }
        if record.get("sha256") != digest:
            raise ValueError("原标注帧已变化")
        objects = []
        for n, glyph in enumerate(extract_glyphs(bgr)):
            guess = model.predict_mask(glyph.mask)
            label = guess.raw_label
            if guess.similarity < .55:
                label = "unreadable"
            is_background = background is not None and unchanged_background(bgr, background, glyph.bbox)
            if is_background:
                label = "junk"
            cid = hashlib.sha256(f"{source_sha}:{name}:{glyph.bbox}".encode()).hexdigest()[:16]
            objects.append({"bbox": list(glyph.bbox), "rank": label,
                "physical_card_id": f"candidate-{cid}", "identity_provenance": "automatic_candidate_not_verified_physical_card",
                "region_id": "unassigned", "label_provenance": "assistant_proposed",
                "proposed_rank": label, "proposal_method": "empty_background_reference" if is_background else "local_annotation_helper",
                "unchanged_empty_background": is_background,
                "proposal_model_id": model.model_id, "proposal_score": guess.score,
                "proposal_similarity": guess.similarity, "proposal_accepted": guess.accepted,
                "proposal_angle": guess.angle, "assistant_visual_reviewed": False,
                "rejection_reason": "自动预标注，请核对点数和误框；一个物理牌可能有多个角标",
                "extraction_method": EXTRACTION_VERSION})
        record.update(objects=objects, complete=False, preannotated=True,
                      annotation_method="model_assisted_pending_user_review")
        record.pop("reviewed_by", None); record.pop("reviewed_at", None)
        frames.append(record)
    data = {"schema": "original-frame-annotations-1", "session": manifest["session"],
        "source_sha256": source_sha, "source_roi": manifest.get("roi"),
        "coordinate_space": "material_roi", "source_complete": True, "role": "assisted_review",
        "frames": frames, "annotation_method": "model_assisted_user_verification",
        "proposal_model_id": model.model_id, "production_model_changed": False,
        "final_acceptance_eligible": False, "selection": {"method": "fixed_manifest_step", "step": step},
        "note": "自动框和标签供用户检查，不是已人审真值；候选编号不证明物理牌身份。已有人审页完整保留。"}
    if background is not None:
        data["empty_background_bgr_sha256"] = hashlib.sha256(background.tobytes()).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("x", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("session"); p.add_argument("output"); p.add_argument("--model", required=True)
    p.add_argument("--original-annotations"); p.add_argument("--step", type=int, default=20)
    p.add_argument("--empty-background", help="人工选定的同尺寸空桌参考 PNG，仅用于预标注排除背景")
    a = p.parse_args(argv)
    background = None
    if a.empty_background:
        from blackjack_lab.vision.deps import load_cv2, load_numpy
        cv2, np = load_cv2(), load_numpy()
        background = cv2.imdecode(np.frombuffer(Path(a.empty_background).read_bytes(), np.uint8), cv2.IMREAD_COLOR)
        if background is None:
            p.error("无法读取空桌参考图")
    data = prepare(a.session, a.output, RankClassifier.load(a.model),
        original_annotations=a.original_annotations, step=a.step, background=background)
    print(json.dumps({"pages": len(data["frames"]), "objects": sum(len(f.get("objects", [])) for f in data["frames"]),
                      "human_complete_preserved": sum(f.get("complete") is True for f in data["frames"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
