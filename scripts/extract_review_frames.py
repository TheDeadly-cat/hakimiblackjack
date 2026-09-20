"""Extract hashed review stills. Never marks unused video or M4 items passed."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.acceptance_pack import build_acceptance_pack
from blackjack_lab.analysis.evidence import write_manifest
from blackjack_lab.analysis.review_frames import attach_review_frames, extract_review_frames


def main(argv=None):
    parser = argparse.ArgumentParser(description="从本地录像提取待审帧并哈希；不能写成已验收或未使用")
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--max-frames", type=int, default=8)
    parser.add_argument("--bind-pack", type=Path, default=None,
                        help="已有未勾选验收包；缺省且给出 --pack-output 时新建空包")
    parser.add_argument("--pack-output", type=Path, default=None)
    parser.add_argument("--item", default="authorized_shoe_video")
    args = parser.parse_args(argv)
    result = extract_review_frames(args.video, args.output_dir, max_frames=args.max_frames)
    if (result.get("accepted") or result.get("passed") or result.get("human_run")
            or result.get("independent_video") or result.get("unused_in_training")
            or result.get("unused_in_threshold_selection")):
        raise SystemExit("待审帧不得写成验收通过或未使用声明")
    pack_output = None
    if args.pack_output is not None or args.bind_pack is not None:
        if args.item == "unused_attestation":
            raise SystemExit("待审帧不能写成未使用声明")
        if args.bind_pack is not None:
            pack = json.loads(args.bind_pack.read_text(encoding="utf-8"))
        else:
            pack = build_acceptance_pack()
        pack = attach_review_frames(pack, result, item_id=args.item)
        if pack.get("accepted") or pack.get("passed") or pack["items"][args.item]["passed"]:
            raise SystemExit("挂包不得把验收项写成通过")
        dest = args.pack_output or (args.output_dir / "m4-pack-unchecked.json")
        dest.parent.mkdir(parents=True, exist_ok=True)
        write_manifest(dest, pack)
        pack_output = str(dest)
    print(json.dumps({
        "schema": result["schema"],
        "accepted": result["accepted"],
        "independent_video": result["independent_video"],
        "unused_in_training": result["unused_in_training"],
        "evidence_level": result["evidence_level"],
        "video_sha256": result["video_sha256"],
        "frame_count": len(result["frames"]),
        "manifest_path": result.get("manifest_path"),
        "pack_output": pack_output,
        "note": result["note"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
