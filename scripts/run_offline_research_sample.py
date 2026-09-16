"""Run the existing 6/7/8 three-round offline sample into a research pack."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.analysis.offline_research_sample import run_offline_research_sample


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="复用已有三轮样板，写出 sample_run.json 与结果文件；不是实机优势证明")
    parser.add_argument(
        "--output", type=Path,
        default=ROOT / ".local-evidence" / "offline-research-sample",
        help="写入目录；默认在 gitignore 的本地证据目录")
    parser.add_argument("--samples", type=int, default=12)
    parser.add_argument("--seed", type=int, default=3)
    args = parser.parse_args(argv)
    report = run_offline_research_sample(
        args.output, n_samples=args.samples, seed=args.seed)
    print(json.dumps({
        "ok": report["ok"],
        "sample_run": report.get("sample_run"),
        "failed_steps": report.get("failed_steps"),
        "expected_remaining": report.get("expected_remaining"),
        "not_a_reliable_window_claim": True,
    }, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
