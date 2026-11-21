#!/usr/bin/env python3
"""
Tsunade - Enhanced FFA controller with fight read, engagement gating, cover, sound tracking.

Based on:
- Original bot.py (policy, A* nav, upgrades)
- charliescull patterns: WorldModel, Reasoner, FightRead, SoundMap, CoverAgent
"""

import os
import sys
import math
import json
import heapq
from collections import deque
from typing import List, Tuple, Dict, Optional, Set, Any
from dataclasses import dataclass, field
from enum import Enum

# ===== CONFIGURATION =====
DEBUG = os.environ.get("TSUNADE_DEBUG", "false").lower() == "true"

# Grid resolution for A*
GRID_SIZE = 4.0  # meters (finer than original 6m)

# Fight Read params (from charliescull/situation.py)
ENGAGE_P = 0.65      # Min p(undetected) to engage
TTK_MAX = 2.5        # Max time-to-kill to commit
MAX_CROWD = 2        # Max enemies in cone to engage
ESCAPE_REACH_MULT = 0.5  # Cover must be reachable in < 0.5 * time_to_die

# Movement
MAX_SPEED = 15.0
STEALTH_SPEED = 5.0  # Quiet movement threshold

# Laser management
LASER_HOLD_THRESHOLD = 0.95
LASER_FIRE_THRESHOLD = 0.98

# Cover
COVER_HOLD_TIME = 3.0  # seconds to hold cover

# Sound tracking
SOUND_MAX_AGE = 5.0  # seconds
GHOST_MAX_AGE = 8.0  # seconds


class Mode(Enum):
    ZONE = "zone"
    COVER = "cover"
    FIGHT = "fight"
    LOOT = "loot"
    CUBE = "cube"
    ROAM = "roam"
    HOLD = "hold"
    EVADE = "evade"


@dataclass
class Vec2:
    x: float = 0.0
    y: float = 0.0

    def __add__(self, other: 'Vec2') -> 'Vec2':
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: 'Vec2') -> 'Vec2':
        return Vec2(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: float) -> 'Vec2':
        return Vec2(self.x * scalar, self.y * scalar)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalize(self) -> 'Vec2':
        l = self.length()
        if l == 0:
            return Vec2(0, 0)
        return Vec2(self.x / l, self.y / l)

    def dist(self, other: 'Vec2') -> float:
        return (self - other).length()

    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    @classmethod
    def from_dict(cls, d: dict) -> 'Vec2':
        return cls(d.get("x", 0), d.get("y", 0))

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y}


def angle_diff(a: float, b: float) -> float:
    diff = a - b
    while diff > math.pi:
        diff -= 2 * math.pi
    while diff < -math.pi:
        diff += 2 * math.pi
    return diff


@dataclass
class Threat:
    """Known enemy threat."""
    id: int
    pos: Vec2
    vel: Vec2
    health: float
    max_health: float
    weapon: str
    last_seen_tick: int
    aware: bool = False  # Knows our position
    behind_cover: bool = False

    def time_since_seen(self, current_tick: int) -> float:
        return (current_tick - self.last_seen_tick) * 0.1  # 10 Hz


@dataclass
class GhostThreat:
    """Inferred threat from sounds/damage."""
    pos: Vec2
    last_sound_tick: int
    sound_type: str
    confidence: float = 0.5

    def age(self, current_tick: int) -> float:
        return (current_tick - self.last_sound_tick) * 0.1


@dataclass
class CoverSpot:
    """Cover position with metadata."""
    pos: Vec2
    covers_from: List[Vec2]  # Threat positions this covers from
    is_roof: bool = False
    peek_angle: float = 0.0  # Angle to peek from cover


@dataclass
class FightVerdict:
    """Result of fight simulation."""
    should_run: bool
    margin: float  # HP fraction remaining after trade
    time_to_die: float
    cover_reachable: bool
    cover_pos: Optional[Vec2] = None


