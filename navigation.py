"""On-demand walkable surfaces and bounded A* for the official arena."""
from array import array
from functools import lru_cache
import heapq
import json
import math
from pathlib import Path
import sys
from time import perf_counter

CELL = 6
BUCKET = 24
OFFSETS = ((1, 0), (-1, 0), (0, 1), (0, -1),
           (1, 1), (1, -1), (-1, 1), (-1, -1))


def edges(ring):
    points = list(zip(ring[::2], ring[1::2]))
    return tuple(zip(points[-1:] + points[:-1], points))


def contains(ring, x, y):
    inside = False
    for (ax, ay), (bx, by) in ring:
        if (ay > y) != (by > y) and x < ax + (bx - ax) * (y - ay) / (by - ay):
            inside = not inside
    return inside


def edge_distance_sq(a, b, x, y):
    dx, dy = b[0] - a[0], b[1] - a[1]
    length_sq = dx * dx + dy * dy
    t = max(0, min(1, ((x - a[0]) * dx + (y - a[1]) * dy) / length_sq)) if length_sq else 0
    return (x - a[0] - t * dx) ** 2 + (y - a[1] - t * dy) ** 2


class Prism:
    def __init__(self, data):
        ring = data["ring"]
        self.outer = edges(ring)
        self.holes = tuple(edges(hole) for hole in data["holes"])
        self.rings = (self.outer, *self.holes)
        self.bounds = min(ring[::2]), min(ring[1::2]), max(ring[::2]), max(ring[1::2])
        self.base, self.top = data["base"], data["top"]

    def contains(self, x, y):
        return contains(self.outer, x, y) and not any(contains(h, x, y) for h in self.holes)

    def overlaps(self, x, y, radius):
        x0, y0, x1, y1 = self.bounds
        if not (x0 - radius <= x <= x1 + radius and y0 - radius <= y <= y1 + radius):
            return False
        return self.contains(x, y) or any(
            edge_distance_sq(a, b, x, y) < radius * radius
            for ring in self.rings for a, b in ring
        )


class Arena:
    def __init__(self, directory):
        root = Path(directory) / "v2"
        world = json.loads((root / "world.json").read_text(encoding="utf-8"))
        self.bounds = world["bounds"]
        self.grid = world["terrain"]
        self.samples = array("h")
        self.samples.frombytes((root / self.grid["file"]).read_bytes())
        if sys.byteorder != "little":
            self.samples.byteswap()
        if len(self.samples) != self.grid["nx"] * self.grid["ny"]:
            raise ValueError("Unexpected arena terrain size")
        self.buckets = {}
        self.prisms = []
        for data in world["prisms"]:
            prism = Prism(data)
            self.prisms.append(prism)
            x0, y0, x1, y1 = prism.bounds
            for col in range(math.floor(x0 / BUCKET), math.floor(x1 / BUCKET) + 1):
                for row in range(math.floor(y0 / BUCKET), math.floor(y1 / BUCKET) + 1):
                    self.buckets.setdefault((col, row), []).append(prism)

    def ground(self, x, y):
        grid = self.grid
        u = min(grid["nx"] - 1, max(0, (x - grid["minX"]) / grid["step"]))
        v = min(grid["ny"] - 1, max(0, (y - grid["minY"]) / grid["step"]))
        i, j = min(int(u), grid["nx"] - 2), min(int(v), grid["ny"] - 2)
        t, s = u - i, v - j
        row0 = j * grid["nx"] + i
        row1 = row0 + grid["nx"]
        return grid["scale"] * ((1 - s) * ((1 - t) * self.samples[row0] + t * self.samples[row0 + 1])
                                + s * ((1 - t) * self.samples[row1] + t * self.samples[row1 + 1]))

    def nearby(self, x, y, radius):
        if radius > BUCKET:
            return self.prisms
        result = set()
        for col in range(math.floor((x - radius) / BUCKET), math.floor((x + radius) / BUCKET) + 1):
            for row in range(math.floor((y - radius) / BUCKET), math.floor((y + radius) / BUCKET) + 1):
                result.update(self.buckets.get((col, row), ()))
        return result

    def clear(self, x, y, z, radius, height):
        b = self.bounds
        if not (b["minX"] + radius <= x <= b["maxX"] - radius
                and b["minY"] + radius <= y <= b["maxY"] - radius):
            return False
        return not any(p.base < z + height and p.top > z + .06 and p.overlaps(x, y, radius)
                       for p in self.nearby(x, y, radius))

    def cover(self, point, threat):
        """Count nearby walls that intercept the direction to a visible threat."""
        x, y, z = point
        dx, dy = threat[0] - x, threat[1] - y
        length = math.hypot(dx, dy)
        if length < 1:
            return False
        for offset in (4, 8, 12):
            if offset >= length:
                break
            px, py = x + dx * offset / length, y + dy * offset / length
            if any(p.base < z + 5 < p.top and p.contains(px, py)
                   for p in self.nearby(px, py, 0)):
                return True
        return False

    def surfaces(self, x, y):
        ground = self.ground(x, y)
        tops = {p.top for p in self.nearby(x, y, 0) if p.top >= ground and p.contains(x, y)}
        return sorted({ground, *tops})

    @lru_cache(maxsize=24_000)
    def cell(self, col, row, radius, height):
        x, y = col * CELL, row * CELL
        return tuple((x, y, z) for z in self.surfaces(x, y) if self.clear(x, y, z, radius, height))

    def segment(self, start, end, shape, stride=1.5, airborne=False):
        radius, height, step, slope = shape
        x0, y0, z = start
        dx, dy = end[0] - x0, end[1] - y0
        length = math.hypot(dx, dy)
        count = max(1, math.ceil(length / stride))
        limit = max(.15, min(step, slope * length / count))
        for i in range(1, count + 1):
            x, y = x0 + dx * i / count, y0 + dy * i / count
            if airborne:
                if self.ground(x, y) > z + step or not self.clear(x, y, z, radius, height):
                    return False
                continue
            floors = [floor for floor in self.surfaces(x, y) if abs(floor - z) <= limit + .02]
            floor = next((floor for floor in sorted(floors, key=lambda h: abs(h - z))
                          if self.clear(x, y, floor, radius, height)), None)
            if floor is None:
                return False
            z = floor
        return airborne or len(end) == 2 or abs(z - end[2]) < .08

    @lru_cache(maxsize=36_000)
    def links(self, node, shape):
        col, row = round(node[0] / CELL), round(node[1] / CELL)
        radius, height, _, _ = shape
        return tuple(target for dc, dr in OFFSETS
                     for target in self.cell(col + dc, row + dr, radius, height)
                     if self.segment(node, target, shape))

    def nearest(self, point, shape, connect):
        col, row = round(point[0] / CELL), round(point[1] / CELL)
        radius, height, _, _ = shape
        candidates = [node for dc in (-1, 0, 1) for dr in (-1, 0, 1)
                      for node in self.cell(col + dc, row + dr, radius, height)]
        for node in sorted(candidates, key=lambda n: math.dist(n, point)):
            if not connect or self.segment(point, node, shape):
                return node
        return None


class Navigator:
    def __init__(self, arena):
        self.arena = arena
        self.path = []
        self.key = None
        self.search = None
        self.retry = 0.0
        self.threat_key = ()

    @staticmethod
    def shape(stats):
        # Round up the cylinder dimensions so nearby upgrades can share safe cache entries.
        return (math.ceil(stats["radius"] * 4) / 4, math.ceil(stats["height"] * 4) / 4,
                stats["step_up"], math.tan(math.radians(stats["max_slope_deg"])))

    def risk(self, node, zone, threats, arrival, now):
        shrink_start = max(now, zone.get("shrink_start", now))
        progress = min(1, max(0, now + arrival - shrink_start) /
                       max(.1, zone["shrink_end"] - shrink_start))
        center = tuple(a + (b - a) * progress for a, b in zip(zone["center"], zone["next_center"]))
        radius = zone["radius"] + (zone["next_radius"] - zone["radius"]) * progress
        outside = max(0, math.hypot(node[0] - center[0], node[1] - center[1]) - radius + 4)
        pressure = 0.0
        for threat in threats:
            exposure = max(0, 1 - math.hypot(node[0] - threat[0], node[1] - threat[1]) / 48)
            if exposure:
                pressure += exposure * (.35 if self.arena.cover(node, threat) else 1)
        return min(6, outside * (0.035 + zone["dps"] * .002)) + pressure * 1.5

    def waypoint(self, position, goal, shape, zone, now, threats, grounded, stuck):
        if not grounded or math.hypot(goal[0] - position[0], goal[1] - position[1]) < 3:
            return goal
        target = (*goal[:2], goal[2] if len(goal) > 2 else self.arena.ground(*goal[:2]))
        key = (tuple(round(v / 18) for v in target[:2]), round(target[2] / 5), shape,
               tuple(round(v / 18) for v in zone["center"]), round(zone["radius"] / 18),
               tuple(round(v / 18) for v in zone["next_center"]), round(zone["next_radius"] / 18))
        threat_key = tuple((round(x / 24), round(y / 24)) for x, y in threats)
        if key != self.key or (now >= self.retry and (stuck or threat_key != self.threat_key)):
            self.key, self.path, self.search, self.retry = key, [], None, now
            self.threat_key = threat_key
        while self.path and math.dist(position, self.path[0]) < 4:
            self.path.pop(0)
        # Skip only nearby points reachable by a swept segment with no extra exposure.
        if self.path:
            first = self.path[0]
            for i in range(min(3, len(self.path) - 1), -1, -1):
                point = self.path[i]
                midpoint = tuple((a + b) / 2 for a, b in zip(position, point))
                if (math.dist(position, point) <= 17
                        and self.risk(midpoint, zone, threats, 1, now)
                        <= self.risk(first, zone, threats, 1, now) + .2
                        and self.arena.segment(position, point, shape)):
                    del self.path[:i]
                    return point
            self.path.clear()
            self.search = None
        if self.search is None and now < self.retry:
            return goal
        if self.search is None:
            direct = math.dist(position, target)
            center_risk = self.risk(tuple((a + b) / 2 for a, b in zip(position, target)),
                                    zone, threats, direct / 12, now)
            if direct <= 23 and center_risk < .25 and self.arena.segment(position, target, shape):
                return goal
            start = self.arena.nearest(position, shape, connect=True)
            end = self.arena.nearest(target, shape, connect=False)
            if start is None or end is None or start == end:
                self.retry = now + .8
                return goal
            self.search = (start, end, [(math.dist(start, end), 0.0, start)],
                           {start: (0.0, 0.0)}, {}, start, zone, threats, now)
            self.retry = now + .8
        start, end, queue, costs, parent, best, route_zone, route_threats, route_time = self.search
        fixed_nodes = getattr(self.arena, "simulation_nodes", None)
        deadline = perf_counter() + .0025 if fixed_nodes is None else float("inf")
        for _ in range(fixed_nodes or 72):
            if not queue or perf_counter() >= deadline or len(costs) >= 3800:
                break
            _, cost, node = heapq.heappop(queue)
            if cost != costs[node][0]:
                continue
            if math.dist(node, end) < math.dist(best, end):
                best = node
            if node == end:
                break
            for neighbor in self.arena.links(node, shape):
                length = math.dist(node, neighbor)
                traveled = costs[node][1] + length
                weight = self.risk(neighbor, route_zone, route_threats, traveled / 12, route_time)
                candidate = cost + length * (1 + weight)
                if neighbor not in costs or candidate < costs[neighbor][0]:
                    costs[neighbor] = candidate, traveled
                    parent[neighbor] = node
                    heapq.heappush(queue, (candidate + math.dist(neighbor, end), candidate, neighbor))
        self.search = (start, end, queue, costs, parent, best, route_zone, route_threats, route_time)
        if best == end or not queue or len(costs) >= 3800:
            nodes = []
            while best != start:
                nodes.append(best)
                best = parent[best]
            self.path = list(reversed(nodes))
            self.search = None
            if self.path and self.arena.segment(position, self.path[0], shape):
                return self.path[0]
        # Keep the origin of the search stable while it spans successive observations.
        return start if queue and math.dist(position, start) < 9 else goal
