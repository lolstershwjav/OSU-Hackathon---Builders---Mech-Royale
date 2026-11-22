#!/usr/bin/env python3
"""Tune Sasuke's squad-mode parameters (the ones Itachi/Naruto actually run in 15 v 1).

Needs the patched giddy/mech/brain.go (SASUKE_SQUAD env override) and a rebuilt sasuke-bin.exe.

    python tune_squad.py --kit C:\\Users\\ouran\\Hackathon\\kit\\mechfight-kit --output tuned.json

Method: (1+K) hill climb with paired seeds. Each generation mutates the current best 1-3
parameters K times, plays the SAME train seeds as the current best, keeps a mutant only if
it wins by --margin points. At the end the winner and the untouched defaults are replayed on
FRESH holdout seeds in squad AND ffa (tuning must not break FFA). Trust only the holdout.
"""
import argparse
import json
import math
import os
import random
import sys
from pathlib import Path
from statistics import mean, stdev

from itachi_eval import PROFILES, STARTERS, SasukeOnly, Simulator, run_match  # also applies kill-event patch

# name: (low, high, value squadParams() uses today)
SPACE = {
    "EngageDist": (30, 120, 60), "GunRange": (50, 120, 85), "LightRange": (15, 45, 28),
    "LaserRange": (70, 170, 120), "Stick": (0, 200, 120), "HPw": (0.02, 0.5, 0.2),
    "LowBonus": (0, 200, 100), "MaxTarget": (150, 300, 240), "FightLoot": (0, 120, 70),
    "FarmHP": (0.3, 0.9, 0.5), "LootRadius": (60, 240, 150), "Finish": (0, 40, 14),
    "OddsFlee": (0, 0.6, 0.0), "RetreatHP": (0.2, 0.6, 0.38), "RecoverHP": (0.6, 0.95, 0.8),
}
TOGGLES = {"LoseRule": True, "CrowdHide": True, "AllFoes": False}
DEFAULTS = {**{k: v[2] for k, v in SPACE.items()}, **TOGGLES}
CACHE = {}


def play(sim, weights, league, cand, seed, profile, squad):
    """One match of Sasuke (never hands off) with squad params `cand`. Returns points."""
    key = (json.dumps(cand, sort_keys=True), seed, squad)
    if key not in CACHE:
        os.environ["SASUKE_SQUAD"] = json.dumps(cand)
        CACHE[key] = run_match(sim, SasukeOnly, weights, league, seed, squad, profile)["points"]
    return CACHE[key]


def mutate(cand, rng):
    new = dict(cand)
    for name in rng.sample(list(DEFAULTS), rng.choice((1, 2, 3))):
        if name in TOGGLES:
            new[name] = not new[name]
        else:
            lo, hi, _ = SPACE[name]
            new[name] = round(min(hi, max(lo, new[name] + rng.gauss(0, 0.2 * (hi - lo)))), 3)
    return new


def paired(a, b):
    d = [x - y for x, y in zip(a, b)]
    h = 1.96 * stdev(d) / math.sqrt(len(d)) if len(d) > 1 else 0.0
    return mean(d), h


def search(ctx, train, gens, K, probe, margin, rng, log):
    run = lambda cand, seeds, squad=True: [play(*ctx, cand, s, PROFILES[i % 3], squad) for i, s in seeds]
    best, best_sc = dict(DEFAULTS), run({}, train)
    print(f"baseline train mean {mean(best_sc):.2f}", flush=True)
    for g in range(gens):
        for k in range(K):
            m = mutate(best, rng)
            changed = {n: m[n] for n in m if m[n] != best[n]}
            d0 = mean(x - y for x, y in zip(run(m, train[:probe]), best_sc[:probe]))
            if d0 < -0.5:
                print(f"g{g}.{k} reject early ({d0:+.2f}) {changed}", flush=True)
                continue
            sc = run(m, train)
            d = mean(x - y for x, y in zip(sc, best_sc))
            ok = d >= margin
            print(f"g{g}.{k} {d:+.2f} {'ACCEPT' if ok else 'no'} {changed}", flush=True)
            log.append({"gen": g, "diff": d, "accepted": ok, "changed": changed})
            if ok:
                best, best_sc = m, sc
    return best, best_sc


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--kit", type=Path, required=True)
    ap.add_argument("--checkpoint", type=Path, default=Path("models/trained.json"))
    ap.add_argument("--train-seeds", type=int, default=16)
    ap.add_argument("--probe", type=int, default=6)
    ap.add_argument("--gens", type=int, default=6)
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--margin", type=float, default=0.3)
    ap.add_argument("--holdout-seeds", type=int, default=40)
    ap.add_argument("--seed", type=int, default=424242)
    ap.add_argument("--step", type=float, default=0.1)
    ap.add_argument("--output", type=Path, default=Path("tuned.json"))
    a = ap.parse_args()

    c = json.loads(a.checkpoint.read_text())
    weights = c["policies"][c["champion"]]
    league = [*STARTERS.values(), *(c["policies"][n] for n in STARTERS)]
    sim = Simulator(a.kit, a.step)
    pool = random.Random(a.seed).sample(range(10_000_000, 20_000_000), a.train_seeds + a.holdout_seeds)
    train, hold = list(enumerate(pool[:a.train_seeds])), list(enumerate(pool[a.train_seeds:]))
    ctx, rng, log = (sim, weights, league), random.Random(a.seed), []

    best, _ = search(ctx, train, a.gens, a.k, a.probe, a.margin, rng, log)
    tuned = {n: v for n, v in best.items() if v != DEFAULTS[n]}
    a.output.write_text(json.dumps({"overrides": tuned, "log": log}, indent=1))
    print("\ntuned overrides:", tuned or "(none beat the defaults)")

    print("\n=== HOLDOUT (fresh seeds, tuned vs defaults) ===")
    for squad in (True, False):
        base = [play(*ctx, {}, s, PROFILES[i % 3], squad) for i, s in hold]
        new = [play(*ctx, best, s, PROFILES[i % 3], squad) for i, s in hold]
        d, h = paired(new, base)
        verdict = "BETTER" if d - h > 0 else "WORSE" if d + h < 0 else "no clear difference"
        print(f"  {'squad' if squad else 'ffa  '} defaults {mean(base):.2f} tuned {mean(new):.2f} "
              f"diff {d:+.2f} +/- {h:.2f} -> {verdict}")
    print("\nIf squad is BETTER and ffa is not WORSE, bake it in (see instructions).")


if __name__ == "__main__":
    sys.exit(main())