class WorldModel:
    """Rich world state: LOS, cover, sound, ghost threats, hidden threat tracking."""

    def __init__(self):
        self.me: Optional[Dict] = None
        self.me_pos: Vec2 = Vec2()
        self.me_vel: Vec2 = Vec2()
        self.me_health: float = 100
        self.me_max_health: float = 100
        self.me_heading: float = 0
        self.me_gun_heading: float = 0
        self.me_weapon: str = "gun"
        self.me_secondary: str = "none"
        self.me_charge: float = 0
        self.tick: int = 0
        self.zone_center: Vec2 = Vec2()
        self.zone_radius: float = 0
        self.next_zone_center: Vec2 = Vec2()
        self.next_zone_radius: float = 0
        self.zone_time: float = 0

        self.threats: Dict[int, Threat] = {}
        self.ghosts: List[GhostThreat] = []
        self.cover_spots: List[CoverSpot] = []
        self.loot_positions: List[Vec2] = []
        self.cube_position: Optional[Vec2] = None

        # Navigation grid (lazy init)
        self.nav_grid: Optional[List[List[int]]] = None
        self.grid_origin: Vec2 = Vec2()
        self.map_bounds: Tuple[float, float, float, float] = (0, 0, 2000, 2000)

    def update(self, obs: dict):
        """Update world model from observation."""
        self.tick = obs.get("tick", 0)

        # Find self
        me_id = obs.get("mech_id", 0)
        for m in obs.get("mechs", []):
            if m.get("id") == me_id:
                self.me = m
                self.me_pos = Vec2.from_dict(m)
                self.me_vel = Vec2(m.get("vx", 0), m.get("vy", 0))
                self.me_health = m.get("health", 100)
                self.me_max_health = m.get("max_health", 100)
                self.me_heading = m.get("heading", 0)
                self.me_gun_heading = m.get("gun_heading", 0)
                self.me_weapon = m.get("weapon", "gun")
                self.me_secondary = m.get("secondary", "none")
                self.me_charge = m.get("charge", 0)
                break

        # Zone
        zone = obs.get("zone", {})
        self.zone_center = Vec2(zone.get("x", 0), zone.get("y", 0))
        self.zone_radius = zone.get("radius", 0)
        self.next_zone_center = Vec2(zone.get("next_x", 0), zone.get("next_y", 0))
        self.next_zone_radius = zone.get("next_radius", 0)
        self.zone_time = zone.get("time_remaining", 0)

        # Update threats
        self._update_threats(obs.get("mechs", []))

        # Update sounds -> ghosts
        self._update_ghosts(obs.get("events", []))

        # Update cover spots
        self._update_cover()

        # Loot/cube
        self._update_loot(obs.get("loot", []))

    def _update_threats(self, mechs: List[dict]):
        alive_ids = set()
        for m in mechs:
            if m.get("id") == self.me.get("id") if self.me else False:
                continue
            if m.get("health", 0) <= 0:
                continue

            tid = m.get("id")
            alive_ids.add(tid)

            pos = Vec2.from_dict(m)
            vel = Vec2(m.get("vx", 0), m.get("vy", 0))

            if tid in self.threats:
                t = self.threats[tid]
                t.pos = pos
                t.vel = vel
                t.health = m.get("health", 100)
                t.max_health = m.get("max_health", 100)
                t.weapon = m.get("weapon", "gun")
                t.last_seen_tick = self.tick
                # Check if aware (has LOS to us)
                t.aware = self._has_los(t.pos)
            else:
                self.threats[tid] = Threat(
                    id=tid,
                    pos=pos,
                    vel=vel,
                    health=m.get("health", 100),
                    max_health=m.get("max_health", 100),
                    weapon=m.get("weapon", "gun"),
                    last_seen_tick=self.tick,
                    aware=self._has_los(pos)
                )

        # Remove dead
        dead_ids = set(self.threats.keys()) - alive_ids
        for did in dead_ids:
            del self.threats[did]

    def _update_ghosts(self, events: List[dict]):
        # Age out old ghosts
        self.ghosts = [g for g in self.ghosts if g.age(self.tick) < GHOST_MAX_AGE]

        for event in events:
            if event.get("type") != "sound":
                continue

            weapon = event.get("weapon", "").lower()
            if weapon not in COMBAT_SOUNDS:
                continue

            pos = Vec2(event.get("x", 0), event.get("y", 0))

            # Check if matches existing ghost
            matched = False
            for g in self.ghosts:
                if g.pos.dist(pos) < 50 and g.sound_type == weapon:
                    g.pos = pos
                    g.last_sound_tick = self.tick
                    g.confidence = min(1.0, g.confidence + 0.1)
                    matched = True
                    break

            if not matched:
                self.ghosts.append(GhostThreat(
                    pos=pos,
                    last_sound_tick=self.tick,
                    sound_type=weapon,
                    confidence=0.5
                ))

    def _update_cover(self):
        """Find cover positions relative to threats."""
        self.cover_spots = []
        if not self.threats:
            return

        threat_positions = [t.pos for t in self.threats.values()]

        # Sample positions around me
        for angle in [i * math.pi / 8 for i in range(16)]:
            for dist in [20, 40, 60, 80]:
                pos = self.me_pos + Vec2(math.cos(angle), math.sin(angle)) * dist

                # Check if covers from any threat
                covers_from = []
                for tp in threat_positions:
                    if self._position_covers(pos, tp):
                        covers_from.append(tp)

                if covers_from:
                    is_roof = self._is_roof_access(pos)
                    self.cover_spots.append(CoverSpot(
                        pos=pos,
                        covers_from=covers_from,
                        is_roof=is_roof,
                        peek_angle=angle
                    ))

    def _update_loot(self, loot: List[dict]):
        self.loot_positions = [Vec2.from_dict(l) for l in loot]

    def _has_los(self, from_pos: Vec2, to_pos: Optional[Vec2] = None) -> bool:
        """Check line of sight (simplified - would use map in real impl)."""
        if to_pos is None:
            to_pos = self.me_pos
        # Placeholder - real impl would check map obstacles
        return True

    def _position_covers(self, pos: Vec2, threat_pos: Vec2) -> bool:
        """Check if position breaks LOS from threat."""
        # Simplified: behind obstacle or far enough
        return pos.dist(threat_pos) > 100  # Placeholder

    def _is_roof_access(self, pos: Vec2) -> bool:
        """Check if position has roof access."""
        return False  # Placeholder

    def nearest_threat(self) -> Optional[Threat]:
        if not self.threats:
            return None
        return min(self.threats.values(), key=lambda t: t.pos.dist(self.me_pos))

    def threats_in_cone(self, angle: float, cone_width: float = math.pi/3) -> int:
        """Count threats within cone of given angle."""
        count = 0
        for t in self.threats.values():
            to_t = t.pos - self.me_pos
            if angle_diff(to_t.angle(), angle) < cone_width / 2:
                count += 1
        return count

    def in_zone(self, pos: Optional[Vec2] = None) -> bool:
        if pos is None:
            pos = self.me_pos
        return pos.dist(self.zone_center) <= self.zone_radius


