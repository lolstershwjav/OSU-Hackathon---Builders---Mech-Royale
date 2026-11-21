# Mech Royale Policy League · Builders Mech Royale

A placement-first Python bot for the 16-mech Builders Mech Royale. It uses a trained tactical model to choose when to evade, scavenge, engage, or hold. An independent rule-based controller turns that choice into movement, aiming, firing, and upgrade decisions. It also handles urgent zone re-entry and low-health retreat.

## What each file does

| File | Purpose |
| --- | --- |
| `Dockerfile` | Root competition entry point. Starts the bot as uid 65534 using a pinned Python patch version and standard-library dependencies only. |
| `bot.py` | Loads tactical weights, chooses objectives, and handles local movement and combat. |
| `navigation.py` | Reads the official arena, caches traversable surfaces and edges, and plans routes with bounded A*. |
| `check_navigation.py` | Checks geometry and benchmarks a blocked route on the official arena. |
| `mechfight.py` | Official starter kit wrapper for the mechfight/1 JSON-lines protocol. |
| `policy.py` | Defines the seven input features and four tactical modes shared by the live bot and trainer. |
| `train.py` | Evolves three model families in seeded 16-mech simulated matches. |
| `models/trained.json` | Contains the initial and evolved weights, validation seeds and scores, and the default policy name. |
| `oval-survivor/` | Archived Oval Survivor test. The root bot and image do not use it. |

## Hybrid navigation

1. **Tactical policy.** Guardian, Scavenger, and Duelist still choose whether to evade, scavenge, engage, or hold. The controller maps that decision to an objective and can prioritize urgent zone re-entry or take nearby cover.
2. **Global A*.** The planner reads `v2/world.json` and `v2/terrain.i16` from the official arena mount. It builds walkable surfaces on demand at 6 m spacing, using the current mech cylinder, bilinear terrain heights, polygon footprints and holes, and bridge clearance. Route costs account for the predicted zone at arrival and nearby exposure to visible enemies. It reuses paths, checks shortcuts with swept terrain and clearance samples, and replans when the objective or zone changes materially, threats shift, or movement stalls.
3. **Local utility.** The waypoint guides the existing 16-direction controller. Projectile avoidance and local zone safety can steer away from it immediately. Directions that fail a short terrain and clearance check are excluded, and jump recovery is available when stalled.

A* searches in short slices across observations. The grid uses sampled walking links and supports stacked floors where they connect within step and slope limits. Air movement uses local steering. Narrow passages below grid resolution can be missed. If arena files are unavailable, the controller falls back to local movement. Set `MECH_NAV=local` for a controlled comparison using the same policy.

From this checkout, with `KIT` pointing to the extracted official starter kit:

```sh
python3 check_navigation.py "$KIT/arena"
python3 "$KIT/check/check.py" -- python3 -u bot.py
MECH_NAV=local python3 "$KIT/check/check.py" -- python3 -u bot.py
```

The arena benchmark found a 58.1 m walkable route for a blocked 37.4 m direct path. The hybrid and local-only variants each answered 300/300 official replay observations on time. Measured host p50/p99 were 0.5/5.2 ms for hybrid and 0.2/0.5 ms for local-only. Continuous self-play still trains the tactical policies in the 2D simulator. That simulator does not model the 3D navigation upgrade. Actual placement impact and the Docker build remain unverified.

## Train and compare policies

```sh
python3 train.py --seed 2027 --generations 6
```

The three families start with different priorities. **Guardian** starts with a preference for evasion, **Scavenger** prioritizes loot, and **Duelist** is more willing to fight. Each generation runs the parent and three mutations per family against a pool containing the original policies and winners from earlier generations. Selection weights squad placement at 50%, free-for-all placement at 40%, and normalized squad survival time at 10%. After training, the script compares initial and evolved policies against a common pool on 18 seeds excluded from evolution. The highest validation score becomes the default, so an evolved policy can lose its spot to an initial one.

The simulator uses 2D movement, shrinking circles, abstract damage, and cube pickups. Its squad shares the player's position and adds hunters over time. Buildings, projectiles, weapon-specific combat, and the official opponents are outside this simulation. Scores estimate performance in this simulator only. The bot loads fixed weights at match start. Run training again offline to generate a new checkpoint.

