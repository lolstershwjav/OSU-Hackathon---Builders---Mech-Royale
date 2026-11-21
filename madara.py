"""Experimental last-duel controller for the Mech Royale simulator."""
import math

from bot import Bot, DIRECTIONS
from navigation import Navigator


def duel_action(obs, arena, mech_config):
    if obs["alive"] != 2:
        return None
    me, zone = obs["self"], obs["zone"]
    enemies = [enemy for enemy in obs["mechs"] if enemy["kind"] == "mech"]
    if len(enemies) != 1 or not me["grounded"] or not me["in_zone"]:
        return None
    enemy, pos = enemies[0], me["pos"]
    target = enemy["pos"]
    if arena.cover(pos, target[:2]):
        return None
    center, radius = zone["next_center"][:2], zone["next_radius"]

    def safe(point):
        return math.dist(point[:2], center) < max(0, radius - 3)

    if not safe(pos):
        return None
    if any(safe(item["pos"]) and math.dist(pos[:2], item["pos"][:2]) <= 45
           for key in ("cubes", "pickups", "drops", "crates")
           for item in obs.get(key, ())):
        return None
    shape = Navigator.shape({**mech_config, **me["stats"]})
    for reach in (8, 16):
        for dx, dy in DIRECTIONS[::2]:
            x, y = pos[0] + reach * dx, pos[1] + reach * dy
            for z in arena.surfaces(x, y):
                point = x, y, z
                if safe(point) and arena.clear(*point, *shape[:2]) and (
                        3 <= z - pos[2] <= 12
                        or arena.cover(point, target[:2]) and arena.segment(pos, point, shape)):
                    return None
    distance = math.dist(pos[:2], target[:2])
    if distance < .01:
        return None
    close_weapon = any(w and w["kind"] == "lightning" for w in me["weapons"])
    preferred = 30 if close_weapon else 65
    dx, dy = (target[0] - pos[0]) / distance, (target[1] - pos[1]) / distance
    side = 1 if (obs["tick"] // 30) % 2 else -1
    advance = min(1, max(-.7, (distance - preferred) / 25))
    vx, vy = advance * dx - .55 * dy * side, advance * dy + .55 * dx * side
    scale = max(1, math.hypot(vx, vy))
    endpoint = pos[0] + 3 * vx / scale, pos[1] + 3 * vy / scale
    move = [vx / scale, vy / scale] if safe(endpoint) and arena.segment(pos, endpoint, shape) else [0, 0]
    action = {"move": move, "boost": False, "jump": False, "fire": [False, False]}
    Bot.aim(action, me, enemy, True)
    gun = me["weapons"][0]
    if gun and gun["kind"] == "gun" and not gun.get("ammo") and not gun.get("reload"):
        action["reload"] = True
    return action
