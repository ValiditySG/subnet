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

## Implementation

- `assignments` retains requests, pinned truth, source versions, outcomes and completion timestamps, including legacy conformance work.
- `rounds` and `round_slots` allocate each case once per miner and preserve progress across restarts. Only complete rounds produce score summaries.
- `weight_batches` freezes an epoch's proposal before submission and distinguishes preparation, queue acknowledgement and independent chain confirmation.
- The latest complete round must match the current source and hotkey/UID roster and satisfy the ten-minute age limit. Negative scores receive zero reward; positive scores are normalized.
- The local miner resolver reads board fixtures independently of validator labels. Eight registered profiles exercise correct, slow, stale, unsafe, malformed, timeout, late and duplicate behavior.
- Both Python projects retain frozen lockfiles. New miner behavior stays in `localnet/`; the echo baseline remains available.

## Practical limits

All provider data is fictional. These results establish the local mechanism and plumbing, not real nursing-source accuracy, automation rights, production resilience or competitive miner quality.

The local callback protocol is unauthenticated. Processing deadlines include validator queue delay. Pending transport callbacks are not resumed: interrupted slots receive new task IDs, while completed results remain immutable. Chain resets require archiving the old local evaluation state; registration-incarnation tracking across resets is not implemented.

No eligible or positive round pauses new submissions. **Previously written chain weights may remain during a pause.** This MVP does not clear them or guarantee bounded on-chain reward expiry. Public deployment requires a qualified policy for this behavior.

Metrics counters and operation-duration histograms exist; no metrics exporter is configured by default. Public-network deployment and real-source qualification remain subsequent work.

## Repeat the checks

Follow the [localnet guide](../localnet/README.md) for startup, both source phases, live process-recovery checks and exact round replay. See the [design](../subnet_design.md) for the scoring policy and the [validator](validator.md) and [miner](miner.md) guides for quality commands.
