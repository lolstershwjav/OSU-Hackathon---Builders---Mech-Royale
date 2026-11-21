#!/usr/bin/env python3
"""
Naruto - Hybrid practice/FFA bot with fixed handoff logic.

Architecture:
- Uses Naruto practice driver (Go binary) for squad practice until FFA detected
- Switches to Tsunade (Python FFA brain) when FFA conditions met
- Stricter detection: 3+ unexplained deaths + spatial clustering + combat sounds
"""

import os
import sys
import json
import subprocess
import threading
import time
import math
from collections import deque
from typing import Optional, List, Tuple, Dict, Any

# ===== CONFIGURATION (tunable via env vars) =====
FFA_DEATHS_THRESHOLD = int(os.environ.get("NARUTO_FFA_DEATHS", "3"))
FFA_TIME_WINDOW = float(os.environ.get("NARUTO_FFA_WINDOW", "90.0"))
FFA_CLUSTER_RADIUS = float(os.environ.get("NARUTO_FFA_CLUSTER", "120.0"))
FFA_REQUIRE_SOUND = os.environ.get("NARUTO_FFA_SOUND", "true").lower() == "true"
DEBUG = os.environ.get("NARUTO_DEBUG", "false").lower() == "true"
NARUTO_TIMEOUT_MS = int(os.environ.get("NARUTO_TIMEOUT", "22"))

# Sound types that indicate combat
COMBAT_SOUNDS = {"gun", "laser", "lightning", "mortar", "shotgun", "railgun", "plasma"}


class DeathEvent:
    """Track an unexplained death event."""
    def __init__(self, tick: int, position: Tuple[float, float], sound_nearby: bool):
        self.tick = tick
        self.position = position
        self.sound_nearby = sound_nearby


