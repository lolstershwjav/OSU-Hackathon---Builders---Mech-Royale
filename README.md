# Mech Royale Policy League · Builders Mech Royale

A placement-first Python bot for the 16-mech Builders Mech Royale. It uses a trained tactical model to choose when to evade, scavenge, engage, or hold. An independent rule-based controller turns that choice into movement, aiming, firing, and upgrade decisions. It also handles urgent zone re-entry and low-health retreat.

## What each file does

| File | Purpose |
| --- | --- |
| `Dockerfile` | Root competition entry point. Starts the bot as uid 65534 using a pinned Python patch version and standard-library dependencies only. |
| `bot.py` | Loads the selected weights and handles candidate movement scoring, projectile avoidance, targeting, and weapon timing. |
| `mechfight.py` | Official starter kit wrapper for the mechfight/1 JSON-lines protocol. |
| `policy.py` | Defines the seven input features and four tactical modes shared by the live bot and trainer. |
| `train.py` | Evolves three model families in seeded 16-mech simulated matches. |
| `models/trained.json` | Contains the initial and evolved weights, validation seeds and scores, and the default policy name. |
| `oval-survivor/` | Archived Oval Survivor test. The root bot and image do not use it. |

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

## Latest run

Seed 2027, six local CPU generations, 18 validation matches per format for each candidate. Placement is mean `16 - place`.

| Policy | Free-for-all | Squad | Squad survival rounds |
| --- | ---: | ---: | ---: |
| guardian_initial | 10.72 | 0.17 | 37.33 |
| scavenger | 8.78 | 0.11 | 35.56 |
| scavenger_initial | 8.17 | 0.28 | 36.44 |
| guardian | 7.06 | 0.33 | 35.72 |
| duelist | 6.67 | 0.17 | 36.33 |
| duelist_initial | 5.83 | 0.17 | 35.28 |

`guardian_initial` had the highest combined validation score and is the default. Evolved Guardian had the highest squad placement. The official host replay passed 300/300 observations with zero late responses. Docker and official scored matches remain unverified.