Set `MECH_POLICY=guardian`, `scavenger`, or `duelist` to try an evolved variant. Append `_initial` to try its starter. Leave it unset to use the selected default.

## Test and submit

From the extracted official starter kit, set `REPO` to the path of this checkout:

```sh
python3 check/check.py -- python3 -u "$REPO/bot.py"
docker build --platform linux/amd64 -t oval-hybrid "$REPO"
python3 check/check.py --map-dir /arena -- docker run --rm -i \
  --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m \
  --user 65534:65534 -v "$PWD/arena:/arena:ro" oval-hybrid
```

Submit this public repository URL and the desired commit SHA on the Mech Royale track page. The official replay check validates message timing and formatting. The track's 48-match practice run measures official placement.

## Overnight training

Resume from the latest checkpoint with a new seed:

```sh
python3 train.py --resume models/trained.json --seed 20261121 --generations 14
```

Every generation tests mutations at one of three scales and tries borrowing one tactical scoring rule from another family. The borrowed rule survives only when its hybrid wins that family's evaluation. Validation uses mixed lineups and a common seed set. The leading candidate then faces the previous default on 36 separate seeds with identical opponent lineups and spawn slots in each comparison. Promotion requires higher squad placement, at least the incumbent's free-for-all placement, and a gain greater than 0.10 on the combined metric. The checkpoint records both sides of the comparison, the number of crossover wins by tactical mode, and the cumulative generation count.

A spatial index now limits obstacle checks to relevant map cells and the controller reuses distance calculations within a decision. With identical model weights, all 300 replay actions matched the previous controller. Median in-process time was 0.0281 ms per observation, down from 0.0458 ms across 15 runs per controller. Host measurements exclude process I/O and official container scheduling.

## Latest run

28 cumulative generations. The five-generation crossover batch kept Guardian’s scavenge and evade rules in Duelist during training. Each policy below was evaluated in 18 matches per format.

| Policy | Free-for-all placement points | Squad placement points | Squad survival rounds |
| --- | ---: | ---: | ---: |
| guardian | 8.39 | 0.50 | 43.11 |
| scavenger | 5.72 | 0.17 | 40.17 |
| duelist | 8.33 | 0.50 | 43.11 |
| guardian_initial | 8.22 | 0.22 | 43.22 |
| scavenger_initial | 8.06 | 0.50 | 43.89 |
| duelist_initial | 4.78 | 0.67 | 40.78 |

Default after the paired promotion test: `guardian_initial`. The challenger was `guardian`. Paired metrics are in the checkpoint. These are local simulator placement scores.

The current default passed the official host replay checker with 300/300 on-time responses. Docker and official scored matches remain unverified.

### Continuous training checkpoint

At 42 cumulative generations, the default policy is `guardian_initial`. On the latest simulator validation seeds, `duelist_initial` led squad placement with 0.56 points, and `guardian` led free-for-all with 10.61 points. The checkpoint records the paired promotion decision.

### Generation 252 checkpoint

The latest simulator batch favored Initial Duelist in squad play (0.72 points) and Evolved Scavenger in free-for-all (8.78). Evolved Scavenger outscored the default in the paired squad comparison (0.36 vs. 0.11) and trailed in paired free-for-all (7.67 vs. 9.33). Initial Guardian remains the default under the combined promotion rule. An [archive](models/history-through-0252.json) preserves 51 distinct matrices from the continuous batches. These scores are from the 2D simulator. The official host replay checker passed 300/300 on-time actions for Initial Duelist, Evolved Guardian, Evolved Scavenger, and the default with the hybrid navigator.

### Generation 336 checkpoint

Default policy: `duelist`. The batch's simulator leaders were `scavenger_initial` in squad (0.94) and `duelist` in free-for-all (10.67). The [policy archive](models/history-through-0336.json) contains 69 distinct weight matrices.

### Squad-focused trial

Four targeted weight changes tested favorable engagements, retreat when wounded, safe looting, and circle positioning. In the [48-seed holdout](models/squad-experiment.json), Initial Duelist improved squad placement from 0.31 to 0.65 but reduced free-for-all from 10.73 to 4.44. None of the finalists satisfied the new promotion criteria. These scores come from the local 2D simulator.
