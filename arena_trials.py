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


class RouteTimedRotation(Bot):
    def zone_deadline(self, me, zone, remaining, next_margin):
        if super().zone_deadline(me, zone, remaining, next_margin):
            return True
        if not self.navigator or not self.navigator.path or next_margin >= 0:
            return False
        path = self.navigator.path
        if distance(path[-1], zone["next_center"]) >= zone["next_radius"] - 7:
            return False
        length = 0.0
        previous = me["pos"]
        for point in path:
            length += distance(previous, point)
            if distance(point, zone["next_center"]) < zone["next_radius"] - 7:
                break
            previous = point
        speed = me["stats"].get("walk_speed", self.mech_config["walk_speed"])
        return length / max(speed, 1) + 3 >= remaining


class QuietRecovery(CoverRecovery):
    def tactical_mode(self, mode, obs, enemies):
        result = super().tactical_mode(mode, obs, enemies)
        me = obs["self"]
        if self.waypoint and me["hp"] < .8 * me["max_hp"]:
            delay = me["stats"].get("regen_delay", self.mech_config.get("regen_delay", 5))
            self.retreat_until = max(self.retreat_until, self.last_hit + delay + 2)
        return "evade" if obs["t"] < self.retreat_until else result

    def act(self, obs):
        action = super().act(obs)
        if (self.waypoint and obs["self"]["in_zone"]
                and distance(obs["self"]["pos"], self.waypoint) < 4):
            action["fire"] = [False, False]
        return action


class GoalBraking(Bot):
    def recovery_goal(self, obs, threats, goal, urgent):
        self.braking_distance = distance(obs["self"]["pos"], goal)
        return goal

    def act(self, obs):
        action = super().act(obs)
        if obs["self"]["grounded"] and obs["self"]["in_zone"] and self.braking_distance < 4:
            action["move"] = [v * self.braking_distance / 4 for v in action["move"]]
            action["boost"] = False
        return action


class RememberedCover(CoverRecovery):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sightings = {}

    def tactical_mode(self, mode, obs, enemies):
        now = obs["t"]
        self.sightings = {key: sighting for key, sighting in self.sightings.items()
                          if now - sighting[0] <= 4}
        self.sightings.update({e["id"]: (now, tuple(e["pos"][:2])) for e in enemies})
        return super().tactical_mode(mode, obs, enemies)

    def recovery_goal(self, obs, threats, goal, urgent):
        position = obs["self"]["pos"]
        remembered = sorted((point for _, point in self.sightings.values()
                             if distance(position, point) < 100),
                            key=lambda point: distance(position, point))
        return super().recovery_goal(obs, tuple(remembered[:5]), goal, urgent)


class EnergyReserve(Bot):
    def act(self, obs):
        action = super().act(obs)
        me = obs["self"]
        immediate_pressure = obs["t"] - self.last_hit < 1.5
        zone_edge = obs["zone"]["radius"] - distance(me["pos"], obs["zone"]["center"])
        if me["grounded"] and me["energy"] < 45 and zone_edge > 12 and not immediate_pressure:
            action["boost"] = False
        return action


class OpeningPursuit(Bot):
    def tactical_mode(self, mode, obs, enemies):
        if obs["t"] < 60 and enemies:
            return "hold" if len(enemies) == 1 and obs["t"] - self.last_hit > 2 else "evade"
        return mode


class EarlyLoot(Bot):
    def tactical_mode(self, mode, obs, enemies):
        if obs["t"] < 20 and not enemies and obs["t"] - self.last_hit > 5:
            me = obs["self"]
            zone = obs["zone"]
            if any(distance(me["pos"], item["pos"]) < 35
                   and distance(item["pos"], zone["next_center"]) < zone["next_radius"] - 20
                   for item in [*obs["cubes"], *obs["pickups"]]):
                return "scavenge"
        return mode


class PracticeArming(Bot):
    """Prioritize a nearby secondary during a quiet opening."""
    def tactical_mode(self, mode, obs, enemies):
        me, zone = obs["self"], obs["zone"]
        if (obs["t"] >= 60 or enemies or obs["t"] - self.last_hit < 5
                or me["weapons"][1] is not None):
            return mode
        if any(item["kind"] in ("laser", "lightning")
               and distance(me["pos"], item["pos"]) < 45
               and abs(item["pos"][2] - me["pos"][2]) < 8
               and distance(item["pos"], zone["next_center"]) < zone["next_radius"] - 20
               for item in obs["pickups"]):
            return "scavenge"
        return mode


