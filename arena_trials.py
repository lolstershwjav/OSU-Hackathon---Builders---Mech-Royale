"""Compare one controller or weight change on paired map-backed matches."""
import argparse
import json
import random
from pathlib import Path
from statistics import mean

from arena_sim import Bot, PROFILES, Simulator
from bot import DIRECTIONS, distance
from navigation import Navigator
from train import STARTERS


class EarlyRotation(Bot):
    def zone_deadline(self, me, zone, remaining, next_margin):
        outside = max(0, -next_margin)
        speed = me["stats"].get("walk_speed", self.mech_config["walk_speed"])
        # Allow for route detours and reserve time to recover from a blocked step.
        return (outside > 0 and outside * 1.6 / max(1, speed) + 10 >= remaining
                or super().zone_deadline(me, zone, remaining, next_margin))


class PressureRetreat(Bot):
    retreat_until = 0.0

    def tactical_mode(self, mode, obs, enemies):
        now = obs["t"]
        position = obs["self"]["pos"]
        nearby = sum((enemy["pos"][0] - position[0]) ** 2
                     + (enemy["pos"][1] - position[1]) ** 2 <= 65 ** 2
                     and abs(enemy["pos"][2] - position[2]) <= 20 for enemy in enemies)
        if nearby >= 2 and now - self.last_hit < 1.5:
            self.retreat_until = now + 3
        return "evade" if now < self.retreat_until else mode


class CoverRecovery(PressureRetreat):
    waypoint = None

    def recovery_goal(self, obs, threats, goal, urgent):
        now = obs["t"]
        if urgent or now >= self.retreat_until or not self.navigator:
            self.waypoint = None
            return goal
        me, zone = obs["self"], obs["zone"]
        x, y, z = position = me["pos"]
        arena = self.navigator.arena

        def safe(point):
            return (distance(point, zone["next_center"]) < zone["next_radius"] - 7
                    and all(arena.cover(point, threat) for threat in threats))

        if self.waypoint and safe(self.waypoint):
            return self.waypoint
        self.waypoint = None
        if not threats:
            return position
        shape = Navigator.shape({**self.mech_config, **me["stats"]})
        for reach in (8, 16, 24):
            for dx, dy in DIRECTIONS[::2]:
                px, py = x + reach * dx, y + reach * dy
                point = (px, py, arena.ground(px, py))
                if safe(point) and arena.segment(position, point, shape):
                    self.waypoint = point
                    return point
        return goal


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
    parser.add_argument("--controller", choices=("early-rotation", "pressure-retreat", "cover-recovery"),
                        default="early-rotation")
    args = parser.parse_args()
    c = json.loads(args.checkpoint.read_text())
    weights = c["policies"][c["champion"]]
    league = [*STARTERS.values(), *(c["policies"][name] for name in STARTERS)]
    controller = {"early-rotation": EarlyRotation, "pressure-retreat": PressureRetreat,
                  "cover-recovery": CoverRecovery}[args.controller]
    report = compare(args.kit, weights, weights, league, args.seed, controller, args.step)
    report.update(controller=controller.__name__, checkpoint_seed=c["seed"], seed=args.seed)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
