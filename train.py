"""Evolve tactical policies in a lightweight 2D placement simulator."""
import argparse
import json
import math
from pathlib import Path
import random
from statistics import mean

from policy import MODES, choose, features

ROOT = Path(__file__).resolve().parent
STARTERS = {
    "guardian": [[-.25, 1, .8, .6, -.3, 0, -.4], [0, -.3, -.8, -.5, 0, 1.7, -.8],
                 [-.6, -.5, 1, 0, 1, 0, -.5], [.15, .2, -.3, 0, 0, 0, .7]],
    "scavenger": [[-.55, 1.3, .7, .6, -.5, 0, -.5], [.1, 0, -.6, -.3, 0, 2.1, -.7],
                  [-.5, -.5, 1.3, 0, 1, 0, -.4], [0, .5, -.65, 0, 0, 0, 1]],
    "duelist": [[-.9, 1.45, .7, .35, -.6, 0, -.5], [-.25, -.4, -.8, -.5, 0, 1.4, -.8],
                [-.1, -.5, 1.55, -.15, 1.1, 0, -.6], [.15, .45, -.6, 0, 0, 0, 1.2]],
}


class Mech:
    def __init__(self, slot, rng):
        angle = rng.uniform(0, math.tau)
        radius = rng.uniform(65, 320)
        self.slot = slot
        self.x = math.cos(angle) * radius
        self.y = math.sin(angle) * radius
        self.hp = 1.0
        self.last_damage = -20
        self.cubes = 0

    def squared_distance(self, x, y):
        dx, dy = self.x - x, self.y - y
        return dx * dx + dy * dy

    def distance(self, x, y):
        return math.hypot(self.x - x, self.y - y)


