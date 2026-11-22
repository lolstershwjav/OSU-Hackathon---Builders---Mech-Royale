#!/usr/bin/env python3
"""Paired evaluation of Naruto / Itachi variants on the repo's map-backed simulator.

Run from the repo root:
    python itachi_eval.py --kit C:\\path\\to\\mechfight-kit --seeds 20 --baseline sasuke-only ^
        --variants sasuke-only,sasuke-retreat,itachi,itachi-retreat --output run.json

Formats: "squad" = 15 house bots hunting you (15 v 1); "ffa" = 16-player battle royale.
Every variant plays the SAME seed/profile, so differences are paired against --baseline.
Sasuke answers on a wall-clock budget, so runs are not bit-reproducible; use >= 20-30 seeds.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path
from statistics import mean, stdev

from arena_sim import PROFILES, Simulator
from bob_mix import Bob
from itachi import Itachi
from train import STARTERS


# --- simulator fidelity patch ------------------------------------------------
# The simulator never sends {"type": "kill"} events, but the real game does and the
# handoff detector subtracts them. Without this, our OWN kills look like mystery deaths.
_orig_damage = Simulator.apply_damage


def _damage_with_kill_events(self, source, target, damage, now, kind):
    was_alive = target.hp > 0
    _orig_damage(self, source, target, damage, now, kind)
    if was_alive and target.hp <= 0 and source is not target:
        source.events.append({"type": "kill", "target": target.slot})


Simulator.apply_damage = _damage_with_kill_events


# --- variants ---------------------------------------------------------------
class ItachiOriginal(Itachi):      # your first itachi.py: threshold 3, one-way, no burst rule
    name = "Itachi-orig"
    BURSTS, REVERT, WARM = 1, False, False


class ItachiV2(Itachi):            # burst rule + reversible handoff
    name = "Itachi-v2"


class ItachiWarm(Itachi):          # v2 + keep Sakura warm during practice
    name = "Itachi-v2-warm"
    WARM = True


class SasukeOnly(Itachi):          # control: never hand off
    name = "Sasuke-only"
    DEATHS = 10 ** 6


class SakuraOnly(Itachi):          # control: hand off immediately
    name = "Sakura-only"
    DEATHS, BURSTS = 0, 0


class SasukeRetreat(SasukeOnly):   # Sasuke-only + pressure retreat (isolates the retreat)
    name = "Sasuke-retreat"
    RETREAT = True


class ItachiRetreat(Itachi):       # Itachi v2 + pressure retreat
    name = "Itachi-retreat"
    RETREAT = True


VARIANTS = {"naruto": Bob, "itachi-orig": ItachiOriginal, "itachi": ItachiV2,
            "itachi-warm": ItachiWarm, "sasuke-only": SasukeOnly, "sakura-only": SakuraOnly,
            "sasuke-retreat": SasukeRetreat, "itachi-retreat": ItachiRetreat}
REGISTRY = []
DIAG_KEYS = ("zone_deaths", "combat_deaths", "pressure_s", "zone_damage", "combat_damage")


def factory(cls):
    def make(hello, weights=None, arena=None):
        bot = cls(hello)
        REGISTRY.append(bot)
        return bot
    return make


def run_match(sim, cls, weights, league, seed, squad, profile):
    REGISTRY.clear()
    sim.controller = factory(cls)
    points, seconds, diag = sim.match(weights, league, seed, squad, profile)
    bot = REGISTRY[0] if REGISTRY else None
    info = {"points": points, "seconds": round(seconds, 1)}
    info.update({k: round(float(diag.get(k, 0)), 1) for k in DIAG_KEYS})
    if bot is not None:
        if hasattr(bot, "report"):
            info.update(bot.report())
        else:  # plain Naruto
            info.update({"switch_t": None, "unexplained": bot.unexplained})
        bot.close()
    return info


def ci(diffs):
    if len(diffs) < 2:
        return 0.0, 0.0
    return mean(diffs), 1.96 * stdev(diffs) / math.sqrt(len(diffs))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--kit", type=Path, required=True)
    p.add_argument("--checkpoint", type=Path, default=Path("models/trained.json"))
    p.add_argument("--seeds", type=int, default=20)
    p.add_argument("--seed", type=int, default=20261121)
    p.add_argument("--step", type=float, default=0.1)
    p.add_argument("--baseline", default="sasuke-only", help="variant every other variant is compared to")
    p.add_argument("--variants", default="sasuke-only,sasuke-retreat,itachi,itachi-retreat")
    p.add_argument("--output", type=Path, default=Path("itachi_results.json"))
    a = p.parse_args()

    unknown = [v for v in a.variants.split(",") + [a.baseline] if v not in VARIANTS]
    if unknown:
        sys.exit(f"unknown variant(s): {unknown}. Known: {list(VARIANTS)}")
    names = list(dict.fromkeys([a.baseline, *a.variants.split(",")]))
    base = a.baseline

    c = json.loads(a.checkpoint.read_text())
    weights = c["policies"][c["champion"]]
    league = [*STARTERS.values(), *(c["policies"][n] for n in STARTERS)]
    sim = Simulator(a.kit, a.step)
    seeds = random.Random(a.seed).sample(range(10_000_000, 20_000_000), a.seeds)

    rows = []
    for i, s in enumerate(seeds):
        profile = PROFILES[i % len(PROFILES)]
        for fmt in ("squad", "ffa"):
            row = {"seed": s, "profile": profile, "format": fmt}
            for n in names:
                row[n] = run_match(sim, VARIANTS[n], weights, league, s, fmt == "squad", profile)
            rows.append(row)
            print(s, fmt, {n: row[n]["points"] for n in names}, flush=True)
        a.output.write_text(json.dumps({"variants": names, "baseline": base, "rows": rows}, indent=1))

    print(f"\n=== paired difference vs {base} (points, mean +/- 95% CI) ===")
    for fmt in ("squad", "ffa"):
        sub = [r for r in rows if r["format"] == fmt]
        print(f"\n[{fmt}]  n={len(sub)}  {base} mean = {mean(r[base]['points'] for r in sub):.2f}")
        for n in names:
            if n == base:
                continue
            d, h = ci([r[n]["points"] - r[base]["points"] for r in sub])
            wins = sum(r[n]["points"] > r[base]["points"] for r in sub)
            loss = sum(r[n]["points"] < r[base]["points"] for r in sub)
            verdict = "BETTER" if d - h > 0 else "WORSE" if d + h < 0 else "no clear difference"
            print(f"  {n:15s} {d:+.2f} +/- {h:.2f}  W/L {wins}/{loss}  -> {verdict}")

    print("\n=== how each variant dies / behaves (means per match) ===")
    for fmt in ("squad", "ffa"):
        sub = [r for r in rows if r["format"] == fmt]
        print(f"\n[{fmt}]")
        for n in names:
            m = lambda k: mean(r[n].get(k, 0) for r in sub)
            print(f"  {n:15s} pts {m('points'):5.2f} | zone deaths {m('zone_deaths'):.2f} "
                  f"combat deaths {m('combat_deaths'):.2f} | pressure s {m('pressure_s'):5.1f} "
                  f"| handoffs {sum(r[n].get('switch_t') is not None for r in sub)}/{len(sub)} "
                  f"| retreats {m('retreats'):.1f} | sasuke timeouts {m('fallbacks'):.0f}")
    print(f"\nraw data: {a.output}")


if __name__ == "__main__":
    sys.exit(main())
