"""Independent Monte Carlo cross-check and target-machine request benchmarks."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import platform
import random
import statistics
import subprocess
import sys
import time
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blackjack_lab.analysis.actions import solve_counts
from blackjack_lab.analysis.information import build_input
from blackjack_lab.analysis.service import AnalysisService
from tests.analysis_reference import dealer_end, net, points
from tests.test_analysis_integration import example
from blackjack_lab.ui.controller import SessionController
from blackjack_lab.analysis.contracts import research_rules
from scripts.source_identity import source_identity


def monte_carlo(n_decks, samples=20_000, seed=20260910):
    counts = [4 * n_decks] * 9 + [16 * n_decks]
    for card in (10, 6, 10):
        counts[card - 1] -= 1
    exact = solve_counts(counts, (10, 6), 10, True)
    pool = [value for value, count in enumerate(counts, 1) for _ in range(count)]
    rng = random.Random(seed + n_decks)
    outcomes = {"stand": [], "double": []}
    for _ in range(samples):
        while True:
            world = tuple(rng.sample(pool, 20))
            if world[0] != 1:  # Negative peek with T up: hole cannot be A.
                break
        outcomes["stand"].append(float(net((10, 6), dealer_end(10, world, 0))))
        hand = (10, 6, world[1])
        outcomes["double"].append(-2.0 if points(hand) > 21 else float(net(hand, dealer_end(10, world, 1), 2)))
    result = {"n_decks": n_decks, "seed": seed + n_decks, "samples": samples,
              "unit": "independent synthetic rounds; two actions reuse worlds, not independent extra samples", "actions": {}}
    for action, values in outcomes.items():
        mean = statistics.mean(values)
        se = statistics.stdev(values) / math.sqrt(samples)
        expected = exact["actions"][action]["ev"]
        result["actions"][action] = {"exact_ev": expected, "sample_mean": mean,
            "standard_error": se, "normal_ci95": [mean - 1.96 * se, mean + 1.96 * se],
            "verification_bound": "predeclared abs(mean-exact) <= 6*SE + 1e-10", "passed": abs(mean - expected) <= 6 * se + 1e-10}
    return result


def benchmarks():
    cases = [(("10", "6"), "10"), (("5", "6"), "6"), (("A", "6"), "9"),
             (("3", "5"), "10"), (("A", "2"), "2"), (("2", "3"), "2")]
    results = []
    for n in (6, 7, 8):
        for hand, up in cases:
            ledger = example(n, cards=hand, up=up)
            start = time.perf_counter()
            snapshot = build_input(ledger, "玩家1")
            input_seconds = time.perf_counter() - start
            service = AnalysisService()
            try:
                service.start(snapshot)
                while True:
                    result = service.poll()
                    if result is not None:
                        break
                    time.sleep(0.005)
                elapsed = time.perf_counter() - start
                results.append({"n_decks": n, "player": hand, "dealer_up": up,
                    "status": result["status"], "input_seconds": input_seconds,
                    "wall_seconds": elapsed, "compute_seconds": result["elapsed_seconds"],
                    "worker_peak_working_set_bytes": result.get("worker_peak_working_set_bytes"),
                    "nodes": result.get("nodes")})
                print(f"{n} decks {hand} vs {up}: {result['status']} {elapsed:.3f}s", flush=True)
            finally:
                service.close()
    times = sorted(r["wall_seconds"] for r in results)
    p95 = times[math.ceil(0.95 * len(times)) - 1]
    return {"cases": results, "n": len(times), "p50_seconds": statistics.median(times),
            "p95_seconds": p95, "max_seconds": max(times), "p95_method": "nearest rank",
            "target_p95_seconds": 2.0, "target_met": p95 <= 2.0,
            "all_completed": all(r["status"] == "available" for r in results)}


def recording_benchmark():
    timings = []
    with tempfile.TemporaryDirectory() as temp:
        controller = SessionController(Path(temp) / "recording.db", recording_source="自建模拟器")
        try:
            controller.new_shoe(research_rules())
            for _ in range(20):
                operations = [lambda: controller.start_round(["玩家1"]),
                    lambda: controller.deal_shown("庄家", "9"), lambda: controller.deal_hidden("庄家"),
                    lambda: controller.deal_shown("玩家1", "10"), lambda: controller.deal_shown("玩家1", "6")]
                for operation in operations:
                    start = time.perf_counter()
                    operation()
                    timings.append(time.perf_counter() - start)
                state = controller.state().current
                hand_id = state.table.players["玩家1"].hands[0].hand_id
                hidden_id = next(eid for eid, info in state.unresolved.items() if info["round_id"] == state.round_id)
                for operation in (lambda: controller.player_action("玩家1", hand_id, "停牌"),
                                  lambda: controller.reveal(hidden_id, "8"), controller.end_round):
                    start = time.perf_counter()
                    operation()
                    timings.append(time.perf_counter() - start)
            return {"rounds": 20, "operations": len(timings), "event_count": controller.store.event_count(),
                    "p50_seconds": statistics.median(timings), "p95_seconds": sorted(timings)[math.ceil(.95 * len(timings)) - 1],
                    "max_seconds": max(timings), "scope": "single-session record validation and SQLite commit, no UI repaint"}
        finally:
            controller.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--samples", type=int, default=20_000)
    args = parser.parse_args()
    directory = args.output or ROOT / ".local-evidence" / ("analysis-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
    directory.mkdir(parents=True, exist_ok=False)
    simulations = [monte_carlo(n, args.samples) for n in (6, 7, 8)]
    performance = benchmarks()
    identity = source_identity(ROOT)
    report = {"timestamp_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
        "platform": platform.platform(), "commit": identity["commit"], "source_identity_kind": identity["kind"],
        "dirty_worktree": identity["dirty_worktree"],
        "synthetic_only": True, "monte_carlo": simulations, "performance": performance, "recording": recording_benchmark(),
        "passed": all(a["passed"] for s in simulations for a in s["actions"].values()) and performance["all_completed"] and performance["target_met"]}
    (directory / "analysis-validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(directory), "passed": report["passed"],
                      "p50": performance["p50_seconds"], "p95": performance["p95_seconds"], "max": performance["max_seconds"]}), flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
