# Validator guide

Validity's local RN validator allocates the same cases to every miner, verifies source-backed responses, persists numerical scores, and submits weights from completed rounds. The [verification report](localnet-implementation.md) records the live evidence.

## Start

From the subnet repository root:

```sh
localnet/run-in-tmux.sh
uv run --frozen --project miner python localnet/mvp.py verify --version v1 --timeout 600
```

Set the fictional source to v1 first if a previous run selected v2. See the [localnet guide](../localnet/README.md) for prerequisites, source transitions, restarts and chain resets.

## Run the validator separately

Start and register miners using the [manual startup instructions](../localnet/README.md#manual-startup). Run one validator per database. Then:

```sh
cd validator
uv sync --frozen
uv run --frozen validator --env-file ../localnet/.env
```

`VALIDATOR_MODE=local_credentials` enables RN evaluation. `VALIDATOR_MODE=ping` selects the echo baseline.

| Setting | Default / meaning |
| --- | --- |
| `NETUID` / `VALIDATOR_NETUID` | Required; local harness uses 2 |
| `SUBNET_TEMPO` / `VALIDATOR_TEMPO` | 360 blocks; must match the sidecar and chain |
| `VALIDATOR_FIXTURE_DIR` | `../localnet/fixtures` |
| `VALIDATOR_LEDGER_PATH` | `../localnet/state/credentials.sqlite3` |
| `VALIDATOR_TOTAL_PROCESSING_TIMEOUT` | Ten seconds |
| `VALIDATOR_MAX_IN_FLIGHT` | 4 |
| `VALIDATOR_MAX_SCORE_AGE` | Ten minutes; ISO 8601 value `PT10M` |
| `VALIDATOR_CALLBACK_HOST` / `VALIDATOR_CALLBACK_PORT` | `127.0.0.1` / `8001` |

## Scores and persistence

See the [adopted policy](../subnet_design.md). Every round pins its source catalog and hotkey/UID roster. Remote failures count as zero. Validator interruptions are retried without counting the interrupted attempt. Source or registration changes void unfinished rounds. Legacy conformance records are preserved outside score windows.

Epoch proposals remain fixed across retries. Source, age and current registrations are checked before handing weights to the sidecar. Queue acknowledgements and independent chain confirmations are stored separately. A pause prevents new submissions; prior on-chain weights can remain.

Recompute a completed round from saved observations:

```sh
uv run --frozen --project validator python -m validator.credentials.audit localnet/state/credentials.sqlite3 1
```

Run that command from the repository root and replace `1` with a completed round ID. A changed score summary, incomplete round or inconsistent assignment fails replay.

## Signed score publication

Optional reporting runs independently of evaluation and weight setting. Each validator signs completed-round
summaries with its own hotkey and uploads directly to Hippius using its team's dedicated ACL credentials.
Each validator's private outbox keeps its original signed bytes and retry state across restarts. Its ledger
must remain available even after reports are uploaded. Production validators run independently on separate
operator machines. SQLite is never shared between validators or queried by the combined leaderboard.
See [score reporting](score-reporting.md) for configuration and storage ownership.

Dedicated ACL user tokens were confirmed with the Hippius team. The three test tokens have bucket-wide
WRITE access to `localnet`. Put their named profiles in the private, Git-ignored
`localnet/hippius-credentials.json`, following the
[example format](../localnet/hippius-credentials.example.json). GET/LIST checks have passed for all three
profiles; the user approved `owner` for the third test validator. Use
`VALIDATOR_REPORTS_BACKEND=hippius` for real integration tests. The explicit `gateway` backend is only for
development transport tests. Each destination has separate delivery status, so a gateway receipt never
satisfies Hippius delivery. WRITE-only tokens acknowledge uploads; a reader with READ permission verifies
stored signatures later. Three live localnet validators have passed real bucket readback and independent
chain-weight checks; see the [verification results](localnet-implementation.md#three-validator-hippius-verification--2026-09-16).

## Observability

Structured logs and OpenTelemetry lifecycle counters and operation-duration histograms cover evaluation and weight attempts. Tracing exports only when configured. No metrics exporter or external telemetry service is required by the local harness. Deadlines are checked when the observation actor processes a response, so processing delay is included.

Reporting adds `validity.report.events` and `validity.report.operation.duration`, with upload, read and
publisher outcomes. Publisher logs include report identifiers and error types, without storage secrets or
private key material. No metrics exporter is enabled by the reporting service by default.

## Quality checks

Inside `validator/`:

```sh
uv run --frozen ruff check
uv run --frozen ruff format --check
uv run --frozen basedpyright
uv run --frozen pytest -q --tb=line -r f
```

## Deployment status

This is a fictional-source local MVP. Public-network authentication, real nursing-source adapters and production reward policy remain to be qualified. The retained [installer](../installer/README.md) is not a production release; its image digest is still a placeholder.
