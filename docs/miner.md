# Run a Validity miner on testnet

The runnable reference miner is in [`miner/src/validity_miner/`](../miner/src/validity_miner). It receives a fictional RN query, resolves records from a packaged public board snapshot, and returns a signed finding with evidence. It does not read validator answer labels. This is a synthetic testnet implementation; real nursing-board adapters follow.

## Configure one operator

From the repository root:

```sh
cd miner
umask 077
cp -n .env.example .env
chmod 600 .env
```

Fill in the private `.env` before starting:

| Setting | Value |
| --- | --- |
| `MINER_NETUID` | Actual registered testnet subnet; currently awaiting registration |
| `MINER_WALLET_NAME`, `MINER_HOTKEY_NAME` | This operator's registered miner wallet |
| `MINER_ALLOWED_VALIDATORS` | JSON array of admitted validator hotkeys, e.g. `["<hotkey>"]` |
| `MINER_PYLON_OPEN_ACCESS_TOKEN`, `PYLON_METRICS_TOKEN` | Two independent random secrets for the private chain sidecar |
| `MINER_SOURCE_VERSION` | `v1` or `v2`, matching the pilot's agreed source rollout |
| `MINER_IMAGE` | Released miner image with `@sha256:<digest>` for Compose |
| `HOST_MINER_WALLET_DIR` | Absolute directory containing the miner hotkey and public coldkey only |
| `HOST_MINER_TLS_DIR` | Absolute directory containing `ca.pem`, `server.pem`, `server-key.pem` |

Keep tokens in `.env`; wallet and TLS private keys remain protected files. Give container UID/GID `10001:10001` read access to the required mounted files without making them world-readable. Keep the coldkey private key and CA signing key offline. Do not reuse retired development wallets or state.

## Run with Docker

On the miner operator's host, after registration, TLS provisioning and configuration:

```sh
cd miner
docker compose --env-file .env config --quiet
docker compose --env-file .env pull
docker compose --env-file .env up -d
docker compose ps
docker compose logs --tail=100 miner
```

Do not print the resolved Compose configuration: it contains secrets. The deployment publishes only the miner's TLS port (`8443` by default). Its chain sidecar has read access and no wallet or signing identity. The miner runs without root privileges on a read-only filesystem, with a private state volume and bounded logs. Admission fails closed until the sidecar is ready. Publishing a container port does not register the endpoint on chain.

For a source build, run from the **repository root** so Docker can include the shared protocol package:

```sh
docker build -f miner/Dockerfile -t validity-miner:testnet-review .
```

Publish a reviewed image and use its registry digest in the deployment configuration.

## Run from source

Set `MINER_WALLET_PATH`, `MINER_TLS_CA_FILE`, `MINER_TLS_CERT_FILE`, `MINER_TLS_KEY_FILE` and `MINER_JOURNAL_PATH` to this host's private paths. Set `MINER_PYLON_ADDRESS` to a trusted, privately reachable testnet sidecar using the configured token. Then, from `miner/`:

```sh
uv sync --frozen
uv run --frozen miner --env-file .env
```

Both start methods require an existing wallet. They do not create keys, spend tokens, register hotkeys or advertise endpoints automatically.

## Serving requirements

Register the miner hotkey on the supplied testnet netuid and advertise a globally routable IP/port with HTTP axon protocol value `4`. Serve **HTTPS** at `POST /v1/evaluate`; do not redirect requests. The server certificate must be trusted by validators and include the registered IP as a subject alternative name. Require each validator's client certificate and verify its hotkey signature and current subnet admission.

The request and response envelopes, signature bytes and replay checks are defined in the [synthetic RN protocol](../protocol/synthetic-rn-v1/README.md). Return a synchronous JSON response before the absolute deadline, with at most 64 KiB and no compression. Sign with the registered miner hotkey. Keep service tokens in a private `.env` and private wallet/TLS keys in protected files.

The reference service checks the validator allowlist, current subnet registration and validator permit on each authenticated request. It rejects signatures for another chain, subnet or miner. Deadlines must be within two minutes of issuance; future clock skew is limited to five seconds. It bounds requests and applies a configurable per-validator request quota. `/health` also requires a trusted client certificate. Keep operator clocks synchronized.

## Recovery

One private SQLite journal binds responses to this miner, chain and subnet. It persists each signed response before sending it. Exact retries return identical bytes while the assignment remains valid; changing an existing task ID's content is rejected. Responses expire from the journal after their deadline plus one day. This is private replay state, not a combined validator score store. Miners do not upload score reports to Hippius.

Keep the state volume across updates and restarts. Stop the service before backing it up. Run one active process per hotkey; the filesystem lock cannot coordinate two hosts. Changing chain, subnet or miner identity requires fresh state.

## Evaluation

The public fictional board snapshots are in [the synthetic dataset](../validator/src/validator/synthetic). Five equal cases cover active, suspended, ambiguous, unavailable and missing records. Return the pinned source version/digest and evidence record IDs. Unavailable sources do not support active findings; ambiguous identities do not establish a license match.

Correct answers earn +1; transport failures 0; ordinary incorrect claims -1; unsafe clean findings or fabricated evidence -5. The mean over all assigned cases is the raw score. Validators submit normalized positive scores to the chain and publish each miner's raw totals in their signed Hippius epoch report.

These public cases test protocol conformance and operation. They are not a competitive real-world verification benchmark. Real board adapters and commercial agency workflows follow the subnet loop.

## Code and checks

- [`main.py`](../miner/src/validity_miner/main.py): CLI, wallet, exclusive state lock and mutual TLS server.
- [`service.py`](../miner/src/validity_miner/service.py): signatures, admission, deadlines and response signing.
- [`resolver.py`](../miner/src/validity_miner/resolver.py): synthetic board resolution.
- [`journal.py`](../miner/src/validity_miner/journal.py): durable retries and quotas.
- [`validity_protocol`](../protocol/src/validity_protocol): shared wire models used by both roles.

From `miner/`, run `uv run --frozen ruff check --fix`, `uv run --frozen ruff format`, `uv run --frozen basedpyright`, then `uv run --frozen pytest -q --tb=line -r f`. Tests exercise source resolution against independent labels, signature/context rejection, concurrent retries, restart recovery, deadlines, quotas and the real mutual TLS server. Live testnet registration, endpoint discovery and admission still require the actual operator configuration.
