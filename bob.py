"""Bob: an original controller built on the team's navigation and policy."""

import mechfight
from bot import Bot, DIRECTIONS, distance
from navigation import Navigator


class Bob(Bot):
    name = "Bob"
    recovering = False
    cover_goal = None

    def tactical_mode(self, mode, obs, enemies):
        me = obs["self"]
        health = me["hp"] / me["max_hp"]
        nearby = sum(distance(me["pos"], e["pos"]) < 85 for e in enemies)
        hit = obs["t"] - self.last_hit < 3
        if health < .72 and (nearby >= 2 or health < .48 and hit):
            self.recovering = True
        elif health >= .82:
            self.recovering = False
            self.cover_goal = None
        return "evade" if self.recovering else mode

    def zone_deadline(self, me, zone, remaining, next_margin):
        speed = me["stats"].get("walk_speed", 11.7)
        travel = max(0, -next_margin + 9) / max(1, speed)
        return (super().zone_deadline(me, zone, remaining, next_margin)
                or (next_margin < 0 and 1.25 * travel + 5 >= remaining))

    def recovery_goal(self, obs, threats, goal, urgent):
        if urgent or not self.recovering or not self.navigator:
            self.cover_goal = None
            return goal
        me, zone = obs["self"], obs["zone"]
        position = me["pos"]
        arena = self.navigator.arena

        def safe(point):
            return (distance(point, zone["next_center"]) < zone["next_radius"] - 8
                    and all(arena.cover(point, threat) for threat in threats))

        if self.cover_goal and safe(self.cover_goal) and self.blocked < 9:
            return self.cover_goal
        if self.cover_goal is None and not threats:
            return position
        self.cover_goal = None
        shape = Navigator.shape({**self.mech_config, **me["stats"]})
        for reach in (9, 18):
            for dx, dy in DIRECTIONS[::2]:
                x, y = position[0] + reach * dx, position[1] + reach * dy
                point = (x, y, arena.ground(x, y))
                if (safe(point) and arena.clear(*point, *shape[:2])
                        and arena.segment(position, point, shape)):
                    self.cover_goal = point
                    return point
        return goal

    def act(self, obs):
        action = super().act(obs)
        me = obs["self"]
        if (self.recovering and me["in_zone"] and
                (self.cover_goal and distance(me["pos"], self.cover_goal) < 3
                 or not any(e["kind"] == "mech" for e in obs["mechs"]))):
            action["fire"] = [False, False]
        return action


if __name__ == "__main__":
    mechfight.run(Bob)
