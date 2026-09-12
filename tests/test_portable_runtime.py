"""A packed copy must run outside the developer tree with a cleared PYTHONPATH."""
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import make_portable_copy as pack


DRIVER = textwrap.dedent(r"""
import json
import sys
from pathlib import Path

DEST = Path(sys.argv[1]).resolve()
PHASE = sys.argv[2]
sys.path.insert(0, str(DEST))
import blackjack_lab
from pathlib import Path as P
loaded = P(blackjack_lab.__file__).resolve()
if not loaded.is_relative_to(DEST):
    raise SystemExit("module loaded from " + str(loaded) + " not " + str(DEST))
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.native_backend import SOURCE, build_native
from blackjack_lab.analysis.service import calculate
from blackjack_lab.analysis.split_contracts import DAS_ENGINE, das_research_rules
from blackjack_lab.ledger.events import SOURCE_SIMULATOR
from blackjack_lab.ledger.ledger import EventLedger
from blackjack_lab.ui.controller import SessionController

if not SOURCE.resolve().is_relative_to(DEST):
    raise SystemExit("native source loaded from " + str(SOURCE.resolve()))
build_native()

def example():
    ledger = EventLedger("portable-runtime-session")
    ledger.start_session("运行副本验收；非真实牌桌")
    ledger.create_shoe(das_research_rules(6))
    ledger.start_round(["玩家1"])
    ledger.deal("庄家", "6", source="自建模拟器")
    ledger.deal("庄家", hidden=True, source="自建模拟器")
    ledger.deal("玩家1", "8", source="自建模拟器")
    ledger.deal("玩家1", "8", source="自建模拟器")
    return ledger

db = DEST / "data" / "lab.db"
state_path = DEST / "data" / "runtime-state.json"
if PHASE == "record":
    ctrl = SessionController(db, recording_source=SOURCE_SIMULATOR)
    try:
        ctrl.ledger = example()
        ctrl.session_id = ctrl.ledger.session_id
        ctrl.store.save_ledger(ctrl.ledger)
        result = calculate(build_input(ctrl.ledger, "玩家1"))
        if result["status"] != "available":
            raise SystemExit(result.get("reason") or result["status"])
        saved = ctrl.analysis_store.save(result)
        payload = {
            "module": str(loaded),
            "native_source": str(SOURCE.resolve()),
            "session_id": ctrl.session_id,
            "snapshot_id": saved["snapshot_id"],
            "input_digest": result["input_digest"],
            "engine_version": result["engine_version"],
            "status": result["status"],
        }
        state_path.write_text(json.dumps(payload), encoding="utf-8")
        print(json.dumps(payload), flush=True)
    finally:
        ctrl.close()
elif PHASE == "recompute":
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    ctrl = SessionController.recover(db, payload["session_id"])
    try:
        loaded_snap = ctrl.analysis_store.load(payload["snapshot_id"])
        rebuilt = ctrl.recompute_input(loaded_snap)
        again = calculate(rebuilt)
        if again["status"] != "available":
            raise SystemExit(again.get("reason") or again["status"])
        if again["input_digest"] != payload["input_digest"]:
            raise SystemExit("recompute digest mismatch")
        if again["engine_version"] != DAS_ENGINE:
            raise SystemExit("unexpected engine " + again["engine_version"])
        payload["recomputed"] = True
        payload["recomputed_status"] = again["status"]
        print(json.dumps(payload), flush=True)
    finally:
        ctrl.close()
else:
    raise SystemExit("unknown phase")
""")


@unittest.skipUnless(os.name == "nt", "Pack-and-run copy uses the Windows native backend")
class TestPortableRuntimeOutsideRepo(unittest.TestCase):
    def test_copy_outside_repo_records_analyzes_saves_and_recomputes(self):
        with tempfile.TemporaryDirectory(prefix="hakimi-runtime-") as folder:
            dest = Path(folder) / "runtime"
            self.assertFalse(str(dest.resolve()).startswith(str(ROOT.resolve())))
            self.assertEqual(pack.main(["--output", str(dest)]), 0)
            self.assertFalse((dest / "data").exists())
            self.assertEqual(list(dest.rglob(".env")), [])
            self.assertEqual(list(dest.rglob("*.sqlite3")), [])
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env["PYTHONNOUSERSITE"] = "1"
            env["PYTHONUTF8"] = "1"
            driver = Path(folder) / "driver.py"
            driver.write_text(DRIVER, encoding="utf-8")
            first = subprocess.run(
                [sys.executable, "-I", str(driver), str(dest), "record"],
                cwd=str(dest), env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            recorded = json.loads(first.stdout.strip().splitlines()[-1])
            self.assertTrue(Path(recorded["module"]).resolve().is_relative_to(dest.resolve()))
            self.assertTrue(Path(recorded["native_source"]).resolve().is_relative_to(dest.resolve()))
            self.assertNotIn(str(ROOT.resolve()), recorded["module"])
            second = subprocess.run(
                [sys.executable, "-I", str(driver), str(dest), "recompute"],
                cwd=str(dest), env=env, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            recomputed = json.loads(second.stdout.strip().splitlines()[-1])
            self.assertTrue(recomputed["recomputed"])
            self.assertEqual(recomputed["input_digest"], recorded["input_digest"])
            self.assertEqual(recomputed["recomputed_status"], "available")
            self.assertTrue((dest / "data" / "lab.db").is_file())


if __name__ == "__main__":
    unittest.main()
