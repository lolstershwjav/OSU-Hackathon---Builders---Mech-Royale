"""Compare local squad rankings with manually recorded official practice results."""
import argparse
from itertools import combinations
import json
import math
from pathlib import Path
from statistics import mean


def compare(local, official):
    matches = []
    seen = set()
    for entry in official:
        name, commit, points = entry["policy"], entry["commit"], entry["practice_points"]
        if name in seen:
            raise ValueError(f"Only one official result per policy is supported: {name}")
        seen.add(name)
        if not isinstance(commit, str) or not commit.strip():
            raise ValueError(f"Missing GitHub commit for {name}")
        if not isinstance(points, (int, float)) or not math.isfinite(points) or not 0 <= points <= 15:
            raise ValueError(f"Practice points must be between 0 and 15 for {name}")
        if name not in local["arena"] or "squad" not in local["arena"][name]:
            raise ValueError(f"No arena squad result for {name}")
        matches.append({"policy": name, "commit": commit, "official_squad": points,
                        "arena_squad": local["arena"][name]["squad"]["placement"],
                        "quick_squad": local["quick"][name]["squad"]})
    if not matches:
        raise ValueError("Provide at least one official squad practice result")
    comparison = {}
    for label in ("arena_squad", "quick_squad"):
        outcomes = []
        for a, b in combinations(matches, 2):
            observed = a["official_squad"] - b["official_squad"]
            predicted = a[label] - b[label]
            if observed and predicted:
                outcomes.append(observed * predicted > 0)
        comparison[label] = {"order_agreement": mean(outcomes) if outcomes else None,
                             "comparable_pairs": len(outcomes),
                             "mean_absolute_error": mean(abs(r[label] - r["official_squad"]) for r in matches)}
    return {"matches": matches, "comparison": comparison}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arena-report", type=Path, required=True)
    parser.add_argument("--official", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = compare(json.loads(args.arena_report.read_text()), json.loads(args.official.read_text()))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(str(error))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
