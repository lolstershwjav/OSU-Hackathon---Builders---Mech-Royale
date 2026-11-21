"""Itachi: Naruto with an evidence-based, reversible FFA handoff.

What changed relative to Naruto (bob_mix.Bob):
  1. Detector needs DEATHS unexplained deaths AND they must come from at least
     BURSTS distinct observations. One blast/zone/NPC event that removes several
     mechs in a single tick no longer flips the format by itself.
  2. The handoff is REVERSIBLE. Naruto calls self.close() (kills Sasuke forever),
     so one false positive in squad permanently downgrades the bot. Itachi only
     stops sending observations to Sasuke; if the evidence was weak and no new
     unexplained deaths appear for REVERT_AFTER seconds, Sasuke takes over again.
     Strong evidence (LOCK_DEATHS) kills the child process for good, like Naruto.
  3. Optional WARM mode keeps Sakura's memory (damage, zone, loot knowledge) fresh
     while Sasuke drives, so a handoff doesn't start Sakura from a blank slate.
     Off by default: it is an ablation to test, not a proven win.

  4. Optional RETREAT mode (Survival + Positioning): when incoming damage or enemy
     pressure is high, walk to cover INSIDE the next safe zone and hold there until
     things calm down. Triggers earlier and on more evidence than Tsunade's
     escape_plan (35% hp + 2 visible enemies), and also reacts to unseen shooters.

Every knob is a class attribute (or ITACHI_* env var) so the harness can ablate it.
"""
import math
import os
from collections import deque
from time import monotonic

import mechfight
from bob_mix import Bob
from bot import DIRECTIONS
from navigation import Navigator


def _env(name, default, cast):
    try:
        return cast(os.environ.get(name, default))
    except ValueError:
        return cast(default)


class FfaDetector:
    """Pure-Python format detector (no I/O) so it can be unit tested offline.

    In squad practice the 15 house bots are teammates, so early mech deaths that
    we did not cause are evidence of a free-for-all.
    """

    def __init__(self, deaths=3, bursts=2, window=90.0, revert_after=45.0,
                 lock_deaths=5, allow_revert=True):
        self.deaths, self.bursts, self.window = deaths, bursts, window
        self.revert_after, self.lock_deaths = revert_after, lock_deaths
        self.allow_revert = allow_revert
        self.previous_alive = None
        self.death_times = []        # one entry per unexplained death, inside the window
        self.last_death_t = None     # last unexplained death, any time
        self.total = 0
        self.ffa = False
        self.locked = False
        self.switch_t = None
        self.reverted = 0

    def update(self, obs):
        alive, t = obs["alive"], obs["t"]
        if self.previous_alive is not None:
            kills = sum(e["type"] == "kill" for e in obs["events"])
            lost = max(0, self.previous_alive - alive - kills)
            if lost:
                self.last_death_t = t
                self.total += lost
                if t <= self.window:
                    self.death_times.extend([t] * lost)
        self.previous_alive = alive

        enough = (len(self.death_times) >= self.deaths
                  and len(set(self.death_times)) >= self.bursts)
        if enough and not self.ffa:
            self.ffa, self.switch_t = True, t
        if self.total >= self.lock_deaths or not self.allow_revert:
            self.locked = self.locked or self.ffa
        if (self.ffa and not self.locked and self.last_death_t is not None
                and t - self.last_death_t >= self.revert_after):
            self.ffa, self.reverted = False, self.reverted + 1
            # forget old evidence so a revert needs fresh deaths to flip again
            self.death_times = [x for x in self.death_times if t - x < self.window]
            self.death_times.clear()
        return "ffa" if self.ffa else "practice"


