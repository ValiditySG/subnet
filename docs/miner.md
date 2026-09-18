# Miner setup: manual Python or Docker

The [reference miner](../miner/src/validity_miner) receives a fictional RN query, resolves it from a packaged public board snapshot, and returns a hotkey-signed finding. It does not read validator answer labels. Defaults are `network=test`, `netuid=568` and HTTP port `8080`; the application pins Bittensor `11.1.0`.

## Choose how to run

| | Manual Python | Docker Compose |
| --- | --- | --- |
| Miner process | Run the checkout with `uv` | Run a packaged miner image |
| Prerequisites | Python 3.14, `uv`, a reachable Pylon service | Docker Engine and Compose plugin; Python 3 for the token preparation step below |
| `MINER_IMAGE` | Unused; leave empty or omit | Required |
| Pylon | Configure a separately managed service | Started by Compose without a wallet |
| Settings file | `miner/.env` via `--env-file` | `miner/.env` |
| State | Host path in `MINER_JOURNAL_PATH` | Private `miner-state` volume |

**Pylon is required by the current implementation in both modes.** The miner uses its read-only API to check subnet registrations and validator permits. See the [chain-service guide](chain-service.md), including a standalone Pylon container for manual Python runs. That recipe still uses Docker for Pylon; the miner itself runs directly in Python.

Each miner needs an existing registered SR25519 signing hotkey, a publicly reachable HTTP endpoint advertised on chain, and at least one admitted validator with a validator permit. Public-only hotkey files cannot sign responses. Neither launch method creates keys, registers hotkeys or advertises an endpoint automatically.

