# Validity miner contract

The testnet pilot evaluates synthetic RN cases. A miner receives a fictional provider query and returns a structured finding with evidence. This repository defines the contract and validator; operators implement their own miner service.

## Serving requirements

Register the miner hotkey on the supplied testnet netuid and advertise a globally routable IP/port with HTTP axon protocol value `4`. Serve **HTTPS** at `POST /v1/evaluate`; do not redirect requests. The server certificate must be trusted by validators and include the registered IP as a subject alternative name. Require each validator's client certificate and verify its hotkey signature and current subnet admission.

The request and response envelopes, signature bytes and replay checks are defined in the [synthetic RN protocol](../protocol/synthetic-rn-v1/README.md). Return a synchronous JSON response before the absolute deadline, with at most 64 KiB and no compression. Sign with the registered miner hotkey. Keep service tokens in a private `.env` and private wallet/TLS keys in protected files.

## Evaluation

The public fictional board snapshots are in [the synthetic dataset](../validator/src/validator/synthetic). Five equal cases cover active, suspended, ambiguous, unavailable and missing records. Return the pinned source version/digest and evidence record IDs. Unavailable sources do not support active findings; ambiguous identities do not establish a license match.

Correct answers earn +1; transport failures 0; ordinary incorrect claims -1; unsafe clean findings or fabricated evidence -5. The mean over all assigned cases is the raw score. Validators submit normalized positive scores to the chain and publish each miner's raw totals in their signed Hippius epoch report.

These public cases test protocol conformance and operation. They are not a competitive real-world verification benchmark. Real board adapters and commercial agency workflows follow the subnet loop.
