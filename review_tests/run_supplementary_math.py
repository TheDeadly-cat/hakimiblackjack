"""48 fixed extra scenarios against the repository's Fraction oracle.

Run in this isolated directory: python run_supplementary_math.py
These are extra scenarios, not 48 newly discovered upstream unittest methods.
"""
from fractions import Fraction
from itertools import product
from pathlib import Path
import json
import time
from blackjack_lab.analysis.actions import solve_counts
from tests.analysis_reference import reference

scenarios = list(product(
    ((7, 8, 9, 10, 10, 10), (6, 7, 8, 9, 10, 10)),
    ((10, 2), (10, 6), (5, 6), (1, 6), (1, 1), (2, 3)),
    (1, 2, 6, 10),
))
report = []
start = time.perf_counter()
for cards, hand, up in scenarios:
    peek = up in (1, 10)
    expected = reference(cards, hand, up, peek)
    actual = solve_counts(tuple(cards.count(i) for i in range(1, 11)), hand, up, peek,
                          actions=("stand", "hit", "double", "surrender"))
    errors = []
    for action, result in expected["actions"].items():
        errors.append(abs(actual["actions"][action]["ev"] - float(result["ev"])))
        for payoff, probability in actual["actions"][action]["net_distribution"].items():
            errors.append(abs(probability - float(result["net_distribution"].get(Fraction(payoff), 0))))
    for outcome, probability in actual["dealer_distribution"].items():
        errors.append(abs(probability - float(expected["dealer_distribution"].get(outcome, 0))))
    for value, label in enumerate(actual["next_draw"], 1):
        errors.append(abs(actual["next_draw"][label] - float(expected["next_draw"].get(value, 0))))
    errors.append(abs(actual["hit_bust"] - float(expected["hit_bust"])))
    item = {"cards": cards, "hand": hand, "up": up, "peek": peek,
            "max_abs_error": max(errors), "passed": max(errors) <= 1e-10}
    report.append(item)
    assert item["passed"], item
summary = {"scenario_count": len(report), "passed_count": sum(x["passed"] for x in report),
           "max_abs_error": max(x["max_abs_error"] for x in report),
           "elapsed_seconds": time.perf_counter() - start,
           "method": "additional scenarios against the SAME repository Fraction reference; not a newly authored oracle",
           "scenarios": report}
print(json.dumps({k: v for k, v in summary.items() if k != "scenarios"}, indent=2))
Path("supplementary-math-rerun.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
