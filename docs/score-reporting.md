# Signed score reporting

Weights go to Bittensor; raw scores go directly to Hippius. Each validator operator uses its own hotkey and dedicated ACL credential pair, loaded from its private `.env`.

The deployment defaults to Bittensor testnet (`network=test`), netuid `568`, and bucket `validity-testnet`. Signed reports require evaluations by admitted participants.

## Bucket layout

```text
validity-testnet/
  <validator-hotkey>/
    <epoch-start>.json
```

Each file is one signed snapshot of the latest completed round in that epoch and includes **all evaluated miners**. It contains integer score totals, assignment/correct counts, miner hotkeys/UIDs, validator identity, chain genesis, netuid, policy/source context and completion time/block. Raw score is `score_sum / assigned_tasks`. Provider identifiers and evidence are excluded.

Reports use `scope=synthetic-rn`. The [schema and interoperability vector](../protocol/score-reports-v1/README.md) define canonicalization and signatures. Readers verify authorship independently of the bucket token.

## Upload and retry

Set `HIPPIUS_BUCKET=validity-testnet`, `HIPPIUS_ACCESS_KEY_ID`, and `HIPPIUS_SECRET_ACCESS_KEY` in each operator's `.env`. No shared JSON credential bundle or ambient AWS profile is used. The endpoint is `https://s3.hippius.com`, region `decentralized`, with path-style addressing and SigV4.

The default `HIPPIUS_VERIFY_READBACK=true` requires PUT and GET access. It checks for a newer snapshot, writes the signed report, then independently reads and verifies the stored bytes. WRITE-only operation can explicitly disable readback, producing an `upload_accepted` receipt; it cannot establish verified storage or detect an already newer remote snapshot before PUT.

The private journal preserves exact signed envelopes, retry state and destination-specific receipts. Backoff is bounded at five minutes. Newer epoch snapshots supersede older retries. Weight submission runs independently of storage errors. Never share this SQLite database across validators or use it for the combined leaderboard.

Bucket-wide tokens do not isolate directories. Signatures detect alteration and impersonation, but cannot prevent overwrite, deletion or replay of valid older data by another token holder. One active publisher per hotkey is required. A successful S3 operation does not establish decentralized storage finality.

## Independent reader

Create a separate mode-0600 `.env` from [the reader example](../envs/reader/.env.example). It sets the testnet genesis and `HIPPIUS_NETUID=568`; the reader also defaults to netuid `568`. Use a READ-capable token and the actual admitted validator hotkeys. Keep the credentials server-side. The pilot uses an explicit allowlist; admission is not automatically inferred from stake or historic registration.

```sh
cd validator
uv run --frozen python -m validator.reporting.check   --env-file ../envs/reader/.env --epoch-start <epoch-start>
```

This verifies every configured validator's signature in a common source/policy window. Any number of validators is supported. Chain weights must be checked independently.

For a trusted application backend:

```sh
uv run --frozen python -m validator.reporting.reader --env-file ../envs/reader/.env
```

The reader binds to loopback and exposes paginated `GET /v1/reports?epoch_start=...&limit=100`. POST is disabled. A public deployment needs an authenticated, rate-limited TLS reverse proxy. Each report remains attributable to its validator; a missing or invalid report is not a score of zero. The browser must never receive Hippius tokens.
