"""Report whether a --usage-session folder has human F11 evidence. Never accepts."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blackjack_lab.capture.usage_session_report import inspect_usage_session


def main(argv=None):
    parser = argparse.ArgumentParser(description="核对实测会话目录；不能把向导点击写成 F11 通过")
    parser.add_argument("--folder", type=Path, required=True)
    args = parser.parse_args(argv)
    report = inspect_usage_session(args.folder)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["uses_user_default_db"] or report["accepted"] or report["live_catchup"]:
        raise SystemExit(2)
    if not report["human_f11_ready_for_signoff"]:
        raise SystemExit(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