class FormatAdaptive(PracticeArming):
    """Use practice priorities until observations suggest other mechs fight."""
    previous_alive = None
    last_mech_hit = -float("inf")
    inferred_ffa = False

    def tactical_mode(self, mode, obs, enemies):
        now = obs["t"]
        if any(event["type"] in ("hit", "kill") and 0 <= event.get("target", -1) < 16
               for event in obs["events"]):
            self.last_mech_hit = now
        alive = obs["alive"]
        if (self.previous_alive is not None and alive < self.previous_alive
                and now < obs["zone"]["shrink_start"] and now - self.last_mech_hit > 12):
            self.inferred_ffa = True
        self.previous_alive = alive
        if self.inferred_ffa:
            return mode
        opening = super().tactical_mode(mode, obs, enemies)
        if now < 60 and enemies:
            return "hold" if len(enemies) == 1 and now - self.last_hit > 2 else "evade"
        return opening


class HealthRecovery(CoverRecovery):
    recovering = False

    def tactical_mode(self, mode, obs, enemies):
        me = obs["self"]
        health = me["hp"] / me["max_hp"]
        recent_pressure = obs["t"] - self.last_hit < 2
        if health < .55 and (recent_pressure or len(enemies) >= 2):
            self.recovering = True
        elif health >= .80:
            self.recovering = False
        self.retreat_until = obs["t"] + 1 if self.recovering else 0
        return "evade" if self.recovering else mode

    def act(self, obs):
        action = super().act(obs)
        if self.recovering and self.waypoint and distance(obs["self"]["pos"], self.waypoint) < 4:
            action["fire"] = [False, False]
        return action


class ArmedRecovery(HealthRecovery):
    def recovery_goal(self, obs, threats, goal, urgent):
        goal = super().recovery_goal(obs, threats, goal, urgent)
        me, zone = obs["self"], obs["zone"]
        if urgent or self.recovering or threats or me["weapons"][1] or obs["t"] > 90:
            return goal
        weapons = [item["pos"] for item in obs["pickups"]
                   if item["kind"] in ("laser", "lightning")
                   and abs(item["pos"][2] - me["pos"][2]) < 5
                   and distance(me["pos"], item["pos"]) < 50
                   and distance(item["pos"], zone["next_center"]) < zone["next_radius"] - 20]
        return min(weapons, key=lambda pos: distance(me["pos"], pos), default=goal)


class SpecializedPolicies(ArmedRecovery):
    """Test a practice opener and a separate fallback policy."""
    ffa_weights = None
    previous_alive = None
    unexplained_deaths = 0
    inferred_ffa = False

    def act(self, obs):
        alive = obs["alive"]
        if self.previous_alive is not None and obs["t"] <= 90:
            kills = sum(event["type"] == "kill" for event in obs["events"])
            self.unexplained_deaths += max(0, self.previous_alive - alive - kills)
            self.inferred_ffa = self.unexplained_deaths >= 2
        self.previous_alive = alive
        if self.inferred_ffa and self.ffa_weights is not None:
            self.weights = self.ffa_weights
        return super().act(obs)

    def tactical_mode(self, mode, obs, enemies):
        return mode if self.inferred_ffa else super().tactical_mode(mode, obs, enemies)

    def recovery_goal(self, obs, threats, goal, urgent):
        return goal if self.inferred_ffa else super().recovery_goal(obs, threats, goal, urgent)


class FightRead(SpecializedPolicies):
    """Compare projected damage while near a reachable recovery waypoint."""
    def tactical_mode(self, mode, obs, enemies):
        mode = super().tactical_mode(mode, obs, enemies)
        if self.inferred_ffa or not self.navigator or not enemies:
            return mode
        me = obs["self"]
        arena = self.navigator.arena
        shooters = [e for e in enemies if distance(me["pos"], e["pos"]) < 110
                    and not arena.cover(me["pos"], e["pos"][:2])]
        if len(shooters) < 2:
            return mode
        own_gun = me["weapons"][0]["stats"]
        nearest = min(shooters, key=lambda e: distance(me["pos"], e["pos"]))
        outgoing = own_gun["damage"] / max(.1, own_gun.get("cooldown", .25))
        incoming = outgoing * len(shooters)
        survival = me["hp"] / incoming
        time_to_win = nearest["hp"] / outgoing
        if survival < time_to_win:
            previous_deadline = self.retreat_until
            self.retreat_until = obs["t"] + 1
            goal = super().recovery_goal(obs, tuple(e["pos"][:2] for e in shooters),
                                         me["pos"], not me["in_zone"])
            travel = distance(me["pos"], goal) / max(1, self.mech_config["walk_speed"])
            if self.waypoint and travel < survival:
                self.recovering = True
                return "evade"
            self.retreat_until = previous_deadline
        return mode


