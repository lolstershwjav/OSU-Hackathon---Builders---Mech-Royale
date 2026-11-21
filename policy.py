"""Shared tactical model for live play and offline training."""

FEATURES = ("bias", "missing_hp", "enemy_pressure", "recent_damage",
            "advantage", "loot", "zone_pressure")
MODES = ("evade", "scavenge", "engage", "hold")


def features(hp, distance, enemies, damaged, enemy_hp, loot_distance, zone):
    """Convert current observations into bounded values for a linear policy."""
    return (
        1.0,
        1.0 - hp,
        min(1.0, max(0.0, 1.0 - distance / 180.0) + 0.15 * (enemies > 1)),
        float(damaged),
        (hp - enemy_hp) if enemies else 0.0,
        max(0.0, 1.0 - loot_distance / 130.0),
        min(1.0, max(0.0, zone)),
    )


def choose(weights, inputs):
    scores = [sum(weight * value for weight, value in zip(row, inputs))
              for row in weights]
    return MODES[max(range(len(MODES)), key=scores.__getitem__)]