class FightRead:
    """Simulate fight forward to evaluate trade outcomes (charliescull/situation.py)."""

    def __init__(self, world: WorldModel):
        self.world = world

    def evaluate(self, target: Threat) -> FightVerdict:
        """
        Simulate 1v1 trade:
        - Target shoots at us
        - Other aware threats join after travel time
        - We kill them one at a time (quickest first)
        - Compute margin: HP remaining / max HP
        """
        me = self.world.me_pos
        my_hp = self.world.me_health
        my_max_hp = self.world.me_max_health

        # Time for target to hit us
        dist = target.pos.dist(me)
        projectile_speed = self._projectile_speed(target.weapon)
        travel_time = dist / projectile_speed if projectile_speed > 0 else 0

        # DPS from target
        target_dps = self._weapon_dps(target.weapon)

        # Other aware threats that will join
        joiners = []
        for t in self.world.threats.values():
            if t.id == target.id:
                continue
            if t.aware:
                joiners.append(t)

        # Sort joiners by travel time
        joiners.sort(key=lambda t: t.pos.dist(me) / self._projectile_speed(t.weapon))

        # Simulate: we kill target, then joiners one by one
        current_hp = my_hp
        time_elapsed = 0

        # Our TTK on target
        our_dps = self._our_dps()
        target_ttk = target.health / our_dps if our_dps > 0 else float('inf')

        # Damage taken while killing target
        dmg_during_target = target_dps * target_ttk
        current_hp -= dmg_during_target
        time_elapsed += target_ttk

        if current_hp <= 0:
            return FightVerdict(
                should_run=True,
                margin=0,
                time_to_die=time_elapsed,
                cover_reachable=False
            )

        # Joiners arrive and shoot
        for joiner in joiners:
            j_dist = joiner.pos.dist(me)
            j_travel = j_dist / self._projectile_speed(joiner.weapon)
            j_ttk = joiner.health / our_dps if our_dps > 0 else float('inf')

            # Damage while joiner travels + we kill them
            dmg = joiner_dps * (j_travel + j_ttk)
            current_hp -= dmg
            time_elapsed += j_travel + j_ttk

            if current_hp <= 0:
                return FightVerdict(
                    should_run=True,
                    margin=0,
                    time_to_die=time_elapsed,
                    cover_reachable=self._cover_reachable_in(time_elapsed)
                )

        margin = current_hp / my_max_hp

        # Check cover reachability
        cover_reachable, cover_pos = self._best_cover_reachable()

        # Run if margin <= 0 AND cover reachable in < 0.5 * time_to_die
        # (Simplified: run if margin < 0.3 and cover exists)
        should_run = margin < 0.3 and cover_reachable

        return FightVerdict(
            should_run=should_run,
            margin=margin,
            time_to_die=time_elapsed,
            cover_reachable=cover_reachable,
            cover_pos=cover_pos
        )

    def _our_dps(self) -> float:
        """Estimate our DPS."""
        if self.world.me_weapon == "laser":
            return 80 * (self.world.me_charge / 100.0)
        return 50  # Base gun

    def _weapon_dps(self, weapon: str) -> float:
        dps_map = {
            "gun": 50, "laser": 80, "lightning": 60,
            "mortar": 40, "shotgun": 100, "railgun": 70, "plasma": 55
        }
        return dps_map.get(weapon, 50)

    def _projectile_speed(self, weapon: str) -> float:
        speed_map = {
            "gun": 300, "laser": 1000, "lightning": 500,
            "mortar": 100, "shotgun": 200, "railgun": 800, "plasma": 400
        }
        return speed_map.get(weapon, 300)

    def _cover_reachable_in(self, time_limit: float) -> bool:
        """Check if any cover reachable within time_limit."""
        for cover in self.world.cover_spots:
            dist = cover.pos.dist(self.world.me_pos)
            if dist / MAX_SPEED < time_limit * ESCAPE_REACH_MULT:
                return True
        return False

    def _best_cover_reachable(self) -> Tuple[bool, Optional[Vec2]]:
        best = None
        best_score = -1
        for cover in self.world.cover_spots:
            dist = cover.pos.dist(self.world.me_pos)
            time_needed = dist / MAX_SPEED
            # Prefer closer cover that covers from more threats
            score = len(cover.covers_from) * 100 - time_needed * 10
            if score > best_score:
                best_score = score
                best = cover
        return (best is not None, best.pos if best else None)


