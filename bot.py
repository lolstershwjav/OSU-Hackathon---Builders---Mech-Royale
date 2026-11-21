"""Independent mechfight/1 controller with offline-trained tactical policies."""
import json
import math
import os
from pathlib import Path

import mechfight
from navigation import Arena, Navigator
from policy import choose, features

ROOT = Path(__file__).resolve().parent
MAP_CELL = 32
DIRECTIONS = tuple((math.cos(i * math.tau / 16), math.sin(i * math.tau / 16))
                   for i in range(16)) + ((0.0, 0.0),)
UPGRADES = ("armor", "hp", "regen", "speed", "energy", "gun_damage", "gun_rate")


def distance(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def angular_error(a, b):
    return abs(math.remainder(a - b, math.tau))


class Bot:
    name = "Mech Royale Policy League"

    def __init__(self, hello, *, weights=None, arena=None):
        if weights is None:
            report = json.loads((ROOT / "models/trained.json").read_text(encoding="utf-8"))
            weights = report["policies"][os.getenv("MECH_POLICY", report["champion"])]
        self.weights = weights
        self.pickup_radius = hello["config"]["loot"]["pickup_radius"]
        self.last_hit = -100.0
        self.last_bearing = None
        self.last_position = None
        self.last_move = (0, 0)
        self.blocked = 0
        self.navigator = None
        self.mech_config = hello["config"]["mech"]
        if arena is not None:
            self.navigator = Navigator(arena)
        elif os.getenv("MECH_NAV") != "local":
            try:
                self.navigator = Navigator(Arena(hello["map"]["dir"]))
            except (OSError, KeyError, ValueError):
                pass
        self.solids = {}
        map_path = Path(hello["map"]["dir"]) / "v2/world.json"
        if self.navigator is None and map_path.is_file():
            world = json.loads(map_path.read_text(encoding="utf-8"))
            for prism in world["prisms"]:
                xs, ys = prism["ring"][::2], prism["ring"][1::2]
                x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
                bounds = (x0, y0, x1, y1, prism["base"], prism["top"])
                for col in range(math.floor((x0 - 18) / MAP_CELL), math.floor((x1 + 18) / MAP_CELL) + 1):
                    for row in range(math.floor((y0 - 18) / MAP_CELL), math.floor((y1 + 18) / MAP_CELL) + 1):
                        self.solids.setdefault((col, row), []).append(bounds)

    def movement(self, obs, target, enemy, urgent, threats):
        me = obs["self"]
        x, y, z = me["pos"]
        if self.last_position is not None and distance(self.last_position, (x, y)) < .17:
            wants_motion = any(self.last_move) or (self.navigator and distance((x, y), target) > 4)
            self.blocked = self.blocked + 1 if wants_motion else 0
        else:
            self.blocked = max(0, self.blocked - 2)
        self.last_position = (x, y)
        tile = (math.floor(x / MAP_CELL), math.floor(y / MAP_CELL))
        nearby = [p for p in self.solids.get(tile, ())
                  if p[0] - 18 < x < p[2] + 18 and p[1] - 18 < y < p[3] + 18
                  and p[4] < z + 8 and p[5] > z + 1.3]
        zone = obs["zone"]
        shape = None
        if self.navigator:
            shape = Navigator.shape({**self.mech_config, **me["stats"]})
            target = self.navigator.waypoint(me["pos"], target, shape, zone, obs["t"],
                                             threats, me["grounded"], self.blocked > 8)
        shots = []
        for shot in obs["projectiles"][:36]:
            if distance(shot["pos"], (x, y)) < 90:
                sx, sy, sz = shot["pos"]
                vx, vy, vz = shot["vel"]
                shots.append((sx, sy, sz, vx, vy, vz, max(1, vx * vx + vy * vy)))

        goal_distance = distance((x, y), target)
        horizon = max(3, min(10, goal_distance)) if shape else 10

        def utility(direction):
            dx, dy = direction
            nx, ny = x + dx * horizon, y + dy * horizon
            value = goal_distance - distance((nx, ny), target)
            edge = distance((nx, ny), zone["center"]) - zone["radius"] + 6
            value -= 2.4 * max(0.0, edge)
            if shape and (dx or dy) and not self.navigator.arena.segment(
                    (x, y, z), (x + dx * 3.5, y + dy * 3.5), shape,
                    airborne=not me["grounded"]):
                return -math.inf
            for x0, y0, x1, y1, _, _ in nearby:
                if x0 - 2 < nx < x1 + 2 and y0 - 2 < ny < y1 + 2:
                    value -= 13
            if enemy and not urgent and distance((nx, ny), enemy["pos"]) < 25:
                value -= 7
            # Test predicted paths against each candidate's short-horizon motion.
            for sx, sy, sz, vx, vy, vz, speed2 in shots:
                end_x, end_y = x + dx * 3.5, y + dy * 3.5
                time = ((end_x - sx) * vx + (end_y - sy) * vy) / speed2
                if 0 < time < .38 and abs(sz + vz * time - (z + 4.5)) < 6:
                    miss = distance((end_x, end_y), (sx + vx * time, sy + vy * time))
                    value -= 2.3 * max(0, 7 - miss)
            if self.blocked > 8:
                value -= 7 * max(0, dx * self.last_move[0] + dy * self.last_move[1])
            return value

        self.last_move = max(DIRECTIONS, key=utility)
        return list(self.last_move)

    def zone_deadline(self, me, zone, remaining, next_margin):
        return next_margin < -max(8, remaining * 5)

    def tactical_mode(self, mode, obs, enemies):
        return mode

    def act(self, obs):
        me = obs["self"]
        x, y, z = position = me["pos"]
        zone = obs["zone"]
        now = obs["t"]
        for event in obs["events"]:
            if event["type"] == "damaged" and event.get("bearing") is not None:
                self.last_hit = now
                self.last_bearing = event["bearing"]
        damaged = now - self.last_hit < 4
        enemies = [enemy for enemy in obs["mechs"] if enemy["kind"] == "mech"]
        enemy = min(enemies, key=lambda e: distance(position, e["pos"]), default=None)
        separation = distance(position, enemy["pos"]) if enemy else 999.0
        hp = min(1, me["hp"] / me["max_hp"])
        enemy_hp = min(1, enemy.get("hp", enemy["max_hp"]) / enemy["max_hp"]) if enemy else 1
        secondary = me["weapons"][1]
        pickups = [item for item in obs["pickups"] if item["kind"] != "gun" and secondary is None]
        loot = min((item for item in [*obs["cubes"], *pickups]
                    if distance(item["pos"], zone["next_center"]) < zone["next_radius"] - 4
                    and abs(item["pos"][2] - z) < 8
                    and all(distance(item["pos"], e["pos"]) > 28 for e in enemies)),
                   key=lambda item: distance(position, item["pos"]), default=None)
        next_margin = zone["next_radius"] - distance(position, zone["next_center"])
        mode = choose(self.weights, features(
            hp, separation, len(enemies), damaged, enemy_hp,
            distance(position, loot["pos"]) if loot else 999,
            (40 - next_margin) / 60,
        ))
        mode = self.tactical_mode(mode, obs, enemies)
        if hp < .28:
            mode = "evade"
        urgent = (not me["in_zone"] or zone["radius"] - distance(position, zone["center"]) < 12
                  or self.zone_deadline(me, zone, zone["shrink_end"] - now, next_margin))
        goal = position
        if urgent or zone["next_radius"] < 18:
            goal = zone["next_center"]
        elif next_margin < 18:
            center = zone["next_center"]
            ratio = max(0, zone["next_radius"] - 28) / max(1, distance(position, center))
            goal = (center[0] + (x - center[0]) * ratio, center[1] + (y - center[1]) * ratio)
        elif mode == "evade" and (enemy or damaged):
            bearing = (math.atan2(enemy["pos"][1] - y, enemy["pos"][0] - x)
                       if enemy else self.last_bearing)
            goal = (x - 45 * math.cos(bearing), y - 45 * math.sin(bearing))
        elif mode == "scavenge" and loot and distance(position, loot["pos"]) < 130:
            goal = loot["pos"]
        elif mode == "engage" and enemy and separation < 180:
            dx, dy = (enemy["pos"][0] - x) / max(1, separation), (enemy["pos"][1] - y) / max(1, separation)
            side = 1 if (obs["tick"] // 80 + me["id"]) % 2 else -1
            advance = max(-1, min(1, (separation - 65) / 45))
            goal = (x + 35 * (advance * dx - side * dy), y + 35 * (advance * dy + side * dx))

        threats = tuple(tuple(e["pos"][:2]) for e in sorted(
            enemies, key=lambda e: distance(position, e["pos"]))[:3])
        if self.navigator and not urgent and enemy and mode in ("evade", "hold") and separation < 75:
            arena = self.navigator.arena
            shape = Navigator.shape({**self.mech_config, **me["stats"]})
            candidates = []
            for direction in DIRECTIONS[::2]:
                if not any(direction):
                    continue
                px, py = x + 12 * direction[0], y + 12 * direction[1]
                point = (px, py, arena.ground(px, py))
                if (distance(point, enemy["pos"]) > max(25, separation - 3)
                        and distance(point, zone["next_center"]) < zone["next_radius"] - 7
                        and arena.cover(point, threats[0])
                        and arena.clear(*point, *shape[:2])
                        and abs(point[2] - z) < 5):
                    candidates.append(point)
            if candidates:
                goal = min(candidates, key=lambda point: distance(point, goal))
        move = self.movement(obs, goal, enemy, urgent, threats)
        action = {"tick": obs["tick"], "move": move, "fire": [False, False]}
        action["boost"] = bool(any(move) and me["energy"] > 23
                               and (urgent or mode == "evade" and separation < 80))
        action["jump"] = bool(self.blocked > 13 and me["grounded"] and me["energy"] > 25)
        if obs["offer"]:
            choices = obs["offer"]["choices"]
            action["pick"] = min(range(len(choices)), key=lambda i: next(
                (j for j, key in enumerate(UPGRADES) if key in choices[i]["id"]), len(UPGRADES)))
        reachable = [item for item in pickups if distance(position, item["pos"]) <= self.pickup_radius
                     and abs(item["pos"][2] - z) <= 4]
        if reachable:
            priority = {"laser": 0, "lightning": 1, "mortar": 2}
            action["take"] = min(reachable, key=lambda item: priority[item["kind"]])["id"]

        target = enemy
        firing = enemy and (separation < 52 or mode == "engage" or damaged and mode != "evade")
        if not enemy and damaged:
            action["aim"] = [self.last_bearing, 0]
        if not enemy and mode == "scavenge" and not urgent:
            target = min((crate for crate in obs["crates"] if distance(position, crate["pos"]) < 55),
                         key=lambda crate: distance(position, crate["pos"]), default=None)
            firing = bool(target)
        if target:
            self.aim(action, me, target, firing)
        gun = me["weapons"][0]
        if gun and not firing and gun.get("ammo", 12) < 5 and not gun.get("reload", 0):
            action["reload"] = True
        return action

    @staticmethod
    def aim(action, me, target, firing):
        x, y, z = me["pos"]
        muzzle = z + me["stats"]["muzzle_height"]
        tx, ty, tz = target["pos"]
        target_height = 4.5 if "kind" in target else 2.0
        vx, vy, vz = target.get("vel", (0, 0, 0))
        best = None
        distance_to_target = distance((x, y), (tx, ty))
        for index, weapon in enumerate(me["weapons"]):
            if weapon is None:
                continue
            kind, stats = weapon["kind"], weapon["stats"]
            rng = stats["range"] if kind != "mortar" else stats["speed"] ** 2 / stats["gravity"]
            travel = distance_to_target / stats["speed"] if kind in ("gun", "mortar") else 0
            dx, dy = tx - x + vx * travel, ty - y + vy * travel
            dz = tz + target_height - muzzle + vz * travel
            if kind == "mortar":
                dz += .5 * stats["gravity"] * travel ** 2
            yaw = math.atan2(dy, dx)
            pitch = math.atan2(dz, max(1, math.hypot(dx, dy)))
            can_fire = bool(firing and distance_to_target <= .96 * rng
                            and weapon["cooldown"] < .10 and not weapon.get("reload", 0))
            if kind == "mortar":
                can_fire &= 28 < distance_to_target < .72 * rng
            if kind == "lightning":
                can_fire &= me["energy"] > 12
            charged = kind == "laser" and weapon.get("charge", 0) > 0
            rank = 3 if charged else 2 if can_fire and index else 1 if can_fire else 0
            if best is None or rank > best[0]:
                best = (rank, index, kind, yaw, pitch, can_fire, weapon)
        if best is None:
            return
        _, index, kind, yaw, pitch, can_fire, weapon = best
        action["aim"] = [yaw, pitch]
        error = max(angular_error(me["aim"][0], yaw), abs(me["aim"][1] - pitch))
        tolerance = max(.027, min(.16, 4 / max(1, distance_to_target)))
        if kind == "laser":
            charge = weapon.get("charge", 0)
            action["fire"][index] = bool(can_fire and (charge < weapon["stats"]["charge"]
                                                       or error > tolerance))
        else:
            action["fire"][index] = bool(can_fire and error < tolerance)


if __name__ == "__main__":
    mechfight.run(Bot)
