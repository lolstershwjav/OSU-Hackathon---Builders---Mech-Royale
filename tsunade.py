"""Experimental low-health disengagement for crowded game fights."""
import math

from bot import DIRECTIONS
from navigation import Navigator


def escape_plan(obs, arena, mech_config, goal=None):
    me, zone = obs["self"], obs["zone"]
    if obs["alive"] < 3 or not me["grounded"] or not me["in_zone"]:
        return None
    pos = me["pos"]
    threats = [tuple(e["pos"][:2]) for e in obs["mechs"] if e["kind"] == "mech"
               and math.dist(pos[:2], e["pos"][:2]) <= 90]
    if goal is None:
        if me["hp"] > .35 * me["max_hp"] or len(set(threats)) < 2 or not any(
                e["type"] == "damaged" and isinstance(e.get("by"), int) and 0 <= e["by"] < 16
                for e in obs["events"]):
            return None
    shape = Navigator.shape({**mech_config, **me["stats"]})
    center = zone["next_center"][:2]

    def inside(point):
        return (math.dist(point[:2], center) < zone["next_radius"] - 5
                and math.dist(point[:2], zone["center"][:2]) < zone["radius"] - 3)

    if goal is None:
        for reach in (8, 16):
            for dx, dy in DIRECTIONS[::2]:
                x, y = pos[0] + reach * dx, pos[1] + reach * dy
                cover = x, y, arena.ground(x, y)
                if (inside(cover) and all(arena.cover(cover, threat) for threat in threats)
                        and arena.segment(pos, cover, shape)):
                    return None
        isolated = max(threats, key=lambda p: (min(math.dist(p, t) for t in threats if t != p)
                                              if any(t != p for t in threats) else 0,
                                              math.dist(p, pos[:2])))
        others = [point for point in threats if point != isolated]
        crowd = tuple(sum(point[i] for point in others) / len(others) for i in (0, 1)) if others else pos[:2]
        dx, dy = isolated[0] - crowd[0], isolated[1] - crowd[1]
        length = math.hypot(dx, dy)
        if not length:
            return None
        goal = isolated[0] + 15 * dx / length, isolated[1] + 15 * dy / length
    if math.dist(pos[:2], goal) <= 5 or not inside(goal):
        return None
    candidates = []
    for ux, uy in DIRECTIONS:
        endpoint = pos[0] + 4 * ux, pos[1] + 4 * uy
        if not (ux or uy) or not inside(endpoint) or not arena.segment(pos, endpoint, shape):
            continue
        clearance = min((math.dist(endpoint, t) for t in threats), default=0)
        if threats and clearance < min(12, min(math.dist(pos[:2], t) for t in threats)):
            continue
        value = math.dist(pos[:2], goal) - math.dist(endpoint, goal) + .5 * clearance
        candidates.append((value, [ux, uy]))
    if not candidates:
        return None
    return goal, {"move": max(candidates, key=lambda entry: entry[0])[1], "boost": me["energy"] > 25,
            "jump": False, "fire": [False, False]}


def escape_action(obs, arena, mech_config):
    plan = escape_plan(obs, arena, mech_config)
    return plan[1] if plan else None
