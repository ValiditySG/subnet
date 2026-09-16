# Signed score reporting

**Weights → chain. Signed scores → Hippius, using one dedicated ACL credential profile per validator team.**

The Hippius team confirmed dedicated ACL user tokens per validator team, and three test credentials have
been generated. Each validator signs completed RN rounds with its own hotkey and uploads directly to the
shared `localnet` test bucket using its team's credential profile. Each token has bucket-wide WRITE access.
Readers later verify the signature, digest, object path
and expected validator identity. An upload gateway is no longer required for Hippius.

The two credentials serve different purposes: the Hippius token authorizes storage operations; the hotkey
signature authenticates the report. Neither secret appears in published reports. A token can be rotated
without changing the validator's signing identity or invalidating older report signatures.

## Deployment and storage ownership

Production validators run independently on their operators' machines. Running several validator identities
on one machine is a development test arrangement only.

| Storage | Owner and purpose |
| --- | --- |
| Chain weights | Each validator submits its own weight row to the chain |
| Published score reports | Hippius holds reports from all validators, authenticated by their individual signatures |
| SQLite recovery journal | Private to one validator; holds only that validator's observations, proposals and upload retries |
| Local JSON object directory | Test substitute used only by the development harness and automated tests |

SQLite is never a shared database for multiple validators or the source for the combined leaderboard. Even
the three-identity automated test creates a separate SQLite file for each identity. The local object-store
substitute uses JSON files, independently of those recovery journals.

**Hippius integration tests must use the actual Hippius backend for shared report storage.** They must not
substitute a local directory or shared SQLite database. Each validator retains its own isolated recovery
state. If Hippius is unavailable, uploads remain pending for retry; the service does not fall back to local
report storage. Production follows the same ownership boundaries.

## Current boundary

| Component | Status |
| --- | --- |
| Hotkey signatures, schema and content hashes | Implemented |
| Durable outbox, retries and lost-ack recovery | Implemented |
| Direct Hippius publisher with separate credential profiles | Implemented |
| Three credential profiles and hotkey identities | Live localnet validators with separate journals, weight rows and Hippius reports |
| Paginated reader with signature verification | Implemented; Hippius mode accepts reads only |
| Dedicated ACL user tokens per team | Confirmed with Hippius by the user; three test credentials generated |
| Bucket and private credential file | `localnet`; `localnet/hippius-credentials.json` |
| Token grants | Bucket-wide WRITE confirmed for each token; no per-validator prefix isolation |
| JSON profile loading | Implemented; three profile names provided in the example file |
| Reader GET/LIST access | Verified with `owner`, `validator-1` and `validator-2` on an actual signed report |
| Retention | Unconfigured by this code; no immutability claim |
| Live Hippius upload/readback | Three signatures verified in one window at epoch 405399; see the verification report |
| Public-network validator admission and chain-context verification | Deferred; localnet uses an operator-managed allowlist |
| Three-validator localnet chain deployment | Weight rows verified directly for UIDs 1, 10 and 11 |
| Live website leaderboard adapter | Follow-up integration |

The development gateway currently defaults to `local`. Use that backend only for the local harness and
automated transport tests; these runs are not Hippius integration tests.
No bucket policies, ACLs, credentials, public-read permissions, versioning or retention settings are created
by this code.

## Direct Hippius setup

Copy [`localnet/hippius-validator.env.example`](../localnet/hippius-validator.env.example) into each validator's
private environment configuration. Set `VALIDATOR_REPORTS_BACKEND=hippius`, bucket `localnet`, local
chain genesis, wallet location and that team's credentials file. Both named JSON profiles and AWS INI
shared-credentials files are supported. Relative paths in the example resolve from `validator/`.
The selected profile must contain both an access key ID and secret. JSON accepts `access-key-id` and
`secret-access-key`, or `aws_access_key_id` and `aws_secret_access_key`; use the ID issued with the ACL token.
Put these values in private files on the appropriate validator host.

For the shared-host test, fill `localnet/hippius-credentials.json` using the structure in
[`hippius-credentials.example.json`](../localnet/hippius-credentials.example.json): a mapping from profile
names to their credential pairs. A surrounding `profiles` object is also accepted. Each profile contains the access key ID and secret;
`aws_session_token` is optional. The private JSON file is ignored by Git; keep its permissions at `0600`.
The checked-in example contains placeholders only. Do not overwrite an existing populated private file
when copying examples.

The three-validator test configuration uses independent identities and recovery journals:

| Test validator | Credential profile | Signing wallet | Private ledger | Callback port on a shared test host |
| --- | --- | --- | --- | --- |
| 1 | `validator-1` | `validator` | `state/credentials.sqlite3` | 8001 |
| 2 | `validator-2` | `validator-2` | `state/validator-2/credentials.sqlite3` | 8002 |
| 3 | `owner` | `validator-3` | `state/validator-3/credentials.sqlite3` | 8003 |

The user approved the `owner` profile for the third test validator. The wallet `validator-3` is a separate
validator identity; it is not the subnet owner's signing wallet. Each validator needs its own registered
wallet, sidecar identity and matching runtime configuration to produce completed-round reports.
Production uses separate operator machines; a validator receives
only its own team's credential file. The test runner may reference three private profiles, but never shares
a SQLite database between identities. Actual profile and wallet names must match the user's mapping.

Each validator's relevant settings are:

```dotenv
VALIDATOR_REPORTS_ENABLED=true
VALIDATOR_REPORTS_BACKEND=hippius
VALIDATOR_REPORTS_BUCKET=localnet
VALIDATOR_REPORTS_CREDENTIALS_FILE=../localnet/hippius-credentials.json
VALIDATOR_REPORTS_PROFILE=validator-1
VALIDATOR_REPORTS_CHAIN_GENESIS=0x...
VALIDATOR_REPORTS_WALLET_NAME=validator
VALIDATOR_REPORTS_HOTKEY_NAME=default
VALIDATOR_REPORTS_VERIFY_READBACK=false
```

The file/profile is explicit: unrelated AWS environment credentials are not substituted for an incomplete
selected profile. Secrets are loaded inside the publishing actor and kept out of its settings model, logs,
report payloads and checked-in files.

With `VERIFY_READBACK=false`, publishing needs PUT access only. A successful S3 PUT records
`verification=upload_accepted`; it does not claim that the report was read back. Set this option to `true`
only when the same ACL profile also has GET access; it records `readback_verified` after validating the
stored signature and content hash. With readback enabled, the publisher also checks an existing epoch
file before writing and retires a retry if a newer valid snapshot is already stored. A separate reader needs GET access to verify all validators'
published reports. All three configured test profiles passed live GET/LIST checks, so the running test
validators enable readback verification. The examples retain `false` for upload-only deployments.

### Three validators on the test host

With the existing localnet running, register the additional wallets using local development funds:

```sh
uv run --frozen --project miner python localnet/bootstrap.py \
  --validator-wallet validator-2 --validator-wallet validator-3
docker compose -f localnet/compose.yml -f localnet/compose.hippius.yml \
  --env-file localnet/.env up -d --no-deps pylon-2 pylon-3
```

The extra sidecars bind `127.0.0.1:8010` and `127.0.0.1:8020`; the first remains on port 8000.
For each validator, copy the validator environment example to a private file under `localnet/state/`,
retain the existing sidecar access tokens, and set the wallet/profile/ledger/callback values from the table.
Set `VALIDATOR_PYLON_SERVICE_ADDRESS` to its sidecar port and keep the sidecar identity name `validator`.
Run each validator from `validator/` using its own environment file:

```sh
uv run --frozen validator --env-file ../localnet/state/hippius-validator-2.env
```

Replace `2` with the desired test validator number. Run only one process for each private ledger.
The current test has private configurations for all three under `localnet/state/` and uses tmux windows
`main`, `validator-2` and `validator-3`. The Hippius reader uses `hippius-reader` and port 8091.
These configuration files contain local access tokens and are ignored by Git.

Verify each chain weight row independently from the repository root, using the corresponding wallet
and ledger. For example:

```sh
uv run --frozen --project miner python localnet/mvp.py verify --version v2 \
  --report hippius --validator-wallet validator-2 \
  --ledger-path localnet/state/validator-2/credentials.sqlite3 --timeout 600
```

Use the default wallet/ledger for validator 1, and replace `2` with `3` for validator 3.
The reports are `state/hippius.json`, `state/hippius-validator-2.json` and `state/hippius-validator-3.json`.

After all three validators publish into the same epoch/source window, verify them with a reader environment
file based on [`hippius-reader.env.example`](../localnet/hippius-reader.env.example), containing
`HIPPIUS_BACKEND=hippius`, the bucket/genesis/netuid, READ/LIST credentials and all three
public hotkeys in `HIPPIUS_VALIDATORS`. From `validator/`, run (replace the epoch with the actual value):

```sh
uv run --frozen python -m validator.reporting.check \
  --env-file ../localnet/state/hippius-reader.env --epoch-start 405399 \
  --output ../localnet/state/hippius-readback.json
```