def match(policies, seed, squad):
    rng = random.Random(seed)
    mechs = [Mech(i, rng) for i in range(16)]
    if squad:
        for m in mechs[1:]:
            m.hp = .45 + .035 * m.slot
    cubes = [(rng.uniform(-300, 300), rng.uniform(-300, 300)) for _ in range(70)]
    center = (rng.uniform(-75, 75), rng.uniform(-75, 75))
    points = [0] * 16
    previous = mechs
    for tick in range(155):
        radius = max(5, 360 - max(0, tick - 12) * 2.65)
        moves = []
        damage = {m.slot: 0.0 for m in previous}
        for m in previous:
            hunter = squad and 0 < m.slot <= min(15, 1 + tick // 16)
            if squad and m.slot:
                opponents = [e for e in previous if hunter and not e.slot]
            else:
                opponents = [e for e in previous if e is not m]
            visible = [(m.squared_distance(e.x, e.y), e) for e in opponents]
            visible = [(d, e) for d, e in visible if d < 180 ** 2]
            nearest, enemy = min(visible, key=lambda item: item[0], default=(999 ** 2, None))
            distance = math.sqrt(nearest)
            loot = min(cubes, key=lambda p: m.squared_distance(*p), default=None)
            margin = m.distance(*center) - radius
            mode = choose(policies[m.slot], features(
                m.hp, distance, len(visible), tick - m.last_damage < 4,
                enemy.hp if enemy else 1.0, m.distance(*loot) if loot else 999,
                (margin + 40) / 60,
            ))
            # The house-bot proxy shares the player's position and applies staggered pressure.
            goal = (m.x, m.y)
            speed = 13.0
            if margin > -10:
                goal = center
            elif hunter and opponents:
                target = opponents[0]
                goal = (target.x, target.y)
            elif mode == "evade" and enemy:
                goal = (2 * m.x - enemy.x, 2 * m.y - enemy.y)
                speed = 18.0
            elif mode == "scavenge" and loot and math.dist(loot, center) < radius:
                goal = loot
            elif mode == "engage" and enemy and distance > 48:
                goal = (enemy.x, enemy.y)
            dx, dy = goal[0] - m.x, goal[1] - m.y
            scale = min(1.0, speed / max(0.01, math.hypot(dx, dy)))
            moves.append((m, m.x + dx * scale, m.y + dy * scale))
            if margin > 0:
                damage[m.slot] += .022 + .0007 * tick
            if enemy and distance < 125 and (hunter or mode == "engage" or distance < 58):
                hit = max(.18, .88 - distance / 200)
                if rng.random() < hit:
                    damage[enemy.slot] += .054 * (1 + .04 * min(m.cubes, 10))
                m.last_damage = tick
        for m, x, y in moves:
            m.x, m.y = x, y
            loot = next((p for p in cubes if m.squared_distance(*p) < 49), None)
            if loot is not None:
                cubes.remove(loot)
                m.cubes += 1
                m.hp = min(1.0, m.hp + .05)
            if damage[m.slot]:
                m.hp -= damage[m.slot]
                m.last_damage = tick
            elif tick - m.last_damage > 5:
                m.hp = min(1.0, m.hp + .045)
        remaining = [m for m in previous if m.hp > 0]
        for m in previous:
            if m.hp <= 0:
                points[m.slot] = 16 - len(previous)
                cubes.append((m.x, m.y))
        if squad and not any(m.slot == 0 for m in remaining):
            return points, tick + 1
        previous = remaining
        if len(previous) <= 1:
            break
    # Simultaneous timeout survivors share the best remaining placement.
    for m in previous:
        points[m.slot] = 15
    return points, tick + 1


def evaluate(candidate, pool, seeds):
    free_for_all = []
    squad = []
    survival = []
    for seed in seeds:
        rng = random.Random(seed)
        # Every finalist faces a mix of the three families and their starters.
        opponents = rng.sample(pool, 15) if len(pool) >= 15 else [
            pool[i % len(pool)] for i in range(15)
        ]
        rng.shuffle(opponents)
        slot = rng.randrange(16)
        lineup = opponents[:slot] + [candidate] + opponents[slot:]
        placements, _ = match(lineup, seed, False)
        free_for_all.append(placements[slot])
        placements, rounds = match([candidate] + opponents, seed, True)
        squad.append(placements[0])
        survival.append(rounds)
    return {"free_for_all": mean(free_for_all), "squad": mean(squad),
            "squad_survival_rounds": mean(survival)}


def score(metrics):
    return (.50 * metrics["squad"] + .40 * metrics["free_for_all"]
            + .10 * 15 * metrics["squad_survival_rounds"] / 155)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=2027)
    parser.add_argument("--generations", type=int, default=4)
    parser.add_argument("--resume", type=Path, help="Previous checkpoint to continue")
    parser.add_argument("--output", type=Path, default=ROOT / "models" / "trained.json")
    args = parser.parse_args()
    if args.generations < 1:
        parser.error("--generations must be positive")
    rng = random.Random(args.seed)
    archive = list(STARTERS.values())
    current = dict(STARTERS)
    prior = json.loads(args.resume.read_text(encoding="utf-8")) if args.resume else None
    incumbent = None
    if prior:
        current = {name: prior["policies"][name] for name in STARTERS}
        incumbent = prior["policies"][prior["champion"]]
        archive.extend([*current.values(), incumbent])
    crossover_wins = dict.fromkeys(MODES, 0)
    for generation in range(args.generations):
        seeds = rng.sample(range(10_000, 900_000), 5)
        evolved = {}
        for name, parent in current.items():
            step = (.15, .30, .55)[generation % 3]
            challengers = [parent] + [
                [[round(max(-3, min(3, weight + rng.gauss(0, step))), 4)
                  for weight in row] for row in parent]
                for _ in range(3)
            ]
            # A row is one mode's scoring rule. Transplant one at a time so a
            # successful borrowing can be attributed to a specific behavior.
            donor_name = rng.choice([other for other in current if other != name])
            mode = rng.randrange(len(MODES))
            donor = current[donor_name]
            hybrid = [donor[i] if i == mode else row for i, row in enumerate(parent)]
            if hybrid not in challengers:
                challengers.append(hybrid)
            winner = max(challengers, key=lambda w: score(evaluate(w, archive, seeds)))
            if winner is hybrid:
                crossover_wins[MODES[mode]] += 1
                print(f"{name} retained {donor_name}'s {MODES[mode]} rule", flush=True)
            evolved[name] = winner
        archive.extend(evolved.values())
        current = evolved
        print(f"Generation {generation + 1}/{args.generations} completed", flush=True)
    # Use disjoint seeds and a common league for comparison across all candidates.
    validation = rng.sample(range(1_000_000, 2_000_000), 18)
    league = list(STARTERS.values()) + list(current.values())
    candidates = {**current, **{f"{name}_initial": w for name, w in STARTERS.items()}}
    results = {name: evaluate(weights, league, validation) for name, weights in candidates.items()}
    challengers = [name for name, weights in candidates.items() if weights != incumbent]
    challenger = max(challengers, key=lambda name: score(results[name]))
    champion = challenger
    promotion = None
    if incumbent is not None:
        baseline_validation = evaluate(incumbent, league, validation)
        promising = [name for name in challengers
                     if results[name]["free_for_all"] >= baseline_validation["free_for_all"]
                     and results[name]["squad"] > baseline_validation["squad"]]
        if promising:
            challenger = max(promising, key=lambda name: (results[name]["squad"], score(results[name])))
        champion = challenger
        gate_seeds = rng.sample(range(2_000_000, 3_000_000), 36)
        # Match the incumbent and challenger against identical lineups and seeds.
        baseline = evaluate(incumbent, league, gate_seeds)
        trial = evaluate(candidates[challenger], league, gate_seeds)
        promoted = (trial["squad"] > baseline["squad"]
                    and trial["free_for_all"] >= baseline["free_for_all"]
                    and score(trial) > score(baseline) + .10)
        promotion = {"challenger": challenger, "incumbent": prior["champion"],
                     "incumbent_metrics": baseline, "challenger_metrics": trial,
                     "promoted": promoted, "seeds": gate_seeds}
        if not promoted:
            champion = next((name for name, weights in candidates.items() if weights == incumbent),
                            "incumbent")
            candidates[champion] = incumbent
            if champion == "incumbent":
                results[champion] = evaluate(incumbent, league, validation)
    total = args.generations + (prior.get("total_generations", prior["generations"]) if prior else 0)
    report = {"seed": args.seed, "generations": args.generations, "total_generations": total,
              "validation_seeds": validation, "champion": champion, "policies": candidates,
              "validation": results, "promotion": promotion,
              "crossover_wins": crossover_wins}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"champion": champion, "validation": results, "promotion": promotion,
              "crossover_wins": crossover_wins}, indent=2))


if __name__ == "__main__":
    main()