class CleanupTarget(Bot):
    def combat_target(self, obs, enemies, nearest):
        if not self.navigator or len(enemies) < 2:
            return nearest
        me = obs["self"]
        if me["hp"] < .55 * me["max_hp"]:
            return nearest
        position = me["pos"]
        arena = self.navigator.arena
        weak = [enemy for enemy in enemies
                if enemy["hp"] < .30 * enemy["max_hp"]
                and distance(position, enemy["pos"]) <= 52
                and not arena.cover(position, enemy["pos"][:2])]
        return min(weak, key=lambda enemy: enemy["hp"], default=nearest)


class CoverPeek(CoverRecovery):
    def recovery_goal(self, obs, threats, goal, urgent):
        me = obs["self"]
        if (urgent or len(threats) != 1 or not self.navigator
                or me["hp"] < .75 * me["max_hp"]
                or obs["t"] - self.last_hit < 3):
            self.peek_anchor = None
            return super().recovery_goal(obs, threats, goal, urgent)
        arena = self.navigator.arena
        pos = me["pos"]
        threat = threats[0]
        anchor = getattr(self, "peek_anchor", None)
        zone = obs["zone"]
        if (anchor is not None and distance(pos, anchor) < 12
                and distance(anchor, zone["next_center"]) < zone["next_radius"] - 7
                and arena.cover(anchor, threat)):
            if obs["t"] >= self.peek_until:
                if distance(pos, anchor) > 2:
                    return anchor
                self.peek_anchor = None
            else:
                return self.peek_point
        if not arena.cover(pos, threat):
            return goal
        shape = Navigator.shape({**self.mech_config, **me["stats"]})
        for dx, dy in DIRECTIONS[::2]:
            x, y = pos[0] + 5 * dx, pos[1] + 5 * dy
            point = (x, y, arena.ground(x, y))
            if (distance(point, zone["next_center"]) < zone["next_radius"] - 7
                    and not arena.cover(point, threat) and arena.segment(pos, point, shape)):
                self.peek_anchor, self.peek_point = pos, point
                self.peek_until = obs["t"] + 1.4
                return point
        return goal


CONTROLLERS = {
    "early-rotation": EarlyRotation,
    "pressure-retreat": PressureRetreat,
    "cover-recovery": CoverRecovery,
    "route-timed": RouteTimedRotation,
    "quiet-recovery": QuietRecovery,
    "goal-braking": GoalBraking,
    "remembered-cover": RememberedCover,
    "energy-reserve": EnergyReserve,
    "opening-pursuit": OpeningPursuit,
    "early-loot": EarlyLoot,
    "practice-arming": PracticeArming,
    "format-adaptive": FormatAdaptive,
    "health-recovery": HealthRecovery,
    "armed-recovery": ArmedRecovery,
    "specialized-policies": SpecializedPolicies,
    "fight-read": FightRead,
    "cleanup-target": CleanupTarget,
    "cover-peek": CoverPeek,
}

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
    parser.add_argument("--controller", choices=CONTROLLERS, default="early-rotation")
    args = parser.parse_args()
    c = json.loads(args.checkpoint.read_text())
    weights = c["policies"][c["champion"]]
    league = [*STARTERS.values(), *(c["policies"][name] for name in STARTERS)]
    controller = CONTROLLERS[args.controller]
    candidate = weights
    selection = {}
    if issubclass(controller, SpecializedPolicies):
        validation = c["validation"]
        squad = max(validation, key=lambda name: validation[name]["squad"])
        ffa = max(validation, key=lambda name: validation[name]["free_for_all"])
        candidate = c["policies"][squad]
        controller.ffa_weights = c["policies"][ffa]
        selection = {"squad_policy": squad, "free_for_all_policy": ffa}
    report = compare(args.kit, weights, candidate, league, args.seed, controller, args.step)
    report.update(selection)
    report.update(controller=controller.__name__, checkpoint_seed=c["seed"], seed=args.seed)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