This command refuses the local backend. It fetches reports from Hippius and requires valid signatures from
all three expected hotkeys within one matching source/policy window. It emits report IDs and public hotkeys,
never tokens. Missing, altered or mismatched reports fail verification. This verifies stored authorship;
chain-weight readback remains a separate check. The command does not deploy or register validators.

Delivery receipts and retries are keyed by destination and object layout in `report_deliveries`. Existing local-test envelopes
remain signed exactly as before, but their local receipts do not mark them delivered to Hippius. Switching
buckets similarly creates a separate delivery record. Rotating credentials for the same bucket preserves
delivery status. Legacy upload columns in `report_outbox` are retained for migration history and are no
longer the current delivery status. The current destination includes `#hotkey-epoch-v1`, so acknowledgements
for the former nested layout do not count as delivery to the new paths.
Publishing prioritizes the newest due round so historical backfill does not delay current leaderboard
scores. There is only one current snapshot per validator/epoch. Earlier rounds from that same epoch are
marked `superseded_by` and retained in the private journal; they are never retried over a newer file.
Other epochs remain queued, with each report's persisted retry delay still applied.

## Local setup

This section runs the **development gateway substitute only**. It is not the Hippius integration setup.

Use the existing [localnet](../localnet/README.md), with one ledger per validator. From the repository root,
read the local genesis hash:

```sh
curl -sS http://127.0.0.1:9944 -H 'Content-Type: application/json' \
  --data '{"jsonrpc":"2.0","id":1,"method":"chain_getBlockHash","params":[0]}'
uv run --frozen --project validator python -c \
  'from bittensor_wallet import Wallet; print(Wallet(name="validator", hotkey="default", path="localnet/wallets").get_hotkey().ss58_address)'
```

Copy `localnet/score-service.env.example` to `localnet/state/score-service.env`. Set
`HIPPIUS_CHAIN_GENESIS` to the returned hash and `HIPPIUS_VALIDATORS` to a JSON array of allowed public
validator hotkeys. Confirm these are the intended localnet validator wallets. The service's allowlist is
explicit local admission; it does not automatically track on-chain permits or revocations.

Start the service from `validator/`:

```sh
uv run --frozen python -m validator.reporting.gateway --env-file ../localnet/state/score-service.env
```

In `localnet/.env`, set these nonsecret values before restarting the validator:

```dotenv
VALIDATOR_REPORTS_ENABLED=true
VALIDATOR_REPORTS_BACKEND=gateway
VALIDATOR_REPORTS_GATEWAY_URL=http://127.0.0.1:8090
# Replace this placeholder with the same actual local genesis hash:
VALIDATOR_REPORTS_CHAIN_GENESIS=0x...
VALIDATOR_REPORTS_WALLET_PATH=../localnet/wallets
VALIDATOR_REPORTS_WALLET_NAME=validator
VALIDATOR_REPORTS_HOTKEY_NAME=default
```

The signing wallet must match that validator's chain identity. Reporting is only connected in
`VALIDATOR_MODE=local_credentials`. For same-machine development tests only, each additional validator needs
its own sidecar identity, wallet, callback port and ledger; add its public hotkey to the gateway allowlist.
Production validators are deployed independently on separate operator machines. Never copy a ledger between
validator identities. Archive state before resetting the local chain, even if its genesis hash repeats.

The existing restart command reloads `.env` without resetting the chain:

```sh
uv run --frozen --project miner python localnet/mvp.py restart-validator
```

Verify a later completed round against the chain, replacing `219` with the restart command's reported
`round_before_restart` and selecting the source version currently in use:

```sh
uv run --frozen --project miner python localnet/mvp.py verify --version v2 \
  --report score-reporting --after-round 219 --timeout 420
```

