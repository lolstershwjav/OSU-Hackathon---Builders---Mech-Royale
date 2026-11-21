"""Map-backed sparring with the production bot and approximate match physics."""
import argparse
import copy
from dataclasses import dataclass, field
import gzip
import json
import math
from pathlib import Path
import random
from statistics import mean

from bot import Bot, angular_error, distance
from navigation import Arena, Navigator
from train import STARTERS, evaluate, score

PROFILES = ("pursuit", "flank", "zone")
TIERS = (0,) * 4 + (1,) * 4 + (2,) * 4 + (3,) * 3
REASONS = ("zone_damage", "combat_damage", "blocked_steps", "pressure_s", "shots", "hits",
           "zone_deaths", "combat_deaths", "timeouts")


def unit(x, y):
    length = max(1, math.hypot(x, y))
    return x / length, y / length


@dataclass
class Mech:
    slot: int
    pos: tuple
    hp: float
    energy: float
    ammo: int
    aim: list
    bot: Bot | None
    tier: int = 0
    navigator: Navigator | None = None
    ready: float = 0
    reload_at: float = 0
    last_damage: float = -100
    last_boost: float = -100
    cubes: int = 0
    vel: tuple = (0, 0, 0)
    events: list = field(default_factory=list)
    grounded: bool = True
    vertical_speed: float = 0
    secondary: str | None = None
    secondary_ready: float = 0
    charge: float = 0
    stats: dict = field(default_factory=dict)
    weapon_stats: dict = field(default_factory=dict)
    upgrade_counts: dict = field(default_factory=dict)
    offers: list = field(default_factory=list)
    diagnostics: dict = field(default_factory=lambda: dict.fromkeys(REASONS, 0))



@dataclass
class Projectile:
    source: Mech
    pos: tuple
    vel: tuple
    damage: float
    remaining: float
    kind: str = "gun"


class Simulator:
    def __init__(self, kit, step=.5, duration=None, controller=Bot):
        self.controller = controller
        if not math.isfinite(step) or not .1 <= step <= 1:
            raise ValueError("Decision interval must be between 0.1 and 1 second")
        with gzip.open(kit / "check/stream.jsonl.gz", "rt") as stream:
            self.hello = json.loads(next(stream))
        self.hello["map"]["dir"] = str(kit / "arena")
        self.config = self.hello["config"]
        self.arena = Arena(kit / "arena")
        self.arena.simulation_nodes = 12
        self.mech = self.config["mech"]
        self.gun = self.config["weapons"]["gun"]
        self.shape = Navigator.shape(self.mech)
        self.step = step
        self.duration = duration if duration is not None else self.config["match"]["duration_s"]
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("Duration must be positive")

    def floor(self, x, y):
        return x, y, self.arena.ground(x, y)

    def open_point(self, rng, center, radius, previous=(), spacing=0):
        for _ in range(350):
            angle = rng.uniform(0, math.tau)
            r = radius * math.sqrt(rng.random())
            point = self.floor(center[0] + r * math.cos(angle), center[1] + r * math.sin(angle))
            if self.arena.clear(*point, *self.shape[:2]) and all(distance(point, p) >= spacing for p in previous):
                return point
        raise RuntimeError("Could not sample a clear spawn")

    def zones(self, rng):
        config = self.config["zone"]
        center, radius = config["start_center"], config["start_radius"]
        t = 0
        for phase in config["phases"]:
            goal = self.open_point(rng, center, max(0, (radius - phase["radius"]) * .65))[:2]
            start, end = t + phase["hold_s"], t + phase["hold_s"] + phase["shrink_s"]
            yield center, radius, goal, phase["radius"], start, end, phase["dps"]
            center, radius, t = goal, phase["radius"], end

    @staticmethod
    def zone_at(phase, time):
        c, r, goal, radius, start, end, dps = phase
        blend = min(1, max(0, (time - start) / (end - start)))
        return {"center": tuple(a + (b - a) * blend for a, b in zip(c, goal)),
                "radius": r + (radius - r) * blend, "next_center": goal,
                "next_radius": radius, "shrink_start": start, "shrink_end": end, "dps": dps}

    def line_clear(self, a, b):
        steps = max(1, math.ceil(math.dist(a, b) / 2.5))
        for i in range(1, steps):
            x, y, z = (s + (e - s) * i / steps for s, e in zip(a, b))
            if self.arena.ground(x, y) >= z or any(
                p.base < z < p.top and p.contains(x, y) for p in self.arena.nearby(x, y, 0)
            ):
                return False
        return True

    @staticmethod
    def raised(pos, height):
        return *pos[:2], pos[2] + height

    def vision(self, mechs, squad):
        visible = {m.slot: [] for m in mechs}
        cfg = self.config["vision"]
        for i, a in enumerate(mechs):
            for b in mechs[i + 1:]:
                if squad and a.slot and b.slot:
                    continue
                d = distance(a.pos, b.pos)
                if d > cfg["range"]:
                    continue
                angle = math.atan2(b.pos[1] - a.pos[1], b.pos[0] - a.pos[0])
                half_fov = math.radians(cfg["fov_deg"] / 2)
                see_b = d <= cfg["near_range"] or angular_error(a.aim[0], angle) <= half_fov
                see_a = d <= cfg["near_range"] or angular_error(b.aim[0], angle + math.pi) <= half_fov
                if (see_a or see_b) and self.line_clear(self.raised(a.pos, self.mech["eye_height"]),
                                                        self.raised(b.pos, self.mech["eye_height"])):
                    if see_b:
                        visible[a.slot].append(b)
                    if see_a:
                        visible[b.slot].append(a)
        return visible

    def max_hp(self, mech):
        cfg = self.config["cubes"]
        return mech.stats["hp"] * (1 + min(mech.cubes, cfg["stat_cap"]) * cfg["hp_per_cube"])

    def observation(self, m, visible, cubes, zone, time, alive, shots, pickups):
        own = {"id": m.slot, "pos": m.pos, "vel": m.vel, "hp": m.hp, "max_hp": self.max_hp(m),
               "aim": m.aim, "grounded": m.grounded, "energy": m.energy, "stats": m.stats,
               "in_zone": distance(m.pos, zone["center"]) <= zone["radius"],
               "weapons": [{"kind": "gun", "stats": m.weapon_stats["gun"], "ammo": m.ammo,
                            "cooldown": max(0, m.ready - time), "reload": max(0, m.reload_at - time)},
                           {"kind": m.secondary, "stats": m.weapon_stats[m.secondary],
                            "charge": m.charge, "cooldown": max(0, m.secondary_ready - time)}
                           if m.secondary else None]}
        return {"type": "obs", "tick": round(time * self.config["match"]["tick_hz"]), "t": time,
                "alive": alive, "self": own, "zone": zone, "offer": {"choices": m.offers[0]} if m.offers else None, "crates": [],
                "pickups": [item for item in pickups.values() if distance(m.pos, item["pos"]) < 130],
                "projectiles": [{"id": i, "kind": shot.kind, "pos": shot.pos, "vel": shot.vel}
                                for i, shot in enumerate(shots) if shot.source is not m
                                and distance(m.pos, shot.pos) <= 90],
                "sounds": [], "beams": [], "drops": [], "events": m.events,
                "mechs": [{"id": a.slot, "kind": "mech", "pos": a.pos, "vel": a.vel, "hp": a.hp,
                           "max_hp": self.max_hp(a)} for a in visible],
                "cubes": [{"id": i, "pos": p} for i, p in cubes.items() if distance(m.pos, p) <= 130]}

    def house(self, m, target, shared, zone, now, active, profile):
        goal = zone["next_center"]
        if active and shared and distance(m.pos, zone["center"]) < zone["radius"] - 8:
            goal = shared
            if profile == "flank" and distance(m.pos, shared) > 35:
                dx, dy = unit(shared[0] - m.pos[0], shared[1] - m.pos[1])
                side = 1 if m.slot % 2 else -1
                goal = shared[0] - side * dy * 25, shared[1] + side * dx * 25
            elif profile == "zone":
                goal = tuple(.65 * a + .35 * b for a, b in zip(shared, zone["next_center"]))
        waypoint = m.navigator.waypoint(m.pos, goal, Navigator.shape(m.stats), zone, now, (), m.grounded, False)
        move = unit(waypoint[0] - m.pos[0], waypoint[1] - m.pos[1])
        look = target.pos if target else goal
        dx, dy = look[0] - m.pos[0], look[1] - m.pos[1]
        yaw = math.atan2(dy, dx)
        pitch = math.atan2(look[2] - m.pos[2] - 2.5, max(1, math.hypot(dx, dy))) if target else 0
        aim = [yaw, pitch]
        return {"move": move, "aim": aim, "boost": active and m.tier >= 2,
                "fire": [bool(target and active and angular_error(m.aim[0], yaw) < .20),
                         bool(target and active and angular_error(m.aim[0], yaw) < .15
                              and (m.secondary != "laser" or m.charge < m.weapon_stats["laser"]["charge"]))]}

    def move(self, m, action, time):
        dx, dy = action["move"]
        stats = m.stats
        shape = Navigator.shape(stats)
        before = m.pos
        if action.get("jump") and m.grounded:
            m.grounded = False
            m.vertical_speed = stats["jump_speed"]
        # Integrate jets and landings in short substeps even for coarse search runs.
        parts = max(1, math.ceil(self.step / .1)) if not m.grounded else 1
        dt = self.step / parts
        for _ in range(parts):
            drain = stats["boost_drain"] if m.grounded else stats["jet_drain"]
            boosting = action.get("boost") and m.energy >= drain * dt
            speed = stats["boost_speed"] if boosting and m.grounded else stats["walk_speed"]
            if m.charge > 0:
                speed *= .5
            if boosting:
                m.energy -= drain * dt
                m.last_boost = time
            elif time - m.last_boost >= stats["energy_regen_delay"]:
                m.energy = min(stats["energy"], m.energy + stats["energy_regen"] * dt)
            x, y, z = m.pos
            if not m.grounded:
                m.vertical_speed = min(stats["max_rise_speed"], m.vertical_speed +
                                       ((stats["jet_accel"] if boosting else 0) - stats["gravity"]) * dt)
                new_z = z + m.vertical_speed * dt
                landings = [h for h in self.arena.surfaces(x, y) if new_z <= h <= z + .02]
                if landings and m.vertical_speed <= 0:
                    new_z, m.vertical_speed, m.grounded = max(landings), 0, True
                if self.arena.clear(x, y, new_z, *shape[:2]):
                    m.pos = x, y, new_z
                else:
                    m.vertical_speed = min(0, m.vertical_speed)
            if dx or dy:
                for mx, my in ((dx, dy), (dx, 0), (0, dy)):
                    if not (mx or my):
                        continue
                    end = x + mx * speed * dt, y + my * speed * dt
                    if not m.grounded:
                        if self.arena.segment(m.pos, end, shape, airborne=True):
                            m.pos = (*end, m.pos[2])
                            break
                    else:
                        floors = sorted(self.arena.surfaces(*end), key=lambda h: abs(h - m.pos[2]))
                        goal = next(((*end, h) for h in floors if self.arena.segment(m.pos, (*end, h), shape)), None)
                        if goal is not None:
                            m.pos = goal
                            break
        if (dx or dy) and distance(m.pos, before) < .01:
            m.diagnostics["blocked_steps"] += 1
        m.vel = tuple((a - b) / self.step for a, b in zip(m.pos, before))
        move_angle = action.get("aim", m.aim)
        for index, rate in enumerate((stats["turn_rate"], stats["pitch_rate"])):
            change = math.remainder(move_angle[index] - m.aim[index], math.tau)
            max_turn = self.step * rate
            m.aim[index] = math.remainder(m.aim[index] + max(-max_turn, min(max_turn, change)), math.tau)

    def fire(self, source, action, rng, time):
        gun = source.weapon_stats["gun"]
        if source.reload_at and time >= source.reload_at:
            source.ammo, source.reload_at = gun["mag"], 0
        if source.reload_at:
            return None
        if not source.ammo or action.get("reload") and source.ammo < gun["mag"]:
            source.reload_at = time + gun["reload"]
            return None
        if time < source.ready or not action["fire"][0]:
            return None
        source.ammo -= 1
        source.ready = time + gun["cooldown"]
        source.diagnostics["shots"] += 1
        yaw, pitch = source.aim
        spread = math.radians(gun["spread_deg"]) * (1 if source.bot else 1.9 - .25 * source.tier)
        yaw, pitch = yaw + rng.gauss(0, spread), pitch + rng.gauss(0, spread)
        speed = gun["speed"]
        velocity = (speed * math.cos(pitch) * math.cos(yaw),
                    speed * math.cos(pitch) * math.sin(yaw), speed * math.sin(pitch))
        cfg = self.config["cubes"]
        damage = gun["damage"] * (1 + cfg["damage_per_cube"] * min(source.cubes, cfg["stat_cap"]))
        return Projectile(source, self.raised(source.pos, self.mech["muzzle_height"]),
                          velocity, damage, gun["range"] / speed)

    def apply_damage(self, source, target, damage, now, kind):
        if target.hp <= 0:
            return
        bearing = math.atan2(source.pos[1] - target.pos[1], source.pos[0] - target.pos[0])
        if angular_error(target.aim[0], bearing) > math.radians(target.stats["front_arc_deg"] / 2):
            damage *= target.stats["rear_mult"]
        damage *= max(0, 1 - target.stats["armor"])
        target.hp -= damage
        target.last_damage = source.last_damage = now
        target.diagnostics["combat_damage"] += damage
        target.diagnostics["combat_deaths"] += target.hp <= 0
        target.events.append({"type": "damaged", "amount": damage, "weapon": kind,
                              "bearing": bearing, "by": source.slot})

    def secondary_fire(self, source, enemies, trigger, now):
        kind = source.secondary
        if not kind or now < source.secondary_ready:
            return None
        stats = source.weapon_stats[kind]
        scale = 1 + self.config["cubes"]["damage_per_cube"] * min(source.cubes, self.config["cubes"]["stat_cap"])
        yaw, pitch = source.aim
        origin = self.raised(source.pos, source.stats["muzzle_height"])
        if kind == "laser":
            if trigger and source.charge < stats["charge"] + stats["hold_s"]:
                source.charge += self.step
                return None
            if not source.charge:
                return None
            fraction = max(stats["min_frac"], min(1, source.charge / stats["charge"]))
            source.charge = 0
        elif not trigger:
            return None
        elif kind == "lightning" and source.energy < stats["energy"]:
            return None
        source.secondary_ready = now + stats["cooldown"]
        source.diagnostics["shots"] += 1
        if kind == "mortar":
            speed = stats["speed"]
            v = (speed * math.cos(yaw) * math.cos(pitch), speed * math.sin(yaw) * math.cos(pitch),
                 speed * math.sin(pitch))
            return Projectile(source, origin, v, stats["damage"] * scale, stats["ttl"], kind)
        if kind == "lightning":
            source.energy -= stats["energy"]
        hit = []
        for target in sorted(enemies, key=lambda m: distance(m.pos, origin)):
            d = distance(origin, target.pos)
            if d > stats["range"] or target.hp <= 0:
                continue
            center = self.raised(target.pos, target.stats["height"] / 2)
            angle = math.atan2(center[1] - origin[1], center[0] - origin[0])
            elevation = math.atan2(center[2] - origin[2], max(d, .01))
            tolerance = math.radians(stats["cone_deg"]) if kind == "lightning" else math.atan2(target.stats["radius"], max(d, .01))
            if angular_error(yaw, angle) > tolerance or not self.line_clear(origin, center):
                continue
            if kind == "laser" and abs(pitch - elevation) > math.atan2(target.stats["height"] / 2, max(d, .01)):
                continue
            self.apply_damage(source, target, stats["damage"] * scale * (fraction if kind == "laser" else 1), now, kind)
            hit.append(target)
            if len(hit) >= (1 + stats["pierce"] if kind == "laser" else 1):
                break
        if hit:
            source.diagnostics["hits"] += 1
        if kind == "lightning" and hit:
            for jump in range(stats["chain"]):
                eligible = [m for m in enemies if m not in hit and m.hp > 0
                            and distance(m.pos, hit[-1].pos) <= stats["chain_range"]]
                if not eligible:
                    break
                target = min(eligible, key=lambda m: distance(m.pos, hit[-1].pos))
                self.apply_damage(source, target, stats["damage"] * scale * stats["chain_falloff"] ** (jump + 1), now, kind)
                hit.append(target)
        return None

    def blast(self, shot, center, mechs, squad, now):
        stats = shot.source.weapon_stats["mortar"]
        hit_enemy = False
        for m in mechs:
            if m.hp <= 0 or squad and shot.source.slot and m.slot and m is not shot.source:
                continue
            target = self.raised(m.pos, m.stats["height"] / 2)
            d = math.dist(center, target)
            if d > stats["splash_radius"] or not self.line_clear(center, target):
                continue
            fraction = 1 - (1 - stats["splash_edge"]) * d / stats["splash_radius"]
            damage = stats["splash"] * fraction * (self.mech["self_damage_mult"] if m is shot.source else 1)
            self.apply_damage(shot.source, m, damage, now, "mortar")
            m.energy = max(0, m.energy - stats["drain"] * fraction)
            hit_enemy |= m is not shot.source
        return hit_enemy

    def advance_shot(self, shot, mechs, previous, now, squad):
        dt = min(self.step, shot.remaining)
        vx, vy, vz = shot.vel
        x, y, z = shot.pos
        impacts = []
        for target in mechs:
            if target is shot.source or target.hp <= 0 or squad and target.slot and shot.source.slot:
                continue
            tx, ty, tz = previous[target.slot]
            dx, dy = vx - target.vel[0], vy - target.vel[1]
            relative_speed_sq = dx * dx + dy * dy
            if relative_speed_sq <= 0:
                continue
            tau = max(0, min(dt, ((tx - x) * dx + (ty - y) * dy) / relative_speed_sq))
            if (math.hypot(x - tx + dx * tau, y - ty + dy * tau) <= self.mech["radius"]
                    and tz <= z + (vz - target.vel[2]) * tau <= tz + self.mech["height"]):
                impacts.append((tau, target.slot, target))
        if impacts:
            tau, _, target = min(impacts)
            impact = x + vx * tau, y + vy * tau, z + vz * tau
            if self.line_clear(shot.pos, impact):
                self.apply_damage(shot.source, target, shot.damage, now, shot.kind)
                if shot.kind == "mortar":
                    self.blast(shot, impact, mechs, squad, now)
                shot.source.diagnostics["hits"] += 1
            return False
        end = x + vx * dt, y + vy * dt, z + vz * dt
        if shot.kind == "mortar":
            gravity = shot.source.weapon_stats["mortar"]["gravity"]
            end = end[0], end[1], end[2] - .5 * gravity * dt * dt
            shot.vel = vx, vy, vz - gravity * dt
        if not self.line_clear(shot.pos, end) or end[2] < self.arena.ground(*end[:2]):
            if shot.kind == "mortar":
                # Find the last open position on the swept arc chord.
                low, high = shot.pos, end
                for _ in range(6):
                    middle = tuple((a + b) / 2 for a, b in zip(low, high))
                    if self.line_clear(low, middle) and self.arena.clear(*middle, .01, .01) and middle[2] >= self.arena.ground(*middle[:2]):
                        low = middle
                    else:
                        high = middle
                shot.source.diagnostics["hits"] += self.blast(shot, low, mechs, squad, now)
            return False
        shot.pos, shot.remaining = end, shot.remaining - dt
        return shot.remaining > 0

    def upgrade(self, mech, choice):
        old_max = self.max_hp(mech)
        for effect in choice["effects"]:
            path = effect["path"].split(".")
            stats = mech.stats if path[0] == "mech" else mech.weapon_stats[path[1]]
            stats[path[-1]] = stats[path[-1]] * effect.get("mul", 1) + effect.get("add", 0)
        mech.hp += self.max_hp(mech) - old_max
        mech.upgrade_counts[choice["id"]] = mech.upgrade_counts.get(choice["id"], 0) + 1

    def match(self, candidate, policy_pool, seed, squad, profile="pursuit"):
        rng = random.Random(seed)
        cfg = self.config["match"]
        phases = list(self.zones(rng))
        center, radius = phases[0][:2]
        player = 0 if squad else rng.randrange(cfg["mechs"])
        mechs = []
        tiers = iter(TIERS)
        for slot in range(cfg["mechs"]):
            pos = self.open_point(rng, center, radius * cfg["spawn_zone_fraction"],
                                  [m.pos for m in mechs], cfg["spawn_min_separation"] * .75)
            controller = self.controller if slot == player else Bot
            bot = None if squad and slot != player else controller(
                self.hello, weights=candidate if slot == player else rng.choice(policy_pool), arena=self.arena)
            tier = next(tiers) if bot is None else 0
            mechs.append(Mech(slot, pos, self.mech["hp"], self.mech["energy"], self.gun["mag"],
                              [rng.uniform(-math.pi, math.pi), 0], bot, tier,
                              Navigator(self.arena) if bot is None else None))
            mechs[-1].stats = dict(self.mech)
            mechs[-1].weapon_stats = copy.deepcopy(self.config["weapons"])
        focal = mechs[player]
        cubes = {i: self.open_point(rng, center, radius * .75) for i in range(40)}
        shared, last_seen, phase_index = None, -100, 0
        pending = []
        shot_rng = {m.slot: random.Random(seed * 31 + m.slot) for m in mechs}
        next_cube = len(cubes)
        loot_rng = random.Random(seed + 230011)
        loot = self.config["loot"]
        pickups = {i: {"id": i, "kind": loot_rng.choices(list(loot["weights"]), weights=list(loot["weights"].values()))[0],
                       "pos": self.open_point(loot_rng, center, radius * .75)}
                   for i in range(loot["floor_count"])}
        for turn in range(math.ceil(self.duration / self.step)):
            now = turn * self.step
            if phase_index + 1 < len(phases) and now >= phases[phase_index][5]:
                phase_index += 1
            zone = self.zone_at(phases[phase_index], now)
            visible = self.vision(mechs, squad)
            if squad and any(focal in visible[m.slot] for m in mechs if m is not focal):
                shared, last_seen = focal.pos, now
            elif now - last_seen > self.config["vision"]["memory_s"]:
                shared = None
            if squad:
                active_count = min(15, 1 + int(now / 55) + (16 - len(mechs)) // 2 + phase_index)
                hunters = {m.slot for m in [m for m in mechs if m is not focal][:active_count]}
                if sum(focal in visible[s] for s in hunters) >= 2:
                    focal.diagnostics["pressure_s"] += self.step
            actions = {}
            previous = {m.slot: m.pos for m in mechs}
            for m in mechs:
                enemies = visible[m.slot]
                actions[m.slot] = (m.bot.act(self.observation(m, enemies, cubes, zone, now, len(mechs), pending, pickups))
                                   if m.bot else self.house(m, enemies[0] if enemies else None, shared,
                                                           zone, now, m.slot in hunters, profile))
                m.events = []
            for m in mechs:
                action = actions[m.slot]
                if m.offers:
                    choices = m.offers.pop(0)
                    self.upgrade(m, choices[action.get("pick", 0)])
                if m.secondary is None:
                    item = pickups.get(action.get("take"))
                    if m.bot is None:
                        item = next((p for p in pickups.values() if distance(m.pos,p["pos"]) <= loot["pickup_radius"]), None)
                    if item and distance(m.pos, item["pos"]) <= loot["pickup_radius"] and abs(m.pos[2] - item["pos"][2]) <= 4:
                        m.secondary = item["kind"]
                        m.secondary_ready = now + loot["equip_s"]
                        del pickups[item["id"]]
                enemies = [e for e in mechs if e is not m and (not squad or not m.slot or not e.slot)]
                projectile = self.secondary_fire(m, enemies, action["fire"][1], now)
                if projectile:
                    pending.append(projectile)
                shot = self.fire(m, actions[m.slot], shot_rng[m.slot], now)
                if shot:
                    pending.append(shot)
                self.move(m, actions[m.slot], now)
            pending = [shot for shot in pending if self.advance_shot(shot, mechs, previous, now, squad)]
            for m in mechs:
                if m.hp <= 0:
                    continue
                if distance(m.pos, zone["center"]) > zone["radius"]:
                    damage = zone["dps"] * self.step
                    m.hp -= damage
                    m.diagnostics["zone_damage"] += damage
                    m.diagnostics["zone_deaths"] += m.hp <= 0
                    m.last_damage = now
                elif now - m.last_damage > m.stats["regen_delay"]:
                    m.hp = min(self.max_hp(m), m.hp + m.stats["regen_per_s"] * self.step)
                for key, pos in list(cubes.items()):
                    if distance(m.pos, pos) <= self.config["cubes"]["pickup_radius"] and abs(m.pos[2] - pos[2]) <= 4:
                        old_max = self.max_hp(m)
                        m.cubes += 1
                        if m.cubes in self.config["cubes"]["upgrade_thresholds"]:
                            choices = [u for u in self.config["upgrades"]
                                       if m.upgrade_counts.get(u["id"], 0) < u["max"]
                                       and u.get("requires") in (None, "gun", m.secondary)]
                            if choices:
                                m.offers.append(loot_rng.sample(choices, min(3, len(choices))))
                        m.hp += self.max_hp(m) - old_max
                        del cubes[key]
            if focal.hp <= 0:
                return 16 - len(mechs), now, focal.diagnostics
            for eliminated in (m for m in mechs if m.hp <= 0):
                for _ in range(eliminated.cubes + self.config["cubes"]["death_drop_extra"]):
                    cubes[next_cube] = eliminated.pos
                    next_cube += 1
            mechs = [m for m in mechs if m.hp > 0]
            if len(mechs) == 1:
                return 15, now, focal.diagnostics
        # A shortened diagnostic match treats other living bots as still ahead.
        focal.diagnostics["timeouts"] += 1
        return 16 - len(mechs), self.duration, focal.diagnostics

    def evaluate(self, candidate, pool, seeds, formats):
        metrics = {}
        for mode in formats:
            results = [self.match(candidate, pool, seed, mode == "squad", PROFILES[i % len(PROFILES)])
                       for i, seed in enumerate(seeds)]
            metrics[mode] = {"placement": mean(r[0] for r in results), "survival_s": mean(r[1] for r in results),
                             **{k: mean(r[2][k] for r in results) for k in REASONS},
                             "misses": mean(r[2]["shots"] - r[2]["hits"] for r in results),
                             "runs": [{"seed": seed,
                                       "profile": PROFILES[i % len(PROFILES)] if mode == "squad" else "self-play",
                                       "placement": r[0], "survival_s": r[1], **r[2]}
                                      for i, (seed, r) in enumerate(zip(seeds, results))]}
        return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, default=Path("models/trained.json"))
    parser.add_argument("--policy", action="append")
    parser.add_argument("--seeds", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261121)
    parser.add_argument("--step", type=float, default=.5)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--mutations", type=int, default=0)
    parser.add_argument("--format", action="append", choices=("squad", "free_for_all"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seeds < 1 or args.mutations < 0:
        parser.error("Use at least one seed and a nonnegative mutation count.")
    data = json.loads(args.checkpoint.read_text())
    base = data["champion"]
    policies = data["policies"]
    pool = [*STARTERS.values(), *(policies[name] for name in STARTERS)]
    candidates = {name: policies[name] for name in dict.fromkeys([base, *(args.policy or STARTERS)])}
    rng = random.Random(args.seed)
    search = rng.sample(range(7_000_000, 8_000_000), max(6, args.seeds))
    seeds = rng.sample(range(8_000_000, 9_000_000), args.seeds)
    if args.mutations:
        parent = candidates[base]
        trials = []
        for _ in range(args.mutations):
            mode = rng.randrange(len(parent))
            trials.append([row if i != mode else [round(w + rng.gauss(0, .18), 5) for w in row]
                           for i, row in enumerate(parent)])
        for i, weights in enumerate(sorted(trials, key=lambda w: score(evaluate(w, pool, search)),
                                           reverse=True)[:2]):
            candidates[f"mutation_{i + 1}"] = weights
    simulator = Simulator(args.kit, args.step, args.duration)
    formats = tuple(dict.fromkeys(args.format or ("squad", "free_for_all")))
    results = {}
    for name, weights in candidates.items():
        results[name] = simulator.evaluate(weights, pool, seeds, formats)
        summary = {mode: {k: v for k, v in metrics.items() if k != "runs"}
                   for mode, metrics in results[name].items()}
        print(f"{name}: {summary}", flush=True)
    quick = {name: evaluate(weights, pool, seeds) for name, weights in candidates.items()}
    baseline = results[base]
    eligible = [name for name, metrics in results.items()
                if len(formats) == 2 and metrics["squad"]["placement"] > baseline["squad"]["placement"]
                and metrics["free_for_all"]["placement"] >= baseline["free_for_all"]["placement"]
                and score(quick[name]) > score(quick[base]) + .10]
    report = {"format": "map-backed approximation", "base": base, "seeds": seeds,
              "profiles": list(PROFILES), "duration_s": simulator.duration, "decision_step_s": args.step,
              "arena": results, "quick": quick, "eligible": eligible, "policies": candidates}
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
