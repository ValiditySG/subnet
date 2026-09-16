# Validity localnet MVP verification

Verified locally on 2026-09-15 UTC. The subnet loop covers fictional RN requests, independent expected labels, balanced assignment, evidence verification, persisted scores, local-chain weights and recovery. Agency workflows follow this loop.

## Measured results

| Check | Observed result |
| --- | --- |
| Baseline source v1 | Round 1: honest, slow, stale and duplicate profiles score 1; each receives 25% on-chain |
| Direct baseline readback | Subnet 2, validator UID 1; observed block **167060**, weight update block **167057** |
| Source transition v2 | Round 3: stale falls to −1 and receives zero; honest, slow and duplicate each receive one third |
| Direct transition readback | Observed block **168425**, weight update block **168243** |
| Unsafe and failed responses | Unsafe score −0.2; malformed, silent timeout and late profiles score 0; all receive zero weight |
| Coverage and duplicates | Exactly five distinct cases per miner per completed round; duplicate callbacks earn no extra score |
| Source change during work | Unfinished round 2 becomes void; its persisted observations remain available |
| Validator restart | Two live restarts preserved 100 terminal records each and recovered in-flight work as interrupted |
| Miner process loss | Stopped slow miner produced a failed assigned task; round 11 retained it in the denominator (score 0.8); restarted endpoint returned a verified response |
| Final recovery readback | Fresh round **12**, observed block **172937**, weight update block **172936**; correct profiles return to one third each |
| Audit replay | All eleven completed rounds through round 12 recompute exactly from persisted assignments and responses |
| Unit tests | **48 pass**, covering contracts, complete failure denominators, severe penalties, source changes, retries, replay and invalidated weight contexts |
| Static checks | Ruff and strict basedpyright pass for validator and local fixture tooling |

The acceptance checker compares the live chain's hotkey/UID roster, validator permit, subnet activation, last-update block and weight row with the persisted proposal. It bypasses the sidecar when reading chain weights. It allows only u16 quantization tolerance. A queued sidecar response alone does not pass.

Raw reports are gitignored under `localnet/state/`: `baseline.json`, `transition.json`, `restart-report.json` and subsequent recovery reports. Block numbers, ports and round IDs above are observations, not configuration.

## Signed reporting verification — 2026-09-16

Reporting was enabled on the existing local chain with the **local storage backend**. At the time of this
run, Hippius access was awaiting confirmation; the subsequent direct-upload setup is recorded below.
No live Hippius upload was performed in this run.

| Check | Observed result |
| --- | --- |
| Validator restart with reporting enabled | Preserved the latest 100 terminal records and recovered one in-flight assignment; round before restart 219 |
| Live score publication | Round **220**, eight miners with five assigned cases each; completion block **281498**, report epoch **281215** |
| Signed object readback | SR25519 signature and content address verified; all aggregate scores match independent observation replay, allowing floating-point representation tolerance |
| Local upload acknowledgement | `2026-09-16T03:32:04.850736Z`; report ID `64018138bf2b90a8e86ceff2bd983778ce6a64b2bde08b7a7c841a6da91cfad4` |
| Weights with reporting active | Same round 220 confirmed at block **281600**, validator last update **281596**, weight epoch **281576** |
| Reward behavior | Honest, slow and duplicate receive one third each; stale, unsafe and failed profiles receive zero |
| Multiple report authors | Automated HTTP test accepts three independent validator signatures into separate paths and reads all three through pagination |
| Failure recovery | Automated tests cover a storage outage, lost acknowledgement, restart with byte-identical retransmission, and continued weight-proposal preparation |
| Quality gates | **58 tests pass**; Ruff and strict basedpyright pass for validator and changed localnet tooling |

The live run uses one registered chain validator; the three-author transport test does not claim three live
chain validators. That automated test uses a separate SQLite journal for each identity and a local JSON
object-store substitute. It establishes transport behavior only; Hippius integration testing must use
actual Hippius report storage. Production validators run independently on separate operator machines.
The publisher uses a dedicated clock so network uploads cannot hold the evaluation or
weight actors' event-context locks. Generated evidence is in `localnet/state/report-publication.json` and
`localnet/state/score-reporting.json`. The live storage verification appears below. See
[score reporting](score-reporting.md) for the remaining leaderboard connection and public-network admission.

## Implementation

### Direct ACL upload update

Following the gateway test above, the user confirmed dedicated ACL user tokens with the Hippius team and
generated three test credentials. The publisher now supports direct Hippius uploads with a distinct
credential profile for each validator team, retaining hotkey signatures in every report. The HTTP upload
gateway remains a development test option; the Hippius reader rejects HTTP uploads.

The updated validator suite has **74 passing tests**, including three isolated credential profiles, three
hotkey signatures, later reader verification, WRITE-only uploads, ACL denial without fallback, tampering
rejection, destination-specific receipts, JSON credential isolation from ambient AWS settings and sanitized
errors for malformed credential files, flat JSON profiles, and fresh-score priority during historical
backfill, one-file-per-epoch replacement, and rejection of stale retries after restart. Ruff and strict
basedpyright pass for the validator and changed localnet Python files. The automated
tests use stubbed S3 responses or local test fixtures; the separate live results below establish Hippius access.

The user confirmed bucket `localnet`, bucket-wide WRITE grants for each token and the private credential
path `localnet/hippius-credentials.json`. Named JSON profiles and AWS INI files are supported; an example
JSON with the three profile names is checked in. The private credential file is ignored by Git and has
permissions `0600`. It contains `owner`, `validator-1` and `validator-2`; the user approved `owner` for the
third test validator. All three profiles passed actual GET/LIST checks.
The original live chain/gateway observations above remain historical evidence for that earlier configuration.

- `assignments` retains requests, pinned truth, source versions, outcomes and completion timestamps, including legacy conformance work.
- `rounds` and `round_slots` allocate each case once per miner and preserve progress across restarts. Only complete rounds produce score summaries.
- `weight_batches` freezes an epoch's proposal before submission and distinguishes preparation, queue acknowledgement and independent chain confirmation.
- The latest complete round must match the current source and hotkey/UID roster and satisfy the ten-minute age limit. Negative scores receive zero reward; positive scores are normalized.
- The local miner resolver reads board fixtures independently of validator labels. Eight registered profiles exercise correct, slow, stale, unsafe, malformed, timeout, late and duplicate behavior.
- Both Python projects retain frozen lockfiles. New miner behavior stays in `localnet/`; the echo baseline remains available.

### Three-validator Hippius verification — 2026-09-16

Three live validator processes evaluated the same eight fictional RN miner profiles. Each used its own
hotkey, sidecar and private SQLite recovery journal. Shared report objects were stored in the real
Hippius bucket `localnet`, with no local object-store fallback.

| Wallet | Hippius profile | Chain UID | Weight round | Observed chain block |
| --- | --- | ---: | ---: | ---: |
| `validator` | `validator-1` | 1 | 469 | 406405 |
| `validator-2` | `validator-2` | 10 | 5 | 406454 |
| `validator-3` | `owner` | 11 | 5 | 406449 |

All three weight rows were last updated at block **406143**. Each assigned one third to honest, slow and
duplicate miners (UIDs 2, 3 and 8), and zero to the other profiles. Independent score checks found honest,
slow and duplicate at 1, stale at −1, unsafe at −0.2 and failed responses at 0, with five cases per miner.

At **2026-09-16T14:12:12Z**, the independent Hippius reader verified all three hotkey signatures in report
epoch **405399**, policy `rn-equal-cases-v1`, and source digest
`5c2b82d92a65081f46b0a1e761b6cae290eb74b96829fd50111b1f5d7e3a5e21`:

| Wallet | Report round | Report ID |
| --- | ---: | --- |
| `validator` | 469 | `30dbd6e6b40cc28b856de49f9f41cfc48089ca59c91152eaac4641823b5c132d` |
| `validator-2` | 4 | `986fcb448c466f3445f82ab748691b5d74cb36a047e02641cf8c9267744ad898` |
| `validator-3` | 4 | `bde943c0cb52e636b0c2499bd4f3accb0ef738283cf86d46d5bbaa08dad2a6f5` |

The score-report epoch and the weight epoch (**406121**) are separate observations. The table does not
claim every report is the exact round selected for that weight update. The HTTP reader on port 8091
also returned three verified reports from Hippius for epoch 405399. Its local development-gateway upload
route is disabled.

Restarting the first validator for direct Hippius publication preserved 100 terminal results and recovered
one in-flight assignment. A later restart loaded fresh-score priority; subsequent current-round uploads
and chain weights passed. Older reports remain queued for historical backfill until acknowledged.

Evidence files under the ignored `localnet/state/` directory are `hippius-first-readback.json`,
`hippius-readback.json`, `hippius.json`, `hippius-validator-2.json`, `hippius-validator-3.json` and
`restart-report.json`. The [score reporting guide](score-reporting.md#three-validators-on-the-test-host)
documents the wallet/profile mapping, sidecars and verification commands. Live website data wiring remains
a separate integration step.

### Simplified bucket layout — 2026-09-16

The bucket now uses `<validator-hotkey>/<epoch-start>.json`, one signed file containing all miners' scores
from the latest completed round in that epoch. Chain/subnet context and the content hash remain inside
the signed report. Older same-epoch snapshots are retained in each validator's private journal and marked
superseded so they cannot overwrite newer scores on retry.

At **2026-09-16T14:41:16Z**, the independent reader verified epoch **411897** from all three hotkey folders,
with eight miners in each file. The HTTP reader returned the same report IDs. The rounds were 482 for the
first validator and 17 for each additional validator. Evidence is in
`localnet/state/hippius-layout-readback.json`.

The migration verified **279** reports at their new hotkey/epoch paths before removing **279** old nested
copies, completing at **2026-09-16T14:42:23Z**. Every legacy report mapped to a distinct epoch file; no
reports were merged or discarded. The migration used actual Hippius reads, writes and signature checks.
Its inventory and object mapping are recorded in `localnet/state/hippius-layout-migration.json`.

## Practical limits

All provider data is fictional. These results establish the local mechanism and plumbing, not real nursing-source accuracy, automation rights, production resilience or competitive miner quality.

The local callback protocol is unauthenticated. Processing deadlines include validator queue delay. Pending transport callbacks are not resumed: interrupted slots receive new task IDs, while completed results remain immutable. Chain resets require archiving the old local evaluation state; registration-incarnation tracking across resets is not implemented.

No eligible or positive round pauses new submissions. **Previously written chain weights may remain during a pause.** This MVP does not clear them or guarantee bounded on-chain reward expiry. Public deployment requires a qualified policy for this behavior.

Metrics counters and operation-duration histograms exist; no metrics exporter is configured by default. Public-network deployment and real-source qualification remain subsequent work.

## Repeat the checks

Follow the [localnet guide](../localnet/README.md) for startup, both source phases, live process-recovery checks and exact round replay. See the [design](../subnet_design.md) for the scoring policy and the [validator](validator.md) and [miner](miner.md) guides for quality commands.
