"""Test whether individual tactic rows explain placement changes."""
import argparse
import json
import random
from pathlib import Path

from arena_sim import Simulator
from policy import MODES


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, required=True)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    audit = json.loads(args.audit.read_text())
    base = audit["candidates"]["incumbent"]["weights"]
    candidate = audit["candidates"][args.candidate]["weights"]
    variants = {"incumbent": base, "challenger": candidate}
    for i, tactic in enumerate(MODES):
        if candidate[i] != base[i]:
            variants[f"add_{tactic}"] = base[:i] + [candidate[i]] + base[i + 1:]
            variants[f"revert_{tactic}"] = candidate[:i] + [base[i]] + candidate[i + 1:]
    seeds = random.Random(args.seed).sample(range(40_000_000, 50_000_000), 6)
    report = dict(candidate=args.candidate, seeds=seeds, step_s=.1, results={})
    sim = Simulator(args.kit, .1)
    evaluated = {}
    for label, weights in variants.items():
        key = tuple(map(tuple, weights))
        if key not in evaluated:
            evaluated[key] = sim.evaluate(weights, audit["league"], seeds, ("squad", "free_for_all"))
        row = report["results"][label] = evaluated[key]
        comparisons = {}
        for reference in ("incumbent", "challenger"):
            if reference in report["results"]:
                baseline = report["results"][reference]
                comparisons[reference] = {
                    mode: [a["placement"] - b["placement"]
                           for a, b in zip(row[mode]["runs"], baseline[mode]["runs"])]
                    for mode in row
                }
        report.setdefault("paired_placement_deltas", {})[label] = comparisons
        temporary = args.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, indent=2) + "\n")
        temporary.replace(args.output)
        print(label, {mode: result["placement"] for mode, result in row.items()}, flush=True)


if __name__ == "__main__":
    main()
