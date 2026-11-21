# Mech Royale Policy League · Builders Mech Royale

A placement-first Python bot for the 16-mech Builders Mech Royale. It uses a trained tactical model to choose when to evade, scavenge, engage, or hold. An independent rule-based controller turns that choice into movement, aiming, firing, and upgrade decisions. It also handles urgent zone re-entry and low-health retreat.

## Competition runtime

The root Dockerfile starts Naruto through `bob_mix.py` as uid 65534. Sasuke handles practice and free-for-all decisions, with Sakura as a fallback and the existing Tsunade escape and Madara duel overrides. The Go controller incorporates [waiprotein’s MIT-licensed changes](https://github.com/waiprotein/mech-royale-bot/tree/6dcaf14cb7410497f8a195d68c18a3b56730969f) to the Giddy bot. Training checkpoints under `models/` are available for simulator work.

## What each file does

| File | Purpose |
| --- | --- |
| `Dockerfile` | Builds the Go controller and starts the Python Naruto wrapper as uid 65534. |
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

## Map-backed self-play and practice calibration

Use the fast simulator for broad policy search, then run a shortlist through the production controller on the official terrain. The second simulator uses the kit's match configuration, map and shrinking-zone phases. It approximates terrain movement, line of sight, gun projectiles, cube pickups, health, and boost energy. Its squad fields four easy, four medium, four hard, and three expert proxies that share sightings and escalate pressure. Seeds rotate between pursuit, flank, and zone-oriented squads. Free-for-all uses 16 instances of the actual bot, with opponents drawn from a fixed policy pool.

```sh
python3 arena_sim.py --kit "$KIT" --policy scavenger_initial --policy duelist   --seeds 6 --mutations 6 --output arena-report.json
```

The command compares every candidate with the checkpoint default on the same seeds using both simulators. `--mutations` adds up to two candidates from a separate screening set. Arena eligibility requires higher squad placement, no free-for-all drop, and the existing combined-score improvement in the fast simulator. It writes results and candidate weights for review. Use `--format squad` to investigate squad losses in isolation. Reports include per-seed placement, survival, zone and combat deaths, damage by source, stalled movement, time under pressure, shots, hits, and misses.

The simulator approximates airborne movement, jet energy, secondary weapons and upgrade effects using kit settings. Private house behavior, spawn distributions, collision physics and visibility remain approximate. The default decision step is 0.5 s. Use `--step 0.1` for competition decision timing. Map simulation uses a fixed A* expansion budget per slice for reproducible comparisons. The live controller uses its wall-clock budget. Shortened runs via `--duration` are diagnostics.

Resumed training first applies the paired fast promotion gate. A candidate passing that gate must also improve mean squad placement and maintain free-for-all placement against the same frozen opponent pool in full-controller matches at 0.1 s. Supply `--arena-kit PATH` if the kit is outside the sibling mechfight-kit directory. Missing kit data blocks promotion. The map gate tests three shared seeds across three house profiles and extends to six unless a format falls behind by more than one point. Checkpoint promotion metadata records the pair results.

`python3 arena_trials.py --kit ../mechfight-kit --checkpoint models/trained.json --seed 12345 --output trial.json` tests earlier zone rotation with identical weights. Use a fresh seed for confirmation. The experimental controller is isolated from the runtime default.

After you have official squad practice results for the exact policies in the report, record the policy name, tested Git commit, and score in JSON:

```json
[
  {"policy": "incumbent", "commit": "<tested commit>", "practice_points": 0.0}
]
```

Replace the example score with the actual result. Compare the measured points against both local simulators with:

```sh
python3 arena_calibrate.py --arena-report arena-report.json --official official-results.json
```

The comparison reports mean absolute error and pairwise rank agreement. It needs at least two models with distinct scores before it can assess ordering. Only the official practice scorer can establish leaderboard placement.

The [first map-backed benchmark](models/arena-benchmark.json) compared the generation 672 default with Initial Scavenger on three shared seeds at a 1 s decision step. The default led squad placement (2.67 vs. 1.67), and Initial Scavenger led free-for-all (9.00 vs. 6.67). Two of the default's three squad losses came from zone damage. This small diagnostic run did not produce an eligible replacement.

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

### Learning from crossover results

Training checkpoints record cumulative attempt and win counts for each crossover rule. New batches sample rules that have helped more often, with exploration for all four rules. A separate [squad refinement trial](models/squad-refine.json) screened 64 distinct policies and evaluated its finalists on 64 fresh seeds. The best squad policy reached 0.72 versus the frozen default's 0.41, with free-for-all falling from 11.03 to 5.39. Every candidate failed at least one promotion requirement. The saved candidates are available for subsequent evaluation.


### Generation 672 checkpoint

The latest simulator batch led with Initial Duelist in squad (0.61) and Evolved Duelist in free-for-all (9.50). The prior default stayed in place after the matched promotion run. Across saved continuous batches, the highest observed squad mean was 0.94 for Initial Scavenger at generation 336. The highest observed free-for-all mean was 11.61 for the retained incumbent at generation 658. Those runs had different seeds, and the final comparison will use common fresh seeds.

The [archive](models/history-0337-through-0672.json) contains 72 additional distinct matrices. Crossover search recorded 15 wins in 60 evade attempts, 17 in 71 scavenge attempts, 13 in 69 hold attempts, and 2 in 47 engage attempts. The latest default passed all 300 official host replay observations with p99 4.6 ms. These replay results check timing and conformance. Placement figures are from the local simulator.

The trainer also tries a mutation confined to one tactical rule each generation. After a failed paired promotion test, it records the squad, free-for-all, and combined score differences. For the next three batches, distinct challengers get priority over the rejected weights while the same default is active. Archived weights remain available for the final common-seed comparison.

### Check whether the fast simulator overlooks candidates

Run `python3 screen_audit.py --archive ../mech-continuous --kit ../mechfight-kit --seed UNIQUE_INTEGER --output models/screen-audit.json` alongside training. Use a new output path for each audit. It freezes the most recent archived incumbent and league, selects four distinct previously rejected policies, then measures both simulators on six shared seeds with 0.1-second map decisions. The report stores weights, archived generations, per-match map results and squad rankings. Any rejected policy that improves map squad placement and maintains map free-for-all gets six additional paired seeds against the same incumbent. The audit only writes its own report.

### Diagnose a squad and free-for-all tradeoff

Run `python3 tactic_audit.py --audit models/screen-audit.json --kit ../mechfight-kit --candidate clear_rejection --seed UNIQUE_INTEGER --output models/tactic-audit.json`. The audit adds each changed tactic row to the incumbent and independently reverts that row in the challenger. Every variant uses six shared seeds at 0.1-second decisions with the original audit league. Compare per-seed placement changes with survival time, zone damage, combat damage, pressure and blocked movement. A gain in the add test and a loss in the revert test support attribution to that row. A mixed result can indicate interactions or seed sensitivity. Repeat with a new seed to confirm.

### Generation 1456 checkpoint

The incumbent led the latest fast validation batch with 0.44 mean squad placement points and 11.17 free-for-all points. In the separate 36-seed promotion check, Initial Guardian gained free-for-all points but fell from 0.61 to 0.14 in squad, so the incumbent retained the default. The historical archive includes the distinct matrices added since generation 1302. These are simulator scores.

### Pressure-triggered retreat experiment

Run `python3 arena_trials.py --kit ../mechfight-kit --checkpoint models/trained.json --seed UNIQUE_INTEGER --controller pressure-retreat --output pressure-retreat.json`. This challenger temporarily chooses evade after recent damage when at least two nearby opponents are visible. The benchmark uses identical weights, opponent pools and map seeds for the baseline and challenger. Zone routing still takes priority. This experimental controller is selected only by the benchmark.

### Generation 1974 checkpoint

In fast validation, the incumbent led squad with 0.61 mean placement points and trained Scavenger led free-for-all with 9.50. Over 36 paired promotion seeds, Scavenger scored 0.19 squad and 9.75 free-for-all against the incumbent's 0.50 and 9.58. The squad regression kept the incumbent as the default. The archive covers distinct policy matrices first observed after generation 1820. All placement scores are simulator results.

### Controller trials

Use `arena_trials.py --controller NAME` with a frozen checkpoint and shared seed. The experimental names are `route-timed`, `quiet-recovery`, `goal-braking`, `remembered-cover`, `energy-reserve`, `opening-pursuit`, and `early-loot`. Use 0.5-second decisions to screen and 0.1-second decisions on fresh seeds to confirm. Each paired run records placement, survival time, and simulator damage and movement diagnostics.

Every fourth 14-generation resume batch runs one rotating controller benchmark after saving the weight checkpoint. Format adaptation, health-based recovery, and armed recovery face the saved champion on paired map seeds at 0.1-second decisions. Reports go under `models/controller-trials/`. This comparison can extend batch time and leaves weight promotion to the existing paired gate.

### Generation 3024 checkpoint

The archive preserves 114 distinct matrices first observed after generation 2492. Guardian gained 0.44 free-for-all points but lost 0.08 squad points in paired fast simulation, so Initial Scavenger remained the default. In a separate six-seed map-backed Naruto screen, the no-switch practice driver led free-for-all with 8.83 points. All handoff thresholds tied in squad at 2.83 points. These are simulator scores.

### Generation 3066 checkpoint

The paired fast simulator gate retained Initial Scavenger. The archived holdout uses three matched map seeds at 0.1-second decisions. The no-switch adapter averaged 5.33 squad and 9.00 free-for-all. The two-elimination handoff averaged 4.67 squad and 6.33 free-for-all. Neither switched in squad, and unequal response-time fallbacks limit interpretation of the squad result. These are simulator findings.

### Generation 3136 checkpoint

Initial Scavenger retained the default. On paired fast promotion seeds, trained Scavenger gained 1.97 free-for-all points and lost 0.08 squad points. The archive preserves 15 newly observed policy matrices. All scores are from simulation.

### Generation 3192 checkpoint

The archive adds 12 distinct matrices. On 18 fast simulator seeds, trained Scavenger led the trained policies in squad placement (0.67) and Guardian led free-for-all (9.22). On 36 paired promotion seeds, Scavenger gained 1.08 free-for-all points but lost 0.03 squad points against Initial Scavenger. Initial Scavenger remains the default.

Madara's no-switch free-for-all trial used three paired map seeds at 0.1-second decisions. Both variants averaged 15.00 points and had zero timeouts. The override never activated, so the trial establishes no benefit from the override. See `models/naruto-madara-noswitch.json` for the run data.

### Generation 3276 checkpoint

This update archives 18 distinct policy matrices. On the paired fast simulator promotion seeds, Guardian changed squad placement by -0.33 points and free-for-all placement by +1.47 points. The active fast trainer policy is Scavenger Initial.

The format-adaptive controller scored 2.33 squad and 2.33 free-for-all in the paired map-backed benchmark, compared with 2.67 and 6.67 for the baseline across three seeds at 0.1-second decisions. The controller failed the paired gate. This benchmark uses the original Python controller and is separate from the Naruto handoff comparison.

The official kit replay of the generation 3276 Docker entrypoint failed before `ready`. The command invokes the `mechfight` library as a CLI, so it exits before starting the controller. This checkpoint is a simulator training artifact and requires a working match entrypoint and a successful kit replay before manual testing.

### Generation 3304 checkpoint

This update archives 6 distinct policy matrices. On the paired fast simulator promotion seeds, Guardian changed squad placement by -0.19 points and free-for-all placement by +1.61 points. The active fast trainer policy is Scavenger Initial.

On the 18-seed fast validation set, Initial Scavenger led squad placement at 0.39 and trained Guardian led free-for-all at 7.78. The interrupted route-timed rotation benchmark tied the baseline on all five recorded squad matches and all four recorded free-for-all matches. The partial run did not clear the paired promotion gate.