class Naruto:
    """
    Hybrid bot: Naruto practice driver -> Tsunade for FFA.

    Handoff triggers when ALL conditions met:
    1. >= FFA_DEATHS_THRESHOLD unexplained deaths in FFA_TIME_WINDOW seconds
    2. Deaths spatially clustered within FFA_CLUSTER_RADIUS meters
    3. Combat sounds detected (if FFA_REQUIRE_SOUND)
    """

    def __init__(self):
        self.naruto_process: Optional[subprocess.Popen] = None
        self.tsunade = None  # Lazy init
        self.tsunade_initialized = False

        # State tracking
        self.previous_alive = 16
        self.own_kills = 0
        self.death_events: deque = deque(maxlen=100)
        self.combat_sounds_recent = 0
        self.switched = False
        self.switch_reason = ""
        self.switch_tick = -1

        # Naruto practice driver communication
        self.naruto_lock = threading.Lock()
        self.naruto_ready = threading.Event()
        self.naruto_response = None
        self.naruto_error = None

        # Start Naruto practice driver
        self._start_naruto()

    def _start_naruto(self):
        """Launch Naruto practice driver Go binary as child process."""
        naruto_path = "/app/naruto-bin"
        if not os.path.exists(naruto_path):
            # Fallback for local testing
            naruto_path = "./naruto-bin"

        if not os.path.exists(naruto_path):
            if DEBUG:
                print(f"[Naruto] Practice driver binary not found at {naruto_path}", file=sys.stderr, flush=True)
            self.naruto_process = None
            return

        try:
            self.naruto_process = subprocess.Popen(
                [naruto_path],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            if DEBUG:
                print(f"[Naruto] Started practice driver (PID: {self.naruto_process.pid})", file=sys.stderr, flush=True)
        except Exception as e:
            if DEBUG:
                print(f"[Naruto] Failed to start practice driver: {e}", file=sys.stderr, flush=True)
            self.naruto_process = None

    def _lazy_init_tsunade(self):
        """Import and initialize Tsunade only when needed."""
        if self.tsunade_initialized:
            return
        try:
            from tsunade import Tsunade
            self.tsunade = Tsunade()
            self.tsunade_initialized = True
            if DEBUG:
                print("[Naruto] Tsunade lazy-initialized", file=sys.stderr, flush=True)
        except Exception as e:
            if DEBUG:
                print(f"[Naruto] Failed to init Tsunade: {e}", file=sys.stderr, flush=True)
            self.tsunade = None

    def _send_to_naruto(self, obs: dict) -> Optional[dict]:
        """Send observation to Giddy, return action or None on failure."""
        if not self.naruto_process or self.naruto_process.poll() is not None:
            return None

        try:
            # Send observation
            self.naruto_process.stdin.write(json.dumps(obs) + "\n")
            self.naruto_process.stdin.flush()

            # Read response with timeout
            import select
            ready, _, _ = select.select([self.naruto_process.stdout], [], [], NARUTO_TIMEOUT_MS / 1000.0)
            if not ready:
                if DEBUG:
                    print(f"[Naruto] Practice driver timeout ({NARUTO_TIMEOUT_MS}ms)", file=sys.stderr, flush=True)
                return None

            line = self.naruto_process.stdout.readline()
            if not line:
                return None

            return json.loads(line.strip())
        except (json.JSONDecodeError, BrokenPipeError, OSError) as e:
            if DEBUG:
                print(f"[Naruto] Practice driver comm error: {e}", file=sys.stderr, flush=True)
            return None

    def _detect_combat_sounds(self, obs: dict) -> int:
        """Count combat-related sounds in recent events."""
        count = 0
        for event in obs.get("events", []):
            if event.get("type") == "sound":
                weapon = event.get("weapon", "").lower()
                if weapon in COMBAT_SOUNDS:
                    count += 1
        return count

    def _update_death_tracking(self, obs: dict):
        """Track unexplained deaths and combat sounds."""
        alive = sum(1 for m in obs.get("mechs", []) if m.get("health", 0) > 0)
        tick = obs.get("tick", 0)

        # Count combat sounds
        self.combat_sounds_recent = self._detect_combat_sounds(obs)

        # Detect deaths (alive dropped more than our kills)
        deaths_this_tick = self.previous_alive - alive - self.own_kills
        if deaths_this_tick > 0:
            me = next((m for m in obs.get("mechs", []) if m.get("id") == obs.get("mech_id")), None)
            my_pos = (me.get("x", 0), me.get("y", 0)) if me else (0, 0)

            for _ in range(deaths_this_tick):
                self.death_events.append(DeathEvent(
                    tick=tick,
                    position=my_pos,  # Approximate - we don't know exact death positions
                    sound_nearby=self.combat_sounds_recent > 0
                ))

            if DEBUG:
                print(f"[Naruto] Tick {tick}: {deaths_this_tick} unexplained deaths, "
                      f"total in window: {len(self.death_events)}, sounds: {self.combat_sounds_recent}",
                      file=sys.stderr, flush=True)

        self.previous_alive = alive
        self.own_kills = 0  # Reset after accounting

    def _check_ffa_switch(self, obs: dict) -> bool:
        """Check if all FFA switch conditions are met."""
        if self.switched:
            return True

        tick = obs.get("tick", 0)
        window_ticks = int(FFA_TIME_WINDOW * 10)  # 10 Hz

        # Filter recent deaths
        recent_deaths = [d for d in self.death_events if tick - d.tick <= window_ticks]

        if len(recent_deaths) < FFA_DEATHS_THRESHOLD:
            return False

        # Check spatial clustering
        if not self._deaths_clustered(recent_deaths):
            if DEBUG:
                print(f"[Naruto] Tick {tick}: {len(recent_deaths)} deaths but not clustered",
                      file=sys.stderr, flush=True)
            return False

        # Check combat sounds
        if FFA_REQUIRE_SOUND and self.combat_sounds_recent == 0:
            if DEBUG:
                print(f"[Naruto] Tick {tick}: deaths clustered but no combat sounds",
                      file=sys.stderr, flush=True)
            return False

        # All conditions met - switch!
        self.switched = True
        self.switch_tick = tick
        self.switch_reason = (f"deaths={len(recent_deaths)} "
                              f"clustered=yes sounds={self.combat_sounds_recent}")
        if DEBUG:
            print(f"[Naruto] *** SWITCH TO TSUNADE at tick {tick}: {self.switch_reason} ***",
                  file=sys.stderr, flush=True)
        return True

    def _deaths_clustered(self, deaths: List[DeathEvent]) -> bool:
        """Check if deaths are spatially clustered within FFA_CLUSTER_RADIUS."""
        if len(deaths) < 2:
            return True

        # Check if all deaths within radius of each other
        for i, d1 in enumerate(deaths):
            clustered = 1
            for j, d2 in enumerate(deaths):
                if i != j:
                    dist = math.hypot(d1.position[0] - d2.position[0],
                                      d1.position[1] - d2.position[1])
                    if dist <= FFA_CLUSTER_RADIUS:
                        clustered += 1
            if clustered >= FFA_DEATHS_THRESHOLD:
                return True
        return False

    def _handle_kill_event(self, obs: dict):
        """Track our own kills to exclude from unexplained deaths."""
        for event in obs.get("events", []):
            if event.get("type") == "kill" and event.get("source_id") == obs.get("mech_id"):
                self.own_kills += 1

    def act(self, obs: dict) -> dict:
        """Main decision function - called every tick."""
        self._handle_kill_event(obs)
        self._update_death_tracking(obs)

        # Check for FFA switch
        if not self.switched:
            self._check_ffa_switch(obs)

        # Route to appropriate brain
        if self.switched:
            return self._act_tsunade(obs)
        else:
            return self._act_naruto(obs)

    def _act_naruto(self, obs: dict) -> dict:
        """Get action from Naruto practice driver."""
        action = self._send_to_naruto(obs)
        if action is not None:
            return action

        # Giddy failed - fallback to Tsunade
        if DEBUG:
            print(f"[Naruto] Practice driver failed, falling back to Tsunade", file=sys.stderr, flush=True)
        self.switched = True
        self.switch_reason = "practice_driver_failed"
        return self._act_tsunade(obs)

    def _act_tsunade(self, obs: dict) -> dict:
        """Get action from Tsunade."""
        self._lazy_init_tsunade()
        if self.tsunade is not None:
            try:
                return self.tsunade.act(obs)
            except Exception as e:
                if DEBUG:
                    print(f"[Naruto] Tsunade error: {e}", file=sys.stderr, flush=True)

        # Ultimate fallback - minimal valid action
        return {
            "move": {"x": 0, "y": 0},
            "gun_angle": 0,
            "fire": False,
            "boost": False,
            "buy": None,
            "upgrade": None
        }

    def get_debug_info(self) -> dict:
        """Return debug information for logging."""
        return {
            "switched": self.switched,
            "switch_tick": self.switch_tick,
            "switch_reason": self.switch_reason,
            "deaths_in_window": len(self.death_events),
            "combat_sounds": self.combat_sounds_recent,
            "naruto_alive": self.naruto_process is not None and self.naruto_process.poll() is None,
            "tsunade_ready": self.tsunade_initialized
        }


# ===== Module-level entry point for mechfight =====
_naruto_instance = None

def act(obs: dict) -> dict:
    """Module-level act function for mechfight protocol."""
    global _naruto_instance
    if _naruto_instance is None:
        _naruto_instance = Naruto()
    return _naruto_instance.act(obs)


if __name__ == "__main__":
    # Quick self-test
    print("Naruto module loaded OK")
    print(f"Config: deaths>={FFA_DEATHS_THRESHOLD}, window={FFA_TIME_WINDOW}s, "
          f"cluster={FFA_CLUSTER_RADIUS}m, sound_required={FFA_REQUIRE_SOUND}")