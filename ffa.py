"""Original free-for-all candidate controllers."""
from bot import Bot, DIRECTIONS, distance
from navigation import Navigator


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


class FightRead(HealthRecovery):
    """Compare projected damage while near a reachable recovery waypoint."""
    def tactical_mode(self, mode, obs, enemies):
        mode = super().tactical_mode(mode, obs, enemies)
        if not self.navigator or not enemies:
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


class FreeForAll(CleanupTarget, CoverPeek, FightRead):
    """Combine targeting, cover peeks, fight assessment and recovery."""
