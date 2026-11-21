"""Check geometry cases and benchmark a blocked route on the official arena."""
import argparse
import math
from statistics import median
from time import perf_counter

from navigation import Arena, Navigator, Prism


def run(arena_dir):
    arena = Arena(arena_dir)
    shape = Navigator.shape({"radius": 2.5, "height": 9, "step_up": 1.2,
                             "max_slope_deg": 50})
    bridge = Prism({"ring": [0, 0, 20, 0, 20, 20, 0, 20],
                    "holes": [[6, 6, 14, 6, 14, 14, 6, 14]], "base": 12, "top": 14})
    assert not bridge.overlaps(10, 10, 2.5)
    assert bridge.overlaps(7, 10, 2.5)
    arena.buckets[(0, 0)] = [bridge]
    assert arena.clear(4, 4, 0, 2.5, 9)  # Under the bridge.
    assert not arena.clear(4, 4, 4, 2.5, 9)  # Head touches its underside.
    assert arena.clear(4, 4, 14, 2.5, 9)  # Standing on the deck.

    arena.buckets.clear()
    arena.ground = lambda x, y: 0.5 * x
    assert arena.segment((1, 20, .5), (5, 20, 2.5), shape)
    arena.ground = lambda x, y: 1.5 * x
    assert not arena.segment((1, 20, 1.5), (5, 20, 7.5), shape)
    assert not arena.clear(arena.bounds["minX"] + 1, 20, 0, 2.5, 9)

    arena = Arena(arena_dir)
    start = (-408.5, -265.7, arena.ground(-408.5, -265.7))
    end = (-371.3, -265.7, arena.ground(-371.3, -265.7))
    assert not arena.segment(start, end, shape)
    zone = {"center": (0, 0), "next_center": (0, 0), "radius": 1000, "next_radius": 1000,
            "shrink_start": 20, "shrink_end": 30, "dps": 1}
    times = []
    for _ in range(12):
        navigator = Navigator(arena)
        for tick in range(60):
            begin = perf_counter()
            navigator.waypoint(start, end, shape, zone, tick / 10, (), True, False)
            times.append(1000 * (perf_counter() - begin))
            if navigator.path and math.dist(navigator.path[-1], end) < 10:
                break
        else:
            raise AssertionError("No route around the official map obstacle")
        route = [start, *navigator.path, end]
        assert all(arena.segment(a, b, shape) for a, b in zip(route, route[1:]))
    assert navigator.risk(end, {**zone, "radius": 20, "next_radius": 10}, (), 25, 0) > 0
    print(f"Direct route blocked at {math.dist(start, end):.1f} m; "
          f"A* found a traversable {sum(math.dist(a, b) for a, b in zip(route, route[1:])):.1f} m route.")
    print(f"A* planning slices: median {median(times):.2f} ms, max {max(times):.2f} ms "
          "(first route cold, subsequent routes use cached map links).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arena")
    run(parser.parse_args().arena)
