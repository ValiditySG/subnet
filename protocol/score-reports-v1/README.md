# Validity signed score reports v1

This protocol is limited to completed fictional RN rounds (`scope=localnet-fixture`). Its JSON schema is
[`signed-score-report.schema.json`](signed-score-report.schema.json). The schema describes shape; consumers
must also validate context, unique miner hotkeys/UIDs, policy score bounds, digest and signature.

## Signed envelope

The envelope contains `report`, `report_id`, `signature_algorithm=sr25519` and a 64-byte signature encoded
as 128 lowercase hexadecimal characters. The report contains its chain genesis, netuid, validator hotkey,
persisted validator instance UUID, local round ID, epoch bounds, completion block, policy/source versions,
source digest, UTC timestamps and miner totals. It contains no weights or provider data.

Miners are sorted by hotkey. Each entry has `miner_hotkey`, historical `miner_uid`, integer `score_sum`,
`assigned_tasks` and `correct_tasks`. The raw score is the sum divided by assigned tasks. Reward clamping
and normalization belong to weight calculation, never this signed transport.

## Canonical bytes and signatures

The reference codec is `validator.reporting.protocol.ScoreReport.canonical_bytes()`:

1. Validate using `ScoreReport`; include all defined report fields, including defaults.
2. Serialize the validated model in JSON mode: UUIDs are lowercase hyphenated strings; UTC datetimes use
   `YYYY-MM-DDTHH:MM:SSZ`, or six fractional digits when microseconds are nonzero. Integer fields stay integers.
3. Encode JSON with recursively sorted object keys, no whitespace, ASCII escaping and UTF-8.
4. `report_id = lowercase_hex(SHA256(canonical_bytes))`.
5. Sign the literal UTF-8 prefix `validity.score-report.v1` followed by one newline and the canonical bytes,
   using the validator's SR25519 hotkey.

Verify the digest and signature against the report's `validator_hotkey`. Validate chain/subnet context and
admission independently. A signature proves authorship, not honest evaluation. The generated test vector
uses a public development key and is for interoperability only.

## Transport

Validators upload envelopes up to 256 KiB directly to Hippius using their teams' dedicated ACL credentials.
The hotkey signature remains part of the stored envelope, independently verifiable after credential rotation.
Object keys are `<validator-hotkey>/<epoch-start>.json`: a hotkey directory containing one file per epoch,
with all miners' scores from that epoch's latest completed round. Epoch filenames use the first block,
not a round number. Later rounds update the same file. The signed envelope retains chain/subnet context,
source, report hash and signature; it does not change the scoring policy or combine rounds into a new score.
Use one bucket per network/subnet. See the [reporting guide](../../docs/score-reporting.md#reports-and-leaderboard-reads).

Local delivery metadata includes `report_id`, derived `object_key`, `stored_at` (client acknowledgement time)
and `verification`: `upload_accepted` after PUT, or `readback_verified` after GET and cryptographic checks.
WRITE-only credentials can use upload acceptance; later verification uses a reader with READ permission.
Neither receipt type proves storage-chain finality or trusted publication time. Delivery status is scoped
to the destination and layout (`#hotkey-epoch-v1`) so a local-test or old-layout acknowledgement never
satisfies publication at the current paths. Older same-epoch rounds are marked superseded, never retried
over a newer snapshot. Their original envelopes remain in the validator's private journal.

The development-only `POST /v1/reports` gateway returns a readback-verified receipt. Invalid reports return 400,
unadmitted identities/context 403, invalid payload size 413 and storage failures 503. Retrying exactly the
same signed report is safe after timeouts or a lost acknowledgement. The publisher persists its envelope
before making network requests. The reader service disables HTTP uploads in Hippius mode (405).

`GET /v1/reports?epoch_start=N&limit=1..100&token=...` returns `reports` and `next_token`. The service verifies
each object and omits corrupt or misplaced reports with a log/metric. Storage failures return 503. The local
reader fetches `<hotkey>/<N>.json` for each configured hotkey, with pagination through that allowlist.
It uses the same allowlist as admission; removing a hotkey also hides its prior reports from
this endpoint. Production historical-registration policy is intentionally not defined here.

Publication windows and weight epochs are distinct: a report belongs to the epoch containing round
completion. A later weight opportunity can use that eligible round. An upload receipt does not confirm
that the report supplied any particular chain weight batch.
