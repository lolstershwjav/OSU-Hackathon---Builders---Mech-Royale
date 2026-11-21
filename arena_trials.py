"""Compare one controller or weight change on paired map-backed matches."""
import argparse
import json
import random
from pathlib import Path
from statistics import mean

from arena_sim import Bot, PROFILES, Simulator
from train import STARTERS


class EarlyRotation(Bot):
    def zone_deadline(self, me, zone, remaining, next_margin):
        outside = max(0, -next_margin)
        speed = me["stats"].get("walk_speed", self.mech_config["walk_speed"])
        # Allow for route detours and reserve time to recover from a blocked step.
        return (outside > 0 and outside * 1.6 / max(1, speed) + 10 >= remaining
                or super().zone_deadline(me, zone, remaining, next_margin))


def compare(kit, incumbent, challenger, league, seed, controller=Bot, step=.1):
    simulator = Simulator(kit, step)
    seeds = random.Random(seed).sample(range(10_000_000, 20_000_000), 6)
    rows = []
    for i, game_seed in enumerate(seeds):
        profile = PROFILES[i % len(PROFILES)]
        for mode in ("squad", "free_for_all"):
            row = {"seed": game_seed, "profile": profile, "format": mode}
            for label, weights, implementation in (("baseline", incumbent, Bot),
                                                   ("candidate", challenger, controller)):
                simulator.controller = implementation
                points, seconds, diagnostics = simulator.match(weights, league, game_seed, mode == "squad", profile)
                row[label] = {"points": points, "seconds": seconds, **diagnostics}
            rows.append(row)
            print(game_seed, mode, row["baseline"]["points"], row["candidate"]["points"], flush=True)
        if i in (2, 5):
            means = {mode: {label: mean(r[label]["points"] for r in rows if r["format"] == mode)
                            for label in ("baseline", "candidate")} for mode in ("squad", "free_for_all")}
            delta = {mode: m["candidate"] - m["baseline"] for mode, m in means.items()}
            if i == 2 and min(delta.values()) < -1:
                break
    return {"step_s": step, "paired": rows, "placement_means": means,
            "passed": delta["squad"] > 0 and delta["free_for_all"] >= 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--step", type=float, default=.1)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    c = json.loads(args.checkpoint.read_text())
    weights = c["policies"][c["champion"]]
    league = [*STARTERS.values(), *(c["policies"][name] for name in STARTERS)]
    report = compare(args.kit, weights, weights, league, args.seed, EarlyRotation, args.step)
    report.update(controller="EarlyRotation", checkpoint_seed=c["seed"], seed=args.seed)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
