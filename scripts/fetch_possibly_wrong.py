"""Download the pinned possibly-wrong/blackjack v7.6 strategy.exe for local compare.

The binary is GPL-3.0-or-later. It is written under .local-evidence and is not
part of the product tree. Do not copy it into blackjack_lab or a portable zip.
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.external_pw import default_strategy_exe, load_spec, verify_strategy_exe

SPEC = ROOT / "fixtures" / "v02b2" / "external_pw_cases.json"


def default_exe():
    return default_strategy_exe(ROOT)


def fetch(spec, dest, timeout=60):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = spec["engine"]["strategy_url"]
    print("GET", url)
    print("->", dest)
    tmp = dest.with_suffix(".exe.partial")
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response, tmp.open("wb") as handle:
            while True:
                chunk = response.read(1024 * 64)
                if not chunk:
                    break
                handle.write(chunk)
        tmp.replace(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise
    return verify_strategy_exe(dest, spec)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Fetch pinned possibly-wrong strategy.exe")
    parser.add_argument("--spec", type=Path, default=SPEC)
    parser.add_argument("--output", type=Path, default=default_exe())
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)
    spec = load_spec(args.spec)
    if spec["_load_errors"]:
        raise SystemExit("案例夹具无效: " + ",".join(spec["_load_errors"]))
    dest = args.output
    if dest.exists():
        digest = verify_strategy_exe(dest, spec)
        print("already present sha256=" + digest)
        return 0
    digest = fetch(spec, dest, timeout=args.timeout)
    print("sha256=" + digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
