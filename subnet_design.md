# Validity subnet design: local RN MVP

Validity measures **the reliability of evidence-backed provider-status findings**. The initial market is RNs at travel nursing agencies in California, Texas, Florida, New York and Illinois. RNs prove the loop; NPs and PAs prove revenue; physicians follow commercial validation.

The local MVP completes the subnet loop first. Agency rosters, reviews and alerts follow.

## Evaluation loop

1. Discover registered local HTTP miners through the chain sidecar.
2. Open a durable round with a fixed hotkey/UID roster, policy version and independently reviewed RN source catalog.
3. Assign each miner each of the five cases once, in deterministic case order. Commit the request and expected evidence before HTTP dispatch. Limit outstanding tasks to four.
4. Accept one terminal result per task. A response must match its task, provider, complete check set, finding and evidence. Deadlines include validator processing delay.
5. Close the round only after every case has a scored outcome. Freeze its score summary and retain all observations for replay.
6. At the chain's weight opportunity, persist an immutable epoch proposal and submit it through the validator's weight setter. Recheck source, age and current hotkey/UID bindings before submission.
7. Use `localnet/mvp.py` to read weights directly from subtensor and compare them with the proposal. A sidecar acknowledgement means queued, not confirmed.

Legacy conformance assignments stay in their original table and do not enter reward rounds. Each validator owns one SQLite database. Local chain resets require archiving the old evaluation state; identity continuity across independent chain runs is outside this harness.

## Score publication

Weights go to the chain; validators upload signed score summaries directly to a shared Hippius bucket.
The Hippius team confirmed dedicated ACL user tokens per validator team, and the user generated three test
credentials. Each validator holds its own team's upload credentials and signs reports with its own hotkey.
ACL credentials authorize storage access; hotkey signatures establish report authorship for later verification.
The test bucket is `localnet`, with one bucket-wide WRITE token per team, loaded from private profiles in
`localnet/hippius-credentials.json`. These grants do not isolate validator prefixes. Signatures detect altered
reports but do not prevent another writer from overwriting or deleting objects. Live GET/LIST checks
passed for all three profiles. For this test, `owner` is assigned to validator 3 and also verifies readback.

Production validators run independently on separate operator machines. Same-machine validator identities
are a development test arrangement only. Hippius integration tests use actual Hippius storage for reports
from multiple validators. No shared SQLite database or local object-store substitute serves that role.

Each completed round records its completion block and epoch. An independent publisher audits the round,
persists an immutable signed envelope, and retries uploads without blocking evaluation or weight submission.
Each validator's private SQLite database remains its own recovery journal for tasks, frozen proposals and
unacknowledged reports. The shared score service and leaderboard read published report objects; neither chain
weights nor published score summaries can reconstruct in-flight work or all observations.

The bucket layout is `<validator-hotkey>/<epoch-start>.json`. Each file contains all miner scores from the
latest completed round in that epoch, with its signature, digest and chain/subnet context. Later rounds
replace that epoch's snapshot; older retries are superseded in the private outbox. Use one bucket per
network/subnet. WRITE-only credentials receive an
upload-accepted receipt; optional immediate readback requires READ access. The reader verifies stored
signatures, digests, object paths and an explicit localnet hotkey allowlist before serving reports. Individual
scores remain separate. Delivery status is scoped to the destination, including its bucket. Neither upload
acceptance nor verified readback confirms chain weights or storage-chain finality. The original upload
gateway and JSON object store remain development-only test substitutes.
See the [reporting guide](docs/score-reporting.md) for the protocol, local setup and remaining integration work.

## Scoring policy: `rn-equal-cases-v1`

Each of the five cases has a fixed 20% share. All assigned valid cases count, including failures. The round score is the mean of these five outcomes; there is no speed bonus or moving average.

| Outcome | Score |
| --- | ---: |
| Correct, supported finding, ambiguity, source outage or no-match | +1 |
| Missing, malformed, late, timed-out response; unjustified unavailability | 0 |
| Evidence explicitly claiming an older source version | 0 |
| Incorrect noncritical finding | −1 |
| Wrong task/provider, fabricated current evidence or records, unsafe active assertion | −5 |

Unsafe active assertions retain the severe penalty even when they cite stale evidence. Scores below zero become zero reward. Normalize positive rewards to sum to one. A miner that only answers easy cases retains failures in its denominator.

A changed source catalog or hotkey/UID roster voids an unfinished round. Its observations remain available for inspection. Validator process loss closes pending assignments as interrupted; their round slots are retried with new task IDs. Completed tasks cannot be replaced by late or duplicate callbacks. Known remote failures count as zero; validator-side faults are retried.

Only the latest complete round is eligible, and only for its current source and roster. The default maximum age is ten minutes. Missing coverage, changed context, stale scores or no positive scores pause new submissions. **Previously written chain weights can remain during a pause.** This local implementation does not clear chain weights or claim bounded on-chain reward expiry. An epoch proposal stays fixed across transport retries; an invalidated proposal waits for a later epoch.

## Fictional source transitions

The public fixtures use RN identifiers in `ZZ-TEST`. Source version v2 changes an active record to suspended, a suspended record to active, restores a previously unavailable record, and makes another lookup unavailable. The ambiguous case remains ambiguous. Miners resolve board records; validators read a separately maintained answer catalog. An atomic pointer selects both versioned files.

Profiles cover correct, slow correct, stale, unsafe active, malformed, silent timeout, late, and duplicate responses. Public fixtures prove plumbing and deterministic incentives; they do not measure competitive accuracy on real nursing data.

## Scope after the local loop

Qualify authorized Nursys/state-board access one target state at a time, including status, expiry, public discipline and source-reported practice privileges. License state, destination state and compact privilege are distinct. NPI may enrich identity; it is not required. OIG screening is a separate check.

Source coverage, automation rights and freshness must be established per check. The five states are a product target, not a verified integration or market-share claim. Real expiry/renewal semantics, authenticated public transport, registration-incarnation tracking, production reward policy and deployment qualification remain ahead of external use.

See the [localnet guide](localnet/README.md), [validator guide](docs/validator.md), [miner guide](docs/miner.md) and [verification report](docs/localnet-implementation.md).