class PressureRetreat:
    """Retreat to cover inside the next zone when taking sustained damage.

    Trigger (any): damage taken in the last WINDOW s >= BURST of max hp; or two or
    more different attackers while below LOW_HP; or hp <= 35% with recent damage.
    Once triggered it commits to a cover point for up to HOLD s (no flip-flopping),
    then re-evaluates. Never runs in the final duel (alive < 3), when airborne, or
    outside the zone (those cases are left to Sasuke / Madara).
    """

    def __init__(self, window=3.0, burst=0.25, low_hp=0.6, hold=4.0, retry=1.5):
        self.window, self.burst, self.low_hp = window, burst, low_hp
        self.hold, self.retry = hold, retry
        self.hits = deque()          # (t, amount, attacker id, bearing)
        self.goal = None
        self.until = 0.0
        self.cool = 0.0
        self.activations = 0
        self.ticks = 0

    def observe(self, obs):
        t = obs["t"]
        for e in obs["events"]:
            if e["type"] == "damaged" and e.get("weapon") != "zone":
                self.hits.append((t, e.get("amount", 0.0), e.get("by"), e.get("bearing")))
        while self.hits and t - self.hits[0][0] > self.window:
            self.hits.popleft()

    def pressured(self, me):
        damage = sum(h[1] for h in self.hits)
        attackers = {h[2] for h in self.hits if h[2] is not None}
        mh = me["max_hp"]
        return (damage >= self.burst * mh
                or (len(attackers) >= 2 and me["hp"] < self.low_hp * mh)
                or (me["hp"] <= 0.35 * mh and damage > 0))

    def threats(self, obs, pos):
        seen = [tuple(e["pos"][:2]) for e in obs["mechs"]
                if e["kind"] == "mech" and math.dist(pos[:2], e["pos"][:2]) <= 90]
        if seen:
            return seen
        # unseen shooter: project the damage bearing out 40 m (assumes x=cos, y=sin)
        return [(pos[0] + 40 * math.cos(h[3]), pos[1] + 40 * math.sin(h[3]))
                for h in self.hits if h[3] is not None][-2:]

    def plan(self, obs, arena, mech_config):
        me, zone, t = obs["self"], obs["zone"], obs["t"]
        pos = me["pos"]
        if obs["alive"] < 3 or not me["grounded"] or not me["in_zone"]:
            self.goal = None
            return None
        threats = self.threats(obs, pos)
        active = self.goal is not None and t < self.until
        if not active:
            self.goal = None
            if t < self.cool or not threats or not self.pressured(me):
                return None
        shape = Navigator.shape({**mech_config, **me["stats"]})
        center, radius = zone["next_center"][:2], zone["next_radius"]

        def inside(p):
            return (math.dist(p[:2], center) < radius - 5
                    and math.dist(p[:2], zone["center"][:2]) < zone["radius"] - 3)

        if self.goal is None:
            self.goal = self.find_cover(pos, threats, arena, shape, inside)
            if self.goal is None:
                self.cool = t + self.retry
                return None
            self.until = t + self.hold
            self.activations += 1
        goal = self.goal
        self.ticks += 1
        if math.dist(pos[:2], goal[:2]) <= 3:                 # arrived: hold in cover
            calm = not self.hits and me["hp"] >= 0.8 * me["max_hp"]
            if calm or not threats:
                self.goal, self.cool = None, t + self.retry
                return None
            return {"move": [0, 0], "boost": False, "jump": False, "fire": [False, False]}
        best = None
        for ux, uy in DIRECTIONS:
            end = (pos[0] + 4 * ux, pos[1] + 4 * uy)
            if not (ux or uy) or not inside(end) or not arena.segment(pos, end, shape):
                continue
            clearance = min(math.dist(end, th) for th in threats)
            if clearance < min(12, min(math.dist(pos[:2], th) for th in threats)):
                continue
            value = math.dist(pos[:2], goal[:2]) - math.dist(end, goal[:2]) + 0.5 * clearance
            if best is None or value > best[0]:
                best = (value, [ux, uy])
        if best is None:
            self.goal, self.cool = None, t + self.retry
            return None
        return {"move": best[1], "boost": me["energy"] > 25, "jump": False,
                "fire": [False, False]}

    @staticmethod
    def find_cover(pos, threats, arena, shape, inside):
        for reach in (8, 16, 24):
            options = []
            for ux, uy in DIRECTIONS[::2]:
                if not (ux or uy):
                    continue
                x, y = pos[0] + reach * ux, pos[1] + reach * uy
                point = (x, y, arena.ground(x, y))
                if (inside(point) and all(arena.cover(point, th) for th in threats)
                        and arena.segment(pos, point, shape)):
                    options.append((min(math.dist(point[:2], th) for th in threats), point))
            if options:
                return max(options)[1]
        return None


class Itachi(Bob):
    name = "Itachi"

    DEATHS = _env("ITACHI_DEATHS", "3", int)
    BURSTS = _env("ITACHI_BURSTS", "2", int)
    WINDOW = _env("ITACHI_WINDOW", "90", float)
    REVERT_AFTER = _env("ITACHI_REVERT_AFTER", "45", float)
    LOCK_DEATHS = _env("ITACHI_LOCK_DEATHS", "5", int)
    REVERT = _env("ITACHI_REVERT", "1", int) == 1
    WARM = _env("ITACHI_WARM", "0", int) == 1
    RETREAT = _env("ITACHI_RETREAT", "0", int) == 1
    SASUKE_TIMEOUT = 0.022
    WARM_BUDGET = 0.012   # only warm Sakura if Sasuke answered faster than this

    def __init__(self, hello):
        super().__init__(hello)
        self.detector = FfaDetector(self.DEATHS, self.BURSTS, self.WINDOW,
                                    self.REVERT_AFTER, self.LOCK_DEATHS, self.REVERT)
        self.fallbacks = 0
        self.retreat = PressureRetreat()

    def report(self):
        d = self.detector
        return {"switch_t": d.switch_t, "reverted": d.reverted, "locked": d.locked,
                "unexplained": d.total, "fallbacks": self.fallbacks,
                "retreats": self.retreat.activations, "retreat_ticks": self.retreat.ticks}

    def base_action(self, obs):
        mode = self.detector.update(obs)
        if self.detector.locked:
            self.close()                      # strong evidence: free the child for good
        if mode == "practice" and self.child.poll() is None:
            started = monotonic()
            reply = self.ask(obs, self.SASUKE_TIMEOUT,
                             lambda action: action.get("tick") == obs["tick"])
            if reply is not None:
                if self.WARM and monotonic() - started < self.WARM_BUDGET:
                    self.sakura.act(obs)      # keep Sakura's memory current; discard output
                return reply
            self.fallbacks += 1
        return self.sakura.act(obs)

    def act(self, obs):
        action = super().act(obs)
        if not self.RETREAT or self.arena is None:
            return action
        self.retreat.observe(obs)
        plan = self.retreat.plan(obs, self.arena, self.mech_config)
        return {**action, **plan} if plan else action


if __name__ == "__main__":
    mechfight.run(Itachi)
