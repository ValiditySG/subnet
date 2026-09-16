# Validity subnet design

## What is measured

Validity measures the correctness of evidence-backed RN credential findings.

The first market is travel nursing agencies. RNs prove the loop; NPs/PAs prove the revenue model; physicians prove the moat. CA, TX, FL, NY and IL are initial jurisdiction targets. Source access and coverage require separate verification.

## Current release: synthetic evaluation on testnet

Validity is registered on Bittensor testnet as subnet `568`. Runtime and deployment defaults are `network=test`, `netuid=568`.

The data is fictional and uses `ZZ-TEST`, `TEST-*` license identifiers, and `scope=synthetic-rn`. Five equally weighted cases exercise active, suspended, ambiguous, unavailable-source and no-match outcomes. This release tests distributed operation and recovery. It does not measure real board verification accuracy.

1. A validator reads registration and public miner endpoints through its chain sidecar.
2. It persists a balanced round and each assignment before dispatch.
3. It sends a hotkey-signed request to the miner's plain HTTP endpoint. The miner authenticates the validator's signature and subnet admission. This connection uses no TLS, HTTPS, or certificates; synthetic payloads travel unencrypted.
4. It verifies the miner signature, exact request binding, deadline and independently reviewed expected result.
5. It persists the score and prepares weights only from a complete current round.
6. The chain sidecar submits weights. A queued acknowledgement is distinct from independent on-chain confirmation.
7. A separate actor signs the round summary and uploads it to Hippius at `<hotkey>/<epoch-start>.json`.

The validator, weight clock and report publisher use independent runtime contexts. Storage retry delays cannot block chain weights. A private journal preserves assignments, completed rounds, exact weight proposals, signed envelopes and upload receipts. One process holds an exclusive journal lock.

## Scoring

Each miner receives all five cases. Correct answers earn +1; unavailable transport earns 0; ordinary incorrect claims earn -1; unsafe clean findings or fabricated evidence earn -5. Source-transition handling checks the claim before stale-evidence treatment. Raw score is `score_sum / assigned_tasks`.

Positive scores are normalized into weights. No eligible positive complete round means no new weights. Previously stored chain weights can remain. Stale source, roster, UID or completion time invalidates a proposal. A recovered interrupted assignment gets a fresh task ID; late or duplicate results cannot replace a terminal result.

## Trust and storage

Every operator has a distinct wallet and Hippius ACL token in its own environment. `validity-testnet` is the designated bucket. Tokens have bucket-wide access; one validator can overwrite another's object at the storage layer. Readers verify signatures, chain/subnet, admitted hotkey and object path before serving data. Invalid objects are omitted. This detects corruption and impersonation but cannot prevent deletion, replay or denial of service by a credential holder.

One epoch file contains the latest complete round, including every miner score. Original signed reports remain in the validator's journal; superseded retries cannot overwrite newer snapshots. One active publisher per hotkey is required. There is no atomic cross-host writer coordination. S3 acceptance and verified readback do not prove decentralized storage finality.

The pilot reader uses an operator-maintained validator hotkey allowlist. It preserves each validator's scores separately and never averages them implicitly. Historical registration proof, permission isolation and replay-resistant external anchoring remain later work. Keep reader credentials server-side; the browser receives verified reports only.

## Deployment gates

The deployment is testnet-only, uses the verified testnet genesis, and defaults to netuid `568`. Operators must still configure the actual subnet tempo. Each operator starts with a fresh state volume and registered hotkey; validators also need a validator permit. Miner communication uses signed HTTP without certificates. Participant hotkeys must be registered before starting the evaluation loop. No subnet registration, stake transfer, hyperparameter mutation or bucket creation occurs automatically.

Completion on testnet requires a full evaluation round, independently confirmed weight rows, verified Hippius readback from all expected validators, and a restart/outage recovery exercise. The runnable synthetic reference miner in `miner/` implements the signed HTTP exchange using the shared protocol package. Its deployment and admission requirements are in `docs/miner.md`.

See [validator operations](docs/validator.md), [miner contract](docs/miner.md), and [score reporting](docs/score-reporting.md).