class Reasoner:
    """Intent selection with engagement gating (charliescull/reasoner.py)."""

    def __init__(self, world: WorldModel, fight_read: FightRead):
        self.world = world
        self.fight_read = fight_read
        self.current_intent = Mode.ROAM
        self.intent_lock_tick = 0
        self.cover_hold_until = 0

    def update(self) -> Mode:
        """Select intent for this tick."""
        # Stance lock: hold intent for 30 ticks (3s) unless forced
        if self.tick - self.intent_lock_tick < 30 and not self._must_switch():
            return self.current_intent

        # Priority 1: Zone survival
        if not self.world.in_zone():
            self._set_intent(Mode.ZONE)
            return Mode.ZONE

        # Priority 2: Fight read says run
        nearest = self.world.nearest_threat()
        if nearest:
            verdict = self.fight_read.evaluate(nearest)
            if verdict.should_run and verdict.cover_reachable:
                self._set_intent(Mode.COVER)
                self.cover_hold_until = self.tick + int(COVER_HOLD_TIME * 10)
                return Mode.COVER

        # Priority 3: Low health -> heal/evade
        hp_frac = self.world.me_health / self.world.me_max_health
        if hp_frac < 0.3:
            if self.world.cover_spots:
                self._set_intent(Mode.COVER)
                return Mode.COVER
            self._set_intent(Mode.EVADE)
            return Mode.EVADE

        # Priority 4: Engagement gating - can we fight?
        if nearest and self._engage_ok(nearest):
            self._set_intent(Mode.FIGHT)
            return Mode.FIGHT

        # Priority 5: Loot nearby
        if self.world.loot_positions:
            nearest_loot = min(self.world.loot_positions, key=lambda l: l.dist(self.world.me_pos))
            if nearest_loot.dist(self.world.me_pos) < 80:
                self._set_intent(Mode.LOOT)
                return Mode.LOOT

        # Priority 6: Cube
        if self.world.cube_position and self.world.cube_position.dist(self.world.me_pos) < 150:
            self._set_intent(Mode.CUBE)
            return Mode.CUBE

        # Default: Roam
        self._set_intent(Mode.ROAM)
        return Mode.ROAM

    def _engage_ok(self, target: Threat) -> bool:
        """Engagement gating (charliescull reasoner.py logic)."""
        # STRIKE conditions
        ttk = target.health / self.fight_read._our_dps() if self.fight_read._our_dps() > 0 else float('inf')

        if ttk > TTK_MAX:
            return False

        crowd = self.world.threats_in_cone((target.pos - self.world.me_pos).angle())
        if crowd > MAX_CROWD:
            return False

        # Hidden: target unaware OR behind us
        to_target = target.pos - self.world.me_pos
        angle_to_target = to_target.angle()
        behind_us = abs(angle_diff(angle_to_target, self.world.me_heading)) > math.pi / 2
        p_undetected = 1.0 if (not target.aware or behind_us) else 0.0

        if p_undetected < ENGAGE_P:
            # Check if escape exists
            _, cover_pos = self.fight_read._best_cover_reachable()
            if not cover_pos:
                return False

        return True

    def _must_switch(self) -> bool:
        """Forced intent switch conditions."""
        # Zone pressure
        if not self.world.in_zone():
            return True
        # Fight read says run
        nearest = self.world.nearest_threat()
        if nearest:
            verdict = self.fight_read.evaluate(nearest)
            if verdict.should_run:
                return True
        # Cover hold expired
        if self.current_intent == Mode.COVER and self.tick > self.cover_hold_until:
            return True
        return False

    def _set_intent(self, mode: Mode):
        self.current_intent = mode
        self.intent_lock_tick = self.tick

    @property
    def tick(self) -> int:
        return self.world.tick


