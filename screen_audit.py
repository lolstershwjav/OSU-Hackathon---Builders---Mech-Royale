"""Audit archived fast-gate rejections against a frozen incumbent and league."""
import argparse
import json
import random
from pathlib import Path

from arena_sim import Simulator
from train import STARTERS, evaluate, policy_id, score


def select(paths):
    records = sorted((json.loads(p.read_text()) for p in paths), key=lambda d: d["total_generations"])
    current = records[-1]
    base = current["policies"][current["champion"]]
    league = list(STARTERS.values()) + [current["policies"][name] for name in STARTERS]
    chosen = {"incumbent": {"weights": base, "generation": current["total_generations"]}}
    pool = {}
    for record in records:
        gate = record.get("promotion")
        if not gate or gate["promoted"]:
            continue
        weights = record["policies"][gate["challenger"]]
        key = policy_id(weights)
        if key == policy_id(base):
            continue
        t, b = gate["challenger_metrics"], gate["incumbent_metrics"]
        squad = t["squad"] - b["squad"]
        ffa = t["free_for_all"] - b["free_for_all"]
        combined = score(t) - score(b)
        if squad > 0 and ffa >= 0 and combined > .10:
            continue
        pool[key] = dict(weights=weights, generation=record["total_generations"], squad_delta=squad,
                         ffa_delta=ffa, deficit=max(0, -squad) + max(0, -ffa) + max(0, .10-combined))
    if len(pool) < 4:
        raise ValueError("Four distinct historical fast-gate rejections are required")
    for name, metric in (("squad_specialist", lambda x: -x["squad_delta"]),
                         ("clear_rejection", lambda x: -x["deficit"]),
                         ("near_miss_1", lambda x: x["deficit"]), ("near_miss_2", lambda x: x["deficit"])):
        chosen[name] = pool.pop(min(pool, key=lambda k: metric(pool[k])))
    return chosen, league


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    chosen, league = select(args.archive.glob("checkpoint-*.json"))
    sim = Simulator(args.kit, .1)
    seeds = random.Random(args.seed).sample(range(30_000_000, 40_000_000), 12)
    report = dict(seed=args.seed, candidates=chosen, league=league, stages={})
    def save():
        tmp = args.output.with_suffix(".tmp")
        tmp.write_text(json.dumps(report, indent=2) + "\n")
        tmp.replace(args.output)
    names = list(chosen)
    for stage, panel in (("screen", seeds[:6]), ("confirmation", seeds[6:])):
        data = report["stages"][stage] = dict(seeds=panel, results={})
        for name in names:
            w = chosen[name]["weights"]
            result = data["results"][name] = {"fast": evaluate(w, league, panel)}
            for mode in ("squad", "free_for_all"):
                result[mode] = sim.evaluate(w, league, panel, [mode])[mode]
                save()
                print(stage, name, mode, result[mode]["placement"], flush=True)
        rows = data["results"]
        for source in ("fast", "map"):
            data[source + "_squad_order"] = sorted(names, key=lambda n: -(rows[n]["fast"]["squad"]
                if source == "fast" else rows[n]["squad"]["placement"]))
        def eligible(n):
            return (rows[n]["squad"]["placement"] > rows["incumbent"]["squad"]["placement"]
                    and rows[n]["free_for_all"]["placement"] >= rows["incumbent"]["free_for_all"]["placement"])
        names = ["incumbent"] + [n for n in names if eligible(n)]
        data["overlooked"] = names[1:]
        save()
        if len(names) == 1:
            break


if __name__ == "__main__":
    main()
