# Miner guide

Validity miners return RN credential findings with source evidence. The local MVP provides eight independently registered fixture profiles under [`localnet/miners/`](../localnet/miners/), with shared local transport and wallet setup in `fixture_runtime.py`.

The fixture resolver reads board records independently of the validator's answer catalog and scoring code. All identifiers are fictional. Real-source miner integrations follow qualification of nursing-board access.

## Start

Run the complete environment from the subnet repository root:

```sh
localnet/run-in-tmux.sh
```

Or, after [manual chain setup](../localnet/README.md#manual-startup), start a single fixture:

```sh
uv run --frozen --project miner python localnet/miners/miner-honest.py -n 1
uv run --frozen --project miner python localnet/check.py wait-miner --profile honest
```

Profiles are `honest`, `slow`, `stale`, `unsafe`, `malformed`, `timeout`, `duplicate` and `late`. Each has a corresponding `miner-<profile>.py` entry point and supports `-n`. The full acceptance suite expects one instance of each. See [profile expectations](../localnet/README.md#verify-scores-and-chain-weights).

Fixtures use development wallets under `localnet/wallets/`, connect to `ws://127.0.0.1:9944` on subnet 2 and advertise HTTP endpoints on `127.0.0.2`. Stop an instance before restarting the same wallet. Refresh discovery after changing endpoints:

```sh
docker compose -f localnet/compose.yml --env-file localnet/.env restart pylon
```

## Requests and evidence

- `POST /task` acknowledges with HTTP 202 and sends a result to the supplied local `/callback` endpoint.
- `GET /health` reports `validity-<profile>-v1`.
- The request requires profession RN, jurisdiction `ZZ-TEST`, a fictional license number and exactly one `fixture_license` check.
- Available evidence names its source version, snapshot SHA-256 and record IDs. Unavailable results carry no finding or evidence.
- Correct profiles read the currently selected board version. The stale profile always reads v1. Expected labels are never used by the resolver.

The [wire contract](../protocol/localnet-v1/README.md) describes identity, evidence and deadline semantics. Transport is deliberately restricted to local callbacks and is not authenticated for public deployment.

## Echo baseline

The independent `miner/` project supplies frozen fixture dependencies and retains its original echo server. Stop the RN fixtures and pair `uv run --frozen miner -n 1` inside `miner/` with a validator started using `VALIDATOR_MODE=ping`. The echo miner and honest RN fixture use the same local wallet name.

## Quality checks

Inside `miner/`:

```sh
uv run --frozen ruff check ../localnet/miners/fixture_runtime.py ../localnet/miners/miner-*.py ../localnet/check.py ../localnet/mvp.py
uv run --frozen ruff format --check ../localnet/miners/fixture_runtime.py ../localnet/miners/miner-*.py ../localnet/check.py ../localnet/mvp.py
uv run --frozen basedpyright ../localnet/miners/fixture_runtime.py ../localnet/miners/miner-*.py ../localnet/check.py ../localnet/mvp.py
```

The [localnet guide](../localnet/README.md) covers reports, source changes, recovery checks and persisted state.
