"""Derive an upright upper-corner review bundle, preserving prior human work."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from blackjack_lab.vision.corner_policy import UPPER_CORNER_POLICY, UpperCornerSelector, corner_key
from blackjack_lab.vision.frame_annotations import material_identity, read_frame
from blackjack_lab.vision.glyph_dataset import LABEL_RANKS


def select_frame(frame, source_sha, selector, overrides=None):
    """Separate excluded records; changing geometry never rewrites a human rank."""
    result = copy.deepcopy(frame)
    kept, excluded = [], copy.deepcopy(frame.get("excluded_objects", []))
    overrides = overrides or {}
    for original in frame["objects"]:
        obj = copy.deepcopy(original)
        key = corner_key(source_sha, frame["sha256"], original["bbox"])
        automatic = selector.assess(original["bbox"])
        decision = copy.deepcopy(overrides.get(key, automatic))
        if type(decision.get("keep")) is not bool:
            raise ValueError("上角筛选决定必须明确 keep=true/false")
        if original["rank"] == "junk" and key not in overrides:
            decision = {"keep": False, "reason": "previous_noncard_label"}
        obj["upper_corner_selection"] = dict(decision, policy=UPPER_CORNER_POLICY,
            original_key=key, provenance="assistant_proposed")
        if not decision["keep"]:
            excluded.append(obj)
            continue
        if "bbox" in decision:
            # Validate bounds with the same image context, without relabeling it.
            selector.assess(decision["bbox"])
            obj["bbox_before_upper_selection"] = copy.deepcopy(original["bbox"])
            obj["bbox"] = list(decision["bbox"])
            obj["bbox_provenance"] = "assistant_upper_corner_crop"
        rank = decision.get("rank", obj["rank"])
        if rank not in LABEL_RANKS + ("unreadable",):
            raise ValueError("无效的上角点数建议")
        if rank != original["rank"]:
            obj["assistant_upper_rank_suggestion"] = rank
            if original.get("label_provenance") == "human_reviewed":
                obj["needs_attention"] = True
                obj["review_hint"] = "保留了你的点数；助手的新建议有差异，可对照上角检查"
            else:
                obj["rank_before_upper_review"] = original["rank"]
                obj["rank"] = rank
                obj["label_provenance"] = "assistant_proposed"
        kept.append(obj)
    result.update(objects=kept, excluded_objects=excluded, review_policy=UPPER_CORNER_POLICY,
                  upper_selection_provenance="assistant_proposed")
    return result


def prepare_bundle(bundle_path, output, *, decisions=None):
    src, out = Path(bundle_path), Path(output)
    if out.exists():
        raise ValueError("请选择新目录，已有复核进度不能覆盖")
    bundle = json.loads(src.read_text(encoding="utf-8"))
    if bundle.get("schema") != "assisted-review-bundle-1" or not bundle.get("sessions"):
        raise ValueError("批量清单格式错误")
    overrides = json.loads(Path(decisions).read_text(encoding="utf-8")) if decisions else {}
    prepared = []
    for i, entry in enumerate(bundle["sessions"]):
        original_path = Path(entry["annotations"])
        raw = original_path.read_bytes()
        original = json.loads(raw)
        if (original.get("schema") != "original-frame-annotations-1"
                or original.get("source_sha256") != entry["source_sha256"]
                or material_identity(entry["session"]) != entry["source_sha256"]):
            raise ValueError("原标注来源不匹配")
        data = copy.deepcopy(original)
        data["frames"] = []
        for f in original["frames"]:
            image, digest = read_frame(entry["session"], f["file"])
            if digest != f["sha256"]:
                raise ValueError("素材原帧内容已改变")
            data["frames"].append(select_frame(f, entry["source_sha256"], UpperCornerSelector(image), overrides))
        data.update(review_policy=UPPER_CORNER_POLICY, final_acceptance_eligible=False,
                    upper_policy_note="只核正向的上方/左上角；下方、倒向、非牌与不确定位置另存，不从下角补识别",
                    previous_annotations_sha256=hashlib.sha256(raw).hexdigest())
        prepared.append((i, original_path, raw, data))
    # Re-read all files before writing: do not quietly replace a user's concurrent edits.
    if any(p.read_bytes() != raw for _, p, raw, _ in prepared):
        raise ValueError("准备期间用户标注已更新，请用最新进度重试")
    out.mkdir(parents=True)
    for i, path, raw, data in prepared:
        (out / f"original-{i+1}.json").write_bytes(raw)
        target = out / f"review-{i+1}.json"
        target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        bundle["sessions"][i]["annotations"] = str(target.resolve())
    bundle.update(review_policy=UPPER_CORNER_POLICY, previous_bundle=str(src.resolve()))
    target = out / "bundle.json"
    target.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("bundle"); p.add_argument("output"); p.add_argument("--decisions")
    args = p.parse_args()
    print(prepare_bundle(args.bundle, args.output, decisions=args.decisions))