Use one active process per hotkey. Stop an existing instance before starting the same identity through another method, and preserve its recovery journal (see [recovery](#recovery)).

## Prepare private configuration

From the repository root:

```sh
cd miner
umask 077
cp -n .env.example .env
chmod 600 .env
```

Generate distinct Pylon tokens directly into empty fields; existing values are preserved:

```sh
python3 - <<'PY'
import secrets
from pathlib import Path
path = Path('.env')
data = path.read_text()
for name in ('MINER_PYLON_OPEN_ACCESS_TOKEN', 'PYLON_METRICS_TOKEN'):
    data = data.replace(name + '=\n', name + '=' + secrets.token_hex(32) + '\n')
path.write_text(data)
path.chmod(0o600)
PY
```

Edit `.env` using the tables below. Set `MINER_ALLOWED_VALIDATORS` to a nonempty JSON list of actual validator hotkeys; the example's `[]` is a placeholder and will fail startup. If using an existing Pylon service, replace the generated open-access token with its matching token in this private file.

Keep secrets in `.env` and wallet keys in protected files. Wallet configuration follows Bittensor defaults: directory `~/.bittensor/wallets`, wallet name `default`, hotkey name `default`. Only the wallet directory expands `~`; use an absolute or working-directory-relative path for the journal.

## Environment variables by launch method

**Required** means supply a nonempty value, including values already provided by the example. **Optional** means omission uses the stated default. **Fixed** means Compose supplies the value. **Not forwarded** means adding the variable to `.env` alone does not change the supplied Compose deployment. Omit optional values to use defaults; an empty string is not generally a default.

### Required settings

| Variable | Docker | Manual Python | Value / purpose |
| --- | --- | --- | --- |
| `MINER_CHAIN_GENESIS` | Required | Required | Keep the accepted testnet hash supplied in `.env.example`, shown below. |
| `MINER_ALLOWED_VALIDATORS` | Required | Required | Nonempty JSON array of SS58 hotkeys, e.g. `["<validator-hotkey>"]` after replacing the placeholder. |
| `MINER_PYLON_OPEN_ACCESS_TOKEN` | Required | Required | Must match Pylon's open-access token. |
| `MINER_PYLON_ADDRESS` | Fixed: `http://pylon:8000` | Required | Example: `http://127.0.0.1:8000` for a host-accessible sidecar. No Python default. |
| `MINER_IMAGE` | Required | Unused | Reviewed miner registry image with `@sha256:<digest>`. |
| `PYLON_IMAGE` | Required | Unused by Python | Reviewed [compatible Pylon image](chain-service.md) with `@sha256:<digest>`. A separately launched Pylon container also needs an image. |
| `PYLON_METRICS_TOKEN` | Required | Unused by Python | Separate Pylon metrics credential. Configure it in the sidecar when using the standalone recipe. |

The accepted testnet genesis is:

`0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105`

The miner needs no Hippius credentials; validators publish the scores.

### Optional settings and defaults

| Variable | Docker | Manual Python | Default / behavior |
| --- | --- | --- | --- |
| `MINER_NETWORK` | Fixed: `test` | Optional | `test`; this release accepts only testnet. |
| `MINER_NETUID` | Optional | Optional | `568`. |
| `MINER_WALLET_PATH` | Optional; host mount source | Optional; wallet directory | `~/.bittensor/wallets`. Compose passes `/wallets` inside the container. |
| `MINER_WALLET_NAME` | Optional | Optional | `default`; change to the existing wallet name. |
| `MINER_HOTKEY_NAME` | Optional | Optional | `default`; change to the existing hotkey name. |
| `MINER_PORT` | Optional; published host port | Optional; listening port | `8080`. Compose always listens on `8080` inside the container. Manual listening ports must be 1024–65535. Advertise the externally reachable port on chain. |
| `MINER_BIND_HOST` | Not forwarded | Optional | `0.0.0.0`; bind address for the Python HTTP server. |
| `MINER_JOURNAL_PATH` | Fixed: `/var/lib/validity-miner/requests.sqlite3` | Optional | Python default: `/var/lib/validity-miner/requests.sqlite3`. The example sets `./state/requests.sqlite3` for a writable source-run location. |
| `MINER_SOURCE_VERSION` | Optional | Optional | `v1`; supports `v1` and `v2`. Match the pilot's agreed dataset version. |
| `MINER_REQUESTS_PER_MINUTE` | Optional | Optional | `120` per validator; range 1–1000. |

Compose forwards only the variables explicitly listed in its service environment. For a setting marked fixed or not forwarded, change the Compose environment and required mounts explicitly to customize it; adding it to `.env` alone is insufficient.

## Manual Python setup

1. Prepare `.env` above. Set the required manual values, wallet identity and a writable `MINER_JOURNAL_PATH`.
2. Start or connect to [Pylon for manual Python runs](chain-service.md#pylon-for-manual-python-runs). The miner needs only open access, with no signing identity in its sidecar. The default Compose sidecar publishes no host port, so host Python cannot reach it at `localhost` without additional configuration.
3. Ensure the configured IP/port is reachable and matches the miner endpoint advertised on testnet. From the repository root, run:

```sh
cd miner
uv sync --frozen
uv run --frozen miner --env-file .env
```

Logs appear in the terminal. Press **Ctrl+C** to stop; use the same command and journal to restart. Existing shell variables take precedence over `.env`, so clear conflicting exported settings when switching configs. Relative paths such as `./state/requests.sqlite3` resolve from the working directory (`miner/` here), not from the `.env` file's directory.

`MINER_IMAGE`, `PYLON_IMAGE` and `PYLON_METRICS_TOKEN` are not read by the Python miner. Leave Docker-only fields empty or omit them for this launch path. Pylon still needs its own configuration.

## Docker Compose setup

1. Prepare `.env` above, including the three Docker-only required fields. Obtain reviewed registry digests for the miner and [compatible Pylon build](chain-service.md).
2. Set `MINER_WALLET_PATH` to a deployment wallet directory containing the required hotkey. The entire directory is mounted read-only; keep coldkey private keys outside it. Give container UID/GID `10001:10001` read and directory-traversal access to the required files without making them world-readable.
3. From the repository root, run:

```sh
cd miner
docker compose --env-file .env config --quiet
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose ps
docker compose logs --follow --tail=100 miner
```

Compose validates required substitutions, but `config --quiet` does not validate the allowlist, signing key or chain admission. The miner rejects invalid runtime settings, and admission fails closed until Pylon is ready. Inspect the logs and health endpoint below. Never print the resolved Compose environment: it includes secrets.

Ctrl+C stops log following. Stop the miner itself with:

```sh
docker compose stop miner
```

Run `docker compose up -d` in `miner/` to restart. Keep the `miner-state` volume during updates and restarts. To update an image, review its new digest in `.env`, then run `docker compose pull` and `docker compose up -d`.

The miner container runs as UID/GID `10001:10001` with a read-only root filesystem and bounded logs. Only the miner HTTP port is published; Pylon has no published port or wallet. Publishing a Docker port does not advertise the endpoint on chain.

To build the miner image yourself, run from the repository root so the shared protocol is included:

```sh
docker build -f miner/Dockerfile -t validity-miner:testnet-review .
```

Publish the reviewed build to your registry and use its registry digest in `MINER_IMAGE`.

## Verify operation

With the default port, run from another terminal on the miner host:

```sh
curl --fail http://127.0.0.1:8080/health
```

Expected response:

```json
{"status":"ok","scope":"synthetic-rn"}
```

Use your configured host port if it differs. This checks the HTTP server, not chain admission or a successful signed evaluation. The admitted validator's logs should show `Credential result` with `outcome=verified` after it discovers and queries this miner's advertised endpoint.

A port-in-use or journal-lock failure usually means another instance is running. Stop the existing instance and preserve its journal and lock file. For a rejected request, verify the allowlist, validator permit, matching chain/subnet and Pylon token/address.

## Serving requirements

Register the miner hotkey on testnet subnet `568` and advertise a globally routable IP/port with HTTP axon protocol value `4`. Each active miner hotkey needs its own instance, signing key, endpoint and private state. Serve **HTTP** at `POST /v1/evaluate`; do not redirect requests.

Miner–validator communication uses **plain HTTP only, with no TLS, HTTPS, or certificates**. The miner authenticates signed requests with registered hotkeys and checks subnet admission before evaluation. Responses are signed with the miner hotkey. Traffic is unencrypted; this release exchanges fictional RN queries only.

The request and response envelopes, signature bytes and replay checks are defined in the [synthetic RN protocol](../protocol/synthetic-rn-v1/README.md). Return a synchronous JSON response before the absolute deadline, with at most 64 KiB and no compression. Sign with the registered miner hotkey. Keep service tokens in a private `.env` and private wallet keys in protected files.

The reference service checks the validator allowlist, current subnet registration and validator permit on each authenticated request. It rejects signatures for another chain, subnet or miner. Deadlines must be within two minutes of issuance; future clock skew is limited to five seconds. It bounds requests and applies a configurable per-validator request quota. `/health` is available over HTTP without authentication and exposes only service status and the synthetic scope. Keep operator clocks synchronized.

## Recovery

One private SQLite journal binds responses to this miner, chain and subnet. It persists each signed response before sending it. Exact retries return identical bytes while the assignment remains valid; changing an existing task ID's content is rejected. Responses expire from the journal after their deadline plus one day. This is private replay state, not a combined validator score store. Miners do not upload score reports to Hippius.

Keep the state volume across updates and restarts. Stop the service before backing it up. Run one active process per hotkey; the filesystem lock cannot coordinate two hosts. Changing chain, subnet or miner identity requires fresh state.

## Evaluation

The public fictional board snapshots are in [the synthetic dataset](../validator/src/validator/synthetic). Five equal cases cover active, suspended, ambiguous, unavailable and missing records. Return the pinned source version/digest and evidence record IDs. Unavailable sources do not support active findings; ambiguous identities do not establish a license match.

Correct answers earn +1; transport failures 0; ordinary incorrect claims -1; unsafe clean findings or fabricated evidence -5. The mean over all assigned cases is the raw score. Validators submit normalized positive scores to the chain and publish each miner's raw totals in their signed Hippius epoch report.

These public cases test protocol conformance and operation. They are not a competitive real-world verification benchmark. Real board adapters and commercial agency workflows follow the subnet loop.

## Code and checks

- [`main.py`](../miner/src/validity_miner/main.py): CLI, wallet, exclusive state lock and HTTP server.
- [`service.py`](../miner/src/validity_miner/service.py): signatures, admission, deadlines and response signing.
- [`resolver.py`](../miner/src/validity_miner/resolver.py): synthetic board resolution.
- [`journal.py`](../miner/src/validity_miner/journal.py): durable retries and quotas.
- [`validity_protocol`](../protocol/src/validity_protocol): shared wire models used by both roles.

From `miner/`, run `uv run --frozen ruff check --fix`, `uv run --frozen ruff format`, `uv run --frozen basedpyright`, then `uv run --frozen pytest -q --tb=line -r f`. Tests exercise source resolution against independent labels, signature/context rejection, concurrent retries, restart recovery, deadlines, quotas and signed requests through the real HTTP server. Live testnet registration, endpoint discovery and admission still require the actual operator configuration.
