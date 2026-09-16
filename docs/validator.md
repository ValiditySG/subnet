# Run a Validity validator on testnet

This release uses fictional RN evaluations with production deployment practices. Defaults are `network=test` and `netuid=568`. Run one operator identity on each host and start only after its hotkey is registered and has a validator permit.

## Required operator configuration

From a reviewed checkout:

```sh
bash installer/install.sh
```

This creates `envs/deployed/.env` with mode `0600` and distinct random chain-sidecar tokens. It preserves an existing file and starts no services. Fill in:


| Setting                                              | Value                                                                     |
| ---------------------------------------------------- | ------------------------------------------------------------------------- |
| `VALIDATOR_IMAGE`                                    | Published validator image with `@sha256:<digest>`                         |
| `VALIDATOR_NETWORK`, `VALIDATOR_NETUID`             | Defaults: `test`, `568`                                                   |
| `VALIDATOR_TEMPO`                                    | Actual subnet tempo; do not copy an assumed development value             |
| `VALIDATOR_WALLET_NAME`, `VALIDATOR_HOTKEY_NAME`     | This operator's registered wallet                                         |
| `HOST_WALLET_DIR`                                    | Absolute wallet directory with the signing hotkey and public coldkey only |
| `HIPPIUS_BUCKET`                                     | `validity-testnet`                                                        |
| `HIPPIUS_ACCESS_KEY_ID`, `HIPPIUS_SECRET_ACCESS_KEY` | This operator's dedicated ACL token pair                                  |


Service tokens live only in `.env`. Wallet private keys remain protected files mounted read-only; never include a coldkey private key in the deployment. Give container UID/GID `10001:10001` read access to only the required files. Do not make private files world-readable. Do not reuse previous development wallets or recovery databases.

The deployment fixes the chain endpoint to Bittensor testnet. The genesis hash was read directly from that endpoint on 2026-09-16:

`0x8f9cf856bf558a14440e75569c9e58594757048d7b3a84b5d25f6bd978263105`

The runtime rejects other configured chain IDs. Chain data is trusted through the operator's sidecar; the preflight checks its subnet identity and validator registration/permit. See the [official network reference](https://preview.bittensor.com/docs/concepts/network) for the public endpoint.

## Miner transport and authentication

Validators contact registered public miner endpoints over **plain HTTP only, with no TLS, HTTPS, or certificates**. The endpoint is `POST http://<registered-ip>:<port>/v1/evaluate`; the miner's default port is `8080`. Redirects, proxy discovery and private-address endpoints are disabled.

The [signed RN exchange](../protocol/synthetic-rn-v1/README.md) authenticates both participants with their existing hotkeys. Each signature binds both hotkeys, the chain, subnet and task. Miners check the validator's signature, configured hotkey admission, registration and validator permit before evaluating. Validators verify the signed miner response. No public validator callback listener is needed.

Hotkey signatures authenticate messages and detect tampering; they do not encrypt traffic. This transport is for the current fictional RN evaluations. Real credential data requires a separate confidentiality design.

## Start and update

After the registered wallet has its validator permit and all required configuration is present:

```sh
bash installer/update_compose.sh
```

The script checks permissions and an immutable image digest, validates Compose without printing secrets, pulls pinned images, starts the private chain sidecar, runs read-only preflight, and starts the validator only if preflight passes. If the sidecar is still syncing, retry after it is ready. There are no unattended update jobs.

Preflight checks settings, packaged synthetic data, wallet access, sidecar subnet identity, registration, permit and Hippius read access. It does not prove miner reachability, PUT permission, actual chain-weight confirmation or future availability. Verify subnet tempo and weight constraints before starting; the deployment does not modify them.

```sh
cd envs/deployed
docker compose ps
docker compose logs --tail=100 validator
```

The validator runs without root privileges on a read-only root filesystem. Its named state volume is private to this operator. Log rotation is bounded; no metrics or chain-sidecar port is published. Container health requires a successful evaluation tick within three minutes. Optional tracing is disabled by default; no external telemetry destination is configured.

The current transport processes one assignment at a time. A round takes five tasks per discovered miner. Size the pilot and score-age threshold so complete rounds remain fresh. It pauses rewards if complete current positive scores are unavailable.

## Acceptance on the live subnet

Before declaring the migration complete:

1. Confirm the chain's genesis, subnet tempo, weight constraints, registered validator hotkey and permit.
2. Complete a round against compatible miners and replay its persisted scores.
3. Independently compare each validator's on-chain weight row and update block with its queued proposal. A log saying “queued” is insufficient.
4. Read `validity-testnet/<hotkey>/<epoch-start>.json` through an independent reader and verify all expected signatures in the same evaluation window.
5. Restart a validator and interrupt Hippius access; verify exact report retries and continuing weight operation.

No live testnet completion is claimed before those checks pass. Synthetic scores remain unsuitable for agency license decisions.

## Recovery and backups

Stop the validator before copying its complete private state volume. Back up the journal and wallet identity together through the operator's secure backup process. Never mount the same journal on two validators or run the same hotkey concurrently on two hosts. The file lock only coordinates processes sharing one filesystem.

Chain weights alone cannot reconstruct assignments, raw scores, penalties or pending reports. SQLite remains the private recovery journal; Hippius is the shared score store. Do not delete the state volume during ordinary updates. Changing network, subnet or hotkey requires new state.

## Contributor checks

```sh
cd validator
uv sync --frozen
uv run --frozen ruff check --fix
uv run --frozen ruff format
uv run --frozen basedpyright
uv run --frozen pytest -q --tb=line -r f
```

Tests exercise signed HTTP exchanges, tampering rejection, replay and identity binding, public-address filtering, upload isolation and durable recovery. They use isolated test doubles; live Hippius acceptance must use real storage.
