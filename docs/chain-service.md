# Pylon setup for Docker and manual Python runs

Pylon is the private service used by the current miner and validator to read Bittensor chain state. For validators it also signs and submits weights. Bittensor itself does not require Pylon; this codebase's current chain integration does. RN evaluation traffic travels directly between miners and validators over HTTP.

| Application launch method | How Pylon runs | Address used by the application |
| --- | --- | --- |
| Docker Compose | Included `pylon` service | `http://pylon:8000` inside the Compose network |
| Manual Python with an existing trusted Pylon service | Managed separately | Its private address and matching tokens |
| Manual Python with the standalone recipe below | Pylon runs in Docker; the application runs on the host | `http://127.0.0.1:8000` |

## Obtain a compatible image

The miner, validator and Validity Pylon image all use **Bittensor `11.1.0` and its bundled `bittensor.wallet` API**. This is the latest stable SDK verified on 2026-09-18 against [PyPI](https://pypi.org/project/bittensor/11.1.0/). Existing wallet files are read without conversion.

[`pylon/Dockerfile`](../pylon/Dockerfile) builds from the pinned Pylon `2.3.2` image and verified TurboBT `1.3.1` source. Reviewed patches migrate [Pylon](../pylon/bittensor-sdk.patch) and [TurboBT](../pylon/turbobt-sdk.patch) wallet imports to the SDK and pass plain bytes when signing transactions. Their packages are rebuilt with matching dependency declarations as `2.3.2+validity.sdk11` and `1.3.1+validity.sdk11`. Pylon still uses TurboBT for chain reads and submissions.

The image removes the standalone `bittensor-wallet` package, which is [superseded by Bittensor 11](https://pypi.org/project/bittensor-wallet/4.1.1/). SDK dependencies and build tools are hash-locked. The build checks dependency consistency; simply installing a newer wallet over the upstream image would leave its old imports and dependency constraints active.

Build the Validity image from the repository root:

```sh
docker build -t validity-pylon:testnet-review pylon
```

The build runs [offline compatibility tests](../pylon/tests/test_sdk_wallet.py) covering service imports, current key files, public-only key rejection, absence of the legacy package and transaction signatures, including large signing payloads. The test keys are public development fixtures; no operator wallet or chain transaction is involved.

For either full Compose deployment, publish the reviewed build to your registry and set `PYLON_IMAGE` in the application's private `.env` to its `registry/image@sha256:<digest>` reference. The validator deployment script enforces registry digests for both images.

For the standalone source-testing recipes below, select the immutable local image ID without publishing it:

```sh
PYLON_IMAGE=$(docker image inspect --format '{{.Id}}' validity-pylon:testnet-review)
```

Run this in the terminal that will execute `docker run`. This variable chooses the Pylon container image; the Python miner and validator do not read it.

### Updating the SDK pin

Keep `bittensor` aligned in `validator/pyproject.toml`, `miner/pyproject.toml`, `protocol/pyproject.toml` and `pylon/sdk-requirements.in`. Regenerate the application locks and Pylon's hashed requirements, review the dependency changes, then rebuild and test the image. Pylon's existing packages are constrained to avoid unrelated upgrades:

```sh
uv pip compile pylon/sdk-requirements.in \
  --constraints pylon/base-constraints.txt --python-version 3.13 --universal \
  --exclude-newer '11 days' --generate-hashes --no-header --no-annotate \
  --output-file pylon/sdk-requirements.txt
```

Also update the explicit SDK dependency pins in both patches and the compatibility test's expected version. Build backends are separately locked in `pylon/build-requirements.txt` from `pylon/build-requirements.in` using the same constraints and release-age policy.

Pylon's pinned upstream image uses Python 3.13; the miner and validator use Python 3.14. Both use the same SDK release. Review and update both source patches when changing the Pylon base image or TurboBT source; the build rejects patch context mismatches. A rebuilt image takes effect only after the operator updates `PYLON_IMAGE` and recreates the sidecar while preserving its state volume.

## Pylon for manual Python runs

If a trusted, correctly configured Pylon instance already exists, set the application address and matching tokens and skip container creation. The validator's Pylon identity must use the same wallet, hotkey and netuid as the Python validator. A miner uses only open access and does not need a signing identity in its sidecar.

The following are separate per-operator recipes. When running two sidecars on one host, choose different container names and host ports, then set each application's address accordingly. Bind the API only to loopback; do not expose Pylon publicly. The default application Compose files deliberately publish no Pylon port, so `docker compose up pylon` alone does not provide host Python with a loopback API.

### Validator sidecar

Prepare the validator's `envs/deployed/.env` using its [setup guide](validator.md#prepare-private-configuration). Create a second private file from the repository root without overwriting an existing one:

```sh
umask 077
touch envs/deployed/.env.pylon
chmod 600 envs/deployed/.env.pylon
```

Edit `envs/deployed/.env.pylon` to contain the following settings. Replace token placeholders in the file with the matching values from the application's private `.env`; do not put tokens on a command line.

```dotenv
PYLON_BITTENSOR_NETWORK=wss://test.finney.opentensor.ai:443
PYLON_BITTENSOR_ARCHIVE_NETWORK=wss://test.finney.opentensor.ai:443
PYLON_OPEN_ACCESS_TOKEN=<same value as VALIDATOR_PYLON_OPEN_ACCESS_TOKEN>
PYLON_METRICS_TOKEN=<same value as the application file's PYLON_METRICS_TOKEN>
PYLON_IDENTITIES=["validator"]
PYLON_BITTENSOR_WALLET_PATH=/wallets
PYLON_ID_VALIDATOR_WALLET_NAME=default
PYLON_ID_VALIDATOR_HOTKEY_NAME=default
PYLON_ID_VALIDATOR_NETUID=568
PYLON_ID_VALIDATOR_TOKEN=<same value as VALIDATOR_PYLON_IDENTITY_TOKEN>
PYLON_DATABASE_PATH=/state/pylon.db
PYLON_BLOCK_DURATION_SECONDS=12
PYLON_RECENT_OBJECTS_SOFT_LIMIT_BLOCKS=5
PYLON_RECENT_OBJECTS_HARD_LIMIT_BLOCKS=10
PYLON_RECENT_OBJECTS_REFRESH_LEAD_BLOCKS=2
PYLON_ENVIRONMENT=testnet
```

Use the same wallet/hotkey names and netuid as the application. This example uses identity name `validator`, so set `VALIDATOR_PYLON_IDENTITY_NAME=validator` in the application's `.env`.

After selecting `PYLON_IMAGE` above, run from the repository root. These two read-only wallet mounts use the standard `default` wallet/hotkey names. If using a custom `VALIDATOR_WALLET_PATH`, wallet name or hotkey name, adjust both the host paths and container paths to match `.env.pylon`. Keep the private coldkey outside the container.

```sh
docker run --detach --name validity-pylon-validator \
  --restart unless-stopped --init \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --log-opt max-size=10m --log-opt max-file=3 \
  --publish 127.0.0.1:8000:8000 \
  --env-file envs/deployed/.env.pylon \
  --mount "type=bind,src=$HOME/.bittensor/wallets/default/hotkeys/default,dst=/wallets/default/hotkeys/default,readonly" \
  --mount "type=bind,src=$HOME/.bittensor/wallets/default/coldkeypub.txt,dst=/wallets/default/coldkeypub.txt,readonly" \
  --mount type=volume,src=validity-pylon-validator-state,dst=/state \
  "$PYLON_IMAGE"
```

Set `VALIDATOR_PYLON_SERVICE_ADDRESS=http://127.0.0.1:8000` in the application's `.env`. Run the validator guide's read-only preflight before starting evaluation or weights. If the sidecar is still syncing, wait and retry preflight.

### Miner sidecar

Prepare `miner/.env` using the [miner guide](miner.md#prepare-private-configuration). Create a private file from the repository root:

```sh
umask 077
touch miner/.env.pylon
chmod 600 miner/.env.pylon
```

Edit it with these values, replacing the token placeholders with the matching application values:

```dotenv
PYLON_BITTENSOR_NETWORK=wss://test.finney.opentensor.ai:443
PYLON_BITTENSOR_ARCHIVE_NETWORK=wss://test.finney.opentensor.ai:443
PYLON_OPEN_ACCESS_TOKEN=<same value as MINER_PYLON_OPEN_ACCESS_TOKEN>
PYLON_METRICS_TOKEN=<same value as the application file's PYLON_METRICS_TOKEN>
PYLON_IDENTITIES=[]
PYLON_DATABASE_PATH=/state/pylon.db
PYLON_ENVIRONMENT=testnet
```

After selecting `PYLON_IMAGE` above, run from the repository root:

```sh
docker run --detach --name validity-pylon-miner \
  --restart unless-stopped --init \
  --security-opt no-new-privileges:true --cap-drop ALL \
  --log-opt max-size=10m --log-opt max-file=3 \
  --publish 127.0.0.1:8000:8000 \
  --env-file miner/.env.pylon \
  --mount type=volume,src=validity-pylon-miner-state,dst=/state \
  "$PYLON_IMAGE"
```

Set `MINER_PYLON_ADDRESS=http://127.0.0.1:8000` in `miner/.env`. This sidecar has no wallet mounts or signing identity. The miner checks chain admission when it receives a signed evaluation request; `/health` alone does not prove chain access.

### Sidecar variables used by these recipes

These settings belong to Pylon's private `.env.pylon`, separate from the application environment. Both supplied recipes explicitly configure the network and tokens.

| Pylon variable | Requirement in these recipes | Purpose |
| --- | --- | --- |
| `PYLON_BITTENSOR_NETWORK` | Required | Explicit testnet RPC endpoint. |
| `PYLON_BITTENSOR_ARCHIVE_NETWORK` | Required when overriding the main endpoint | Must also point to testnet. Pylon rejects an override of only one endpoint. |
| `PYLON_OPEN_ACCESS_TOKEN` | Required | Match the application's open-access token. |
| `PYLON_METRICS_TOKEN` | Required by these recipes | A distinct private metrics token; not used by the Python application. |
| `PYLON_IDENTITIES` | Required by these recipes | `["validator"]` for the validator sidecar; `[]` for a read-only miner sidecar. |
| `PYLON_BITTENSOR_WALLET_PATH` | Required for the validator recipe | `/wallets`, matching the container mount destinations. Unused by the miner recipe. |
| `PYLON_ID_VALIDATOR_WALLET_NAME`, `PYLON_ID_VALIDATOR_HOTKEY_NAME` | Required for the validator recipe | Match the application's existing signing identity. |
| `PYLON_ID_VALIDATOR_NETUID`, `PYLON_ID_VALIDATOR_TOKEN` | Required for the validator recipe | Match the application's netuid and identity token. |
| `PYLON_DATABASE_PATH` | Required for the supplied persistent-volume layout | `/state/pylon.db`. Otherwise Pylon defaults to a database inside its working directory. |
| `PYLON_BLOCK_DURATION_SECONDS` | Optional | Default `12`; used by commit–reveal timing. |
| `PYLON_RECENT_OBJECTS_SOFT_LIMIT_BLOCKS` | Optional; validator recipe sets `5` | Upstream default is `100`. |
| `PYLON_RECENT_OBJECTS_HARD_LIMIT_BLOCKS` | Optional; validator recipe sets `10` | Upstream default is `150`; older cached rosters are rejected. |
| `PYLON_RECENT_OBJECTS_REFRESH_LEAD_BLOCKS` | Optional; validator recipe sets `2` | Upstream default is `10`. Together with the soft limit, the recipe refreshes every three blocks. |
| `PYLON_ENVIRONMENT` | Optional | Recipe sets `testnet`; upstream default `production`. A log label, not a chain selector. |

Pylon reads subnet tempo from the chain; there is no `PYLON_TEMPO` setting in this release. `VALIDATOR_TEMPO` configures the Python validator's own epoch clock. Both RPC settings in these recipes use the same testnet endpoint, so historical reads are limited to the history that endpoint retains.

## Stop, restart and update the standalone sidecar

For the validator recipe:

```sh
docker logs --follow --tail=100 validity-pylon-validator
docker stop validity-pylon-validator
docker start validity-pylon-validator
```

Ctrl+C exits log following; run the stop/start commands when needed. For the miner recipe, use container name `validity-pylon-miner` instead. Stopping Pylon prevents chain reads/submissions until it is back; it does not stop the Python application.

`docker start` reuses the container's existing environment and image. After editing `.env.pylon` or changing the image, stop and recreate that container with the same wallet mounts and named volume. Keep the volume during replacement so the sidecar's stored state survives. Preserve the application's separate recovery journal too; neither a sidecar restart nor a queued proposal proves that weights have revealed on chain.