class CoverAgent:
    """Peek-shoot from cover (charliescull/brain/cover.py)."""

    def __init__(self, world: WorldModel):
        self.world = world
        self.peek_state = "hold"  # hold, peek, fire, retreat
        self.peek_timer = 0

    def act(self, verdict: FightVerdict) -> dict:
        if not verdict.cover_pos:
            return self._fallback()

        cover = verdict.cover_pos
        to_cover = cover - self.world.me_pos
        dist = to_cover.length()

        if dist > 5:
            # Move to cover
            return {
                "move": (to_cover.normalize() * MAX_SPEED).to_dict(),
                "gun_angle": self.world.me_gun_heading,
                "fire": False,
                "boost": False
            }

        # At cover - peek/shoot cycle
        nearest = self.world.nearest_threat()
        if nearest:
            target_angle = (nearest.pos - self.world.me_pos).angle()
            angle_diff_val = angle_diff(target_angle, self.world.me_gun_heading)

            if abs(angle_diff_val) < 0.2:
                # Aligned - peek and fire
                return {
                    "move": Vec2(0, 0).to_dict(),
                    "gun_angle": target_angle,
                    "fire": True,
                    "boost": False
                }
            else:
                # Rotate to target
                return {
                    "move": Vec2(0, 0).to_dict(),
                    "gun_angle": target_angle,
                    "fire": False,
                    "boost": False
                }

        return self._fallback()

    def _fallback(self) -> dict:
        return {
            "move": Vec2(0, 0).to_dict(),
            "gun_angle": self.world.me_gun_heading,
            "fire": False,
            "boost": False
        }


class SoundTracker:
    """Track ghost threats from sounds (charliescull/sound.py)."""

    def __init__(self):
        self.ghosts: List[GhostThreat] = []

    def update(self, events: List[dict], current_tick: int):
        # Age out
        self.ghosts = [g for g in self.ghosts if g.age(current_tick) < GHOST_MAX_AGE]

        for event in events:
            if event.get("type") != "sound":
                continue
            weapon = event.get("weapon", "").lower()
            if weapon not in COMBAT_SOUNDS:
                continue

            pos = Vec2(event.get("x", 0), event.get("y", 0))

            # Merge or create
            for g in self.ghosts:
                if g.pos.dist(pos) < 50 and g.sound_type == weapon:
                    g.pos = pos
                    g.last_sound_tick = current_tick
                    g.confidence = min(1.0, g.confidence + 0.15)
                    break
            else:
                self.ghosts.append(GhostThreat(pos, current_tick, weapon, 0.5))

    def nearest_ghost(self, from_pos: Vec2) -> Optional[GhostThreat]:
        if not self.ghosts:
            return None
        return min(self.ghosts, key=lambda g: g.pos.dist(from_pos))