This checks weights independently of score storage. The observed publication and chain results are in the
[verification report](localnet-implementation.md#signed-reporting-verification--2026-09-16).

An upload timeout defaults to five seconds (`VALIDATOR_REPORTS_TIMEOUT_SECONDS`). Failed attempts back off
from two seconds to five minutes. Recovery retransmits the same signed envelope. The service accepts
historical retries; receipt time is not evaluation time. The local service binds loopback. Remote deployment
needs HTTPS, request/concurrency limits and qualified admission rules before public use.

## Reports and leaderboard reads

See the [wire contract](../protocol/score-reports-v1/README.md). Validators PUT signed envelopes directly to
Hippius. The reader can be started using the same service command with `HIPPIUS_BACKEND=hippius`, a bucket,
READ credential-file path/profile, chain genesis, netuid and expected hotkey allowlist. HTTP uploads
return 405 in Hippius mode. `POST /v1/reports` is retained only for the local development gateway.
`GET /v1/reports?epoch_start=<block>&limit=100` returns verified reports and an optional `next_token`.
Pass that value back as the URL-encoded `token` query parameter until it is null; a page may contain no
valid reports and still have a continuation token.
The reader requests the exact epoch file from each admitted hotkey's directory; it does not scan the
bucket or use local SQLite to combine validators. Pagination advances through the configured hotkeys.

Keep a separate column for every validator hotkey. Group windows by chain genesis, subnet, epoch, policy
and source digest. Each epoch file contains that validator's latest completed round for the epoch,
retaining its report ID for inspection. This is a snapshot, not a sum of every round within the epoch.
Join miners by
hotkey and display the report's UID as historical context. Never merge a replacement miner solely by UID.
Raw score is `score_sum / assigned_tasks`, including zero and negative values. Missing reports are missing
data, not zero scores. Staleness uses completion time/block, never receipt time. Chain weights must be read
separately with their observed block; a reported score is not a confirmed weight.

Objects are addressed as:

```text
localnet/
  <validator-hotkey-1>/
    405399.json
    405760.json
  <validator-hotkey-2>/
    405399.json
  <validator-hotkey-3>/
    405399.json
```

`localnet` is the bucket, each directory is a validator's hotkey, and filenames use the epoch's first
block. One JSON file contains **all miners' scores** for that validator and epoch. Each miner entry carries
integer `score_sum`, `assigned_tasks` and `correct_tasks`; its raw score is `score_sum / assigned_tasks`.
The signed JSON also includes chain genesis, netuid, epoch bounds, completion block, round ID, source
context, report hash and signature. Use a separate bucket for each network/subnet to avoid path collisions.

Run one active publisher per hotkey and preserve its private journal. Its outbox coalesces same-epoch
rounds before retrying. GET-enabled uploads additionally compare the stored snapshot before PUT; this is
not a storage-level compare-and-swap guarantee against competing writers. A conflicting signed snapshot
at an identical completion position is rejected.

The API never accepts a caller-supplied bucket or object key. Repeated uploads of the same report are
idempotent. Content hashes and signatures allow readers to detect changes; they do not prevent a bucket
writer from overwriting or deleting objects or a validator from signing false/conflicting scores. Reader verification
does not establish historical chain registration. Admission and anti-equivocation policy need qualification.

## Persistence and privacy

Each validator's private SQLite journal retains its own observations, round context, pending assignments,
frozen weight batches and report outbox. The score service and leaderboard do not read validator databases;
they read published report objects from the configured object store.
Uploaded summaries cannot reconstruct that operational state. Completed rounds predating recorded block/epoch
metadata are left untouched and are not assigned invented historical windows.

Reports include aggregate task counts and scores, miner/validator hotkeys, source version/digest, policy,
timestamps and block context. They contain no RN names, license numbers, requests, source documents or
private keys. Only fictional-source reports are accepted by v1.

## Hippius configuration and remaining checks

Validator upload settings use the `VALIDATOR_REPORTS_` prefix. The independent reader uses `HIPPIUS_BUCKET`,
`HIPPIUS_CREDENTIALS_FILE` and `HIPPIUS_PROFILE`. Both accept named JSON profiles or an AWS
shared-credentials-format file and an explicit profile. The confirmed test bucket is `localnet`, and the
private credential path is `localnet/hippius-credentials.json`. All three profiles are populated and have
passed GET/LIST checks. The access model and bucket-wide WRITE grants are confirmed.

Each test token can write across the bucket; separate tokens do not isolate validators' object paths.
Signature verification rejects tampering and forged authorship; it does not prevent overwrites or deletion.
Do not claim prefix isolation. Do not alter bucket
policies, public access or retention as part of an upload test.

The S3 adapter uses the published endpoint `https://s3.hippius.com`, region `decentralized`, SigV4
and path-style addressing. It performs PUT/GET/ListObjectsV2 and does not depend on conditional PUT or
folder-level authorization. These are transport assumptions to verify against the eventual account setup.
References: [Hippius integration](https://docs.hippius.com/storage/s3/integration) and
[implementation compatibility](https://github.com/thenervelab/hippius-s3/blob/staging/docs/s3-compatibility.md).

An S3 readback receipt is not proof of storage-chain inclusion, immutability or finalized publication time.
The localnet has weight commit/reveal disabled. Before enabling it elsewhere, report release timing must
be designed to avoid exposing scores before the intended reveal.
