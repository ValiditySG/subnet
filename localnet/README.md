# Validity localnet

A real local chain, eight fictional RN miner profiles, and the Validity validator exercise assignment, evidence verification, scoring, weight submission and recovery. The target product is RN verification for travel nursing agencies; agency workflows follow the subnet loop.

## Start

Requires Docker with Compose v2, uv, Python 3.14 and tmux. From the subnet repository root:

```sh
localnet/run-in-tmux.sh
```

The runner installs both frozen Python environments, starts the chain and sidecar, bootstraps subnet 2, registers the eight profiles sequentially, checks each advertised endpoint, refreshes discovery and starts the validator. First startup takes several minutes. A `localnet` tmux session must not already exist.

Detach with `Ctrl-b d`, then reattach with `tmux attach -t localnet`. Window `main` contains the honest miner and validator; the other windows contain the remaining profiles. Wallets use local development funds and live under `localnet/wallets/`.

## Verify scores and chain weights

```sh
uv run --frozen --project miner python localnet/mvp.py source v1
uv run --frozen --project miner python localnet/mvp.py verify --version v1 --report baseline --timeout 600
uv run --frozen --project miner python localnet/mvp.py source v2
uv run --frozen --project miner python localnet/mvp.py verify --version v2 --report transition --timeout 600
```

Each round contains five cases for every registered local miner. The checker requires the eight default profiles, their independently calculated expected scores, complete case coverage, the same live hotkey/UID mapping, a validator permit and a matching weight row read directly from subtensor. It checks the chain's last-update block and allows only u16 quantization tolerance.

| Profile | v1 score | v2 score | Behavior |
| --- | ---: | ---: | --- |
| honest | 1 | 1 | Current source and correct evidence |
| slow | 1 | 1 | Correct, with a 0.5-second delay |
| duplicate | 1 | 1 | Posts each callback twice; scores once |
| stale | 1 | −1 | Always reads v1 |
| unsafe | −0.2 | −0.2 | Asserts active for suspended records |
| malformed | 0 | 0 | Omits the required check |
| timeout | 0 | 0 | Acknowledges but never responds |
| late | 0 | 0 | Responds after the ten-second deadline |

In v1, the four positive profiles each receive 25%. In v2, honest, slow and duplicate each receive one third; stale receives zero. There is no speed bonus. The [scoring policy](../subnet_design.md#scoring-policy-rn-equal-cases-v1) defines failure attribution and paused submissions.

The v2 source changes active/suspended findings, restores an unavailable lookup and introduces another outage. Board files and expected labels are maintained separately. The atomic, gitignored `fixtures/active-version` pointer selects them; it defaults to v1 when absent. See the [wire contract](../protocol/localnet-v1/README.md).

## Verify restart recovery

While the loop is running:

```sh
uv run --frozen --project miner python localnet/mvp.py restart-validator
```

This replaces only the validator pane, checks that the latest 100 terminal results remain byte-identical, and requires at least one in-flight assignment to become interrupted. It saves `state/restart-report.json`. Interrupted round slots are retried with new IDs. Completed slots stay closed.

Use the reported `round_before_restart` as `--after-round` to require a later round and a new chain confirmation:

```sh
uv run --frozen --project miner python localnet/mvp.py verify --version v2 --report recovery --after-round 3 --timeout 600
```

Replace `3` with the actual reported number. Replay any completed round independently, using its actual ID:

```sh
uv run --frozen --project validator python -m validator.credentials.audit localnet/state/credentials.sqlite3 1
```

Exercise miner process loss and recovery:

```sh
uv run --frozen --project miner python localnet/mvp.py restart-miner slow
```

This stops the miner, waits for a newly assigned task to fail, restarts and checks its advertised endpoint, refreshes sidecar discovery, and requires a verified response. It saves `state/miner-restart-report.json`. Use its `round_at_recovery` as `--after-round` for a subsequent clean-round chain check; the round containing the outage correctly retains the failed task.

## Configuration and state

`localnet/.env` is copied from `.env.example` on first startup. Paths resolve from `validator/`. Keep netuid 2 and the same tempo across chain, sidecar and validator. Default task deadline: ten seconds; maximum outstanding tasks: four; maximum eligible score age: ten minutes.

- `state/credentials.sqlite3`: original assignments, round slots, frozen scores and weight batches.
- `state/baseline.json`, `transition.json`, `recovery.json`: direct-chain acceptance reports.
- `state/restart-report.json`: process-recovery observations.
- `logs/validator.log`, `logs/miner.log`, `logs/miner-<profile>.log`: current process output.
- `wallets/`: local wallet keys.

These generated files are gitignored. Use one validator per database. The callback binds `127.0.0.1:8001`; miner endpoints use `127.0.0.2`. The local chain rejects `127.0.0.1` as a published miner address.

A sidecar acknowledgement records queueing only. The checker records `confirmed_block` after direct-chain readback. Empty, stale or context-invalid score windows pause new submissions; old on-chain weights may remain. Source transitions can invalidate one unfinished round before a new one completes.

## Restart or stop

Restart all host processes while keeping the chain and ledger:

```sh
tmux kill-session -t localnet
localnet/run-in-tmux.sh
```

Stop the environment:

```sh
localnet/stop-tmux.sh
```

Stopping also removes the chain container. It has no persistent chain volume. Archive `localnet/state/` before starting a fresh chain so scores and epoch receipts from different chain runs cannot mix. Wallet files survive and will be registered again.

## Manual startup

```sh
uv sync --frozen --directory validator
uv sync --frozen --directory miner
docker compose -f localnet/compose.yml --env-file localnet/.env up -d --wait
uv run --frozen --project miner python localnet/bootstrap.py
uv run --frozen --project miner python localnet/miners/miner-honest.py
```

Run the remaining profile scripts in separate terminals for the full acceptance suite. For each profile, check readiness with `localnet/check.py wait-miner --profile <name>`, through the same frozen miner environment. Refresh discovery after registration or endpoint changes, then start the validator:

```sh
docker compose -f localnet/compose.yml --env-file localnet/.env restart pylon
cd validator
uv run --frozen validator --env-file ../localnet/.env
```

The validator can evaluate a smaller local roster; the eight-profile acceptance checker deliberately requires the full suite. The original echo miner remains available with `VALIDATOR_MODE=ping`.

## Quality checks

See [validator checks](../docs/validator.md#quality-checks) and [fixture checks](../docs/miner.md#quality-checks). No external nursing source or telemetry service is needed. Metrics instruments cover credential lifecycle events and operation duration; no metrics exporter is configured by default.