class AStarNavigator:
    """Terrain-aware A* on 4m grid (finer than original 6m)."""

    def __init__(self, world: WorldModel):
        self.world = world
        self.grid_size = GRID_SIZE

    def find_path(self, start: Vec2, goal: Vec2, avoid_threats: bool = True) -> List[Vec2]:
        """Find path using A*."""
        # Simplified: direct path with threat avoidance
        # Real impl would use grid
        path = [start, goal]

        if avoid_threats and self.world.threats:
            # Add waypoint to avoid nearest threat
            nearest = self.world.nearest_threat()
            if nearest:
                avoid_dir = (start - nearest.pos).normalize() * 50
                waypoint = start + avoid_dir
                path = [start, waypoint, goal]

        return path

    def next_waypoint(self, goal: Vec2, avoid_threats: bool = True) -> Vec2:
        path = self.find_path(self.world.me_pos, goal, avoid_threats)
        if len(path) > 1:
            return path[1]
        return goal


class Tsunade:
    """
    Main FFA controller integrating all components.
    """

    def __init__(self):
        self.world = WorldModel()
        self.fight_read = FightRead(self.world)
        self.reasoner = Reasoner(self.world, self.fight_read)
        self.cover_agent = CoverAgent(self.world)
        self.sound_tracker = SoundTracker()
        self.navigator = AStarNavigator(self.world)

        # Policy weights (from original bot.py)
        self.policy_weights = self._default_policy()

        # Upgrade priority (armor-first for gunlaser kit)
        self.upgrade_priority = [
            "armor", "plating", "laser_charge", "armor", "plating",
            "laser_damage", "engine", "cooling", "gun_damage", "gun_charge"
        ]
        self.bought_upgrades = set()

        # State
        self.mode = Mode.ROAM
        self.cover_hold_timer = 0
        self.last_shot_tick = -100

    def _default_policy(self) -> Dict:
        """4x7 policy matrix: modes x features."""
        return {
            "evade":    [1.0, 0.0, 0.0, 0.0, 0.5, 1.0, 0.0],
            "scavenge": [0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.5],
            "engage":   [0.0, 0.0, 1.0, 0.5, 0.0, 0.0, 1.0],
            "hold":     [0.5, 0.5, 0.5, 1.0, 0.0, 0.0, 0.0],
        }

    def act(self, obs: dict) -> dict:
        """Main decision function."""
        self.world.update(obs)
        self.sound_tracker.update(obs.get("events", []), self.world.tick)

        # Reasoner selects intent
        intent = self.reasoner.update()
        self.mode = intent

        # Execute based on mode
        if intent == Mode.ZONE:
            return self._act_zone()
        elif intent == Mode.COVER:
            return self._act_cover()
        elif intent == Mode.FIGHT:
            return self._act_fight()
        elif intent == Mode.LOOT:
            return self._act_loot()
        elif intent == Mode.CUBE:
            return self._act_cube()
        elif intent == Mode.EVADE:
            return self._act_evade()
        elif intent == Mode.HOLD:
            return self._act_hold()
        else:
            return self._act_roam()

    def _act_zone(self) -> dict:
        """Move to zone urgently."""
        target = self.world.zone_center
        if self.world.next_zone_radius > 0:
            # Predict next zone
            target = self.world.next_zone_center

        wp = self.navigator.next_waypoint(target)
        move = (wp - self.world.me_pos).normalize() * MAX_SPEED

        return {
            "move": move.to_dict(),
            "gun_angle": self.world.me_gun_heading,
            "fire": False,
            "boost": True,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _act_cover(self) -> dict:
        """Peek-shoot from cover."""
        nearest = self.world.nearest_threat()
        if nearest:
            verdict = self.fight_read.evaluate(nearest)
            return self.cover_agent.act(verdict)
        return self._act_hold()

    def _act_fight(self) -> dict:
        """Engage with fire gating."""
        nearest = self.world.nearest_threat()
        if not nearest:
            return self._act_roam()

        to_target = nearest.pos - self.world.me_pos
        target_angle = to_target.angle()
        angle_diff_val = angle_diff(target_angle, self.world.me_gun_heading)

        # Fire gating: only fire if aligned and engage_ok
        fire = abs(angle_diff_val) < 0.25 and self.reasoner._engage_ok(nearest)

        # Movement: strafe perpendicular
        strafe = Vec2(-to_target.y, to_target.x).normalize() * MAX_SPEED * 0.7

        # Stealth if enemies close and we're hidden
        if to_target.length() < 60 and not nearest.aware:
            strafe = strafe * (STEALTH_SPEED / MAX_SPEED)

        # Laser charge management
        if self.world.me_weapon == "laser":
            if self.world.me_charge < LASER_HOLD_THRESHOLD * 100:
                fire = False
            elif self.world.me_charge > LASER_FIRE_THRESHOLD * 100:
                fire = True

        return {
            "move": strafe.to_dict(),
            "gun_angle": target_angle,
            "fire": fire,
            "boost": False,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _act_loot(self) -> dict:
        """Move to nearest loot."""
        if not self.world.loot_positions:
            return self._act_roam()

        target = min(self.world.loot_positions, key=lambda l: l.dist(self.world.me_pos))
        wp = self.navigator.next_waypoint(target)
        move = (wp - self.world.me_pos).normalize() * MAX_SPEED

        return {
            "move": move.to_dict(),
            "gun_angle": self.world.me_gun_heading,
            "fire": False,
            "boost": False,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _act_cube(self) -> dict:
        """Move to cube."""
        if not self.world.cube_position:
            return self._act_roam()

        target = self.world.cube_position
        wp = self.navigator.next_waypoint(target)
        move = (wp - self.world.me_pos).normalize() * MAX_SPEED

        return {
            "move": move.to_dict(),
            "gun_angle": self.world.me_gun_heading,
            "fire": False,
            "boost": True,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _act_evade(self) -> dict:
        """Flee from threats."""
        nearest = self.world.nearest_threat()
        if not nearest:
            return self._act_roam()

        away = (self.world.me_pos - nearest.pos).normalize() * MAX_SPEED

        # Try to find cover while fleeing
        if self.world.cover_spots:
            cover = min(self.world.cover_spots, key=lambda c: c.pos.dist(self.world.me_pos))
            to_cover = cover.pos - self.world.me_pos
            if to_cover.length() < 100:
                away = to_cover.normalize() * MAX_SPEED

        return {
            "move": away.to_dict(),
            "gun_angle": self.world.me_gun_heading,
            "fire": False,
            "boost": True,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _act_hold(self) -> dict:
        """Hold position, defend."""
        nearest = self.world.nearest_threat()
        if nearest:
            target_angle = (nearest.pos - self.world.me_pos).angle()
            fire = abs(angle_diff(target_angle, self.world.me_gun_heading)) < 0.3
            return {
                "move": Vec2(0, 0).to_dict(),
                "gun_angle": target_angle,
                "fire": fire,
                "boost": False,
                "buy": None,
                "upgrade": self._next_upgrade()
            }
        return self._act_roam()

    def _act_roam(self) -> dict:
        """Patrol/rotate."""
        # Circle around zone center
        to_center = self.world.zone_center - self.world.me_pos
        tangent = Vec2(-to_center.y, to_center.x).normalize() * MAX_SPEED * 0.5

        return {
            "move": tangent.to_dict(),
            "gun_angle": self.world.me_gun_heading + 0.1,
            "fire": False,
            "boost": False,
            "buy": None,
            "upgrade": self._next_upgrade()
        }

    def _next_upgrade(self) -> Optional[str]:
        """Get next upgrade in priority order."""
        for upg in self.upgrade_priority:
            if upg not in self.bought_upgrades:
                self.bought_upgrades.add(upg)
                return upg
        return None


# ===== Module entry point =====
_tsunade_instance = None

def act(obs: dict) -> dict:
    global _tsunade_instance
    if _tsunade_instance is None:
        _tsunade_instance = Tsunade()
    return _tsunade_instance.act(obs)


if __name__ == "__main__":
    print("Tsunade FFA controller loaded OK")