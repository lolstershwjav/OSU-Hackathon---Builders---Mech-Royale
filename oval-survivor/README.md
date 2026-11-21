# Oval Survivor v1 — Mech Royale

A deterministic, standard-library Python agent focused on **placement** in Builders Mech Royale.

## Features
- Low-latency decision-making (<2ms max in a local 300-observation replay)
- Safely routes inside the next zone, with zone pressure overrides
- Values health regeneration and fleeing dangerous fights
- Collects nearby cubes, weapons and uncontested drops
- Projectile evasive steering and static building avoidance
- Leads moving targets, operates gun/lightning/laser/mortar
- Picks survival-oriented upgrades

## Run conformance check
From the extracted official `mechfight-kit` directory:

```bash
python3 check/check.py -- python3 -u /path/to/oval-survivor/example_bot.py
```

Or test the container from the kit directory after building from this repo:

```bash
docker build -t oval-survivor /path/to/oval-survivor
python3 check/check.py --map-dir /arena -- docker run --rm -i --network none --cpus 1 --memory 512m --read-only --tmpfs /tmp:rw,size=64m --user 65534:65534 -v "$PWD/arena:/arena:ro" oval-survivor
```

## Submit
Push these files **at the root** of a public GitHub repository and submit the repository URL on the Builders Mech Royale page. `Dockerfile` must remain in the repository root.

## Validation note
Official local replay check: **PASS, 300/300 decisions, no late answers** (tested on host, not Docker). This is **not** a scoring benchmark and is not a promise of leaderboard rank; leaderboard practice matches must measure competitive strength.
