# Synthetic RN exchange v1

`validity.synthetic-rn.v1` uses fictional RN identifiers in `ZZ-TEST`. The transport envelope is `validity.rn-exchange.v1`. This protocol is explicitly synthetic and is not a real-board verification API.

## HTTPS exchange

The miner registers its public IP/port with axon protocol `4` and accepts `POST /v1/evaluate` over mutual TLS. Its server certificate must include that IP in its subject alternative names. The validator supplies a trusted client certificate. Both participants also authenticate messages using their registered SR25519 hotkeys.

The HTTP request is `SignedRequest` from [signed-request.schema.json](signed-request.schema.json):

```json
{"task":{"version":"validity.rn-exchange.v1","chain_genesis":"0x...","netuid":123,"validator_hotkey":"...","miner_hotkey":"...","request":{}},"signature":"..."}
```

`request` follows [request.schema.json](request.schema.json). The netuid above is illustrative. Use the actual registered subnet. Canonical bytes are UTF-8 JSON with recursively sorted keys, compact separators, ASCII escaping, and all schema default fields included. Timestamps use UTC and the serializer's `Z` representation. Sign `b"validity.rn-request.v1\n" + canonical(task)`; encode the 64-byte SR25519 signature as lowercase hexadecimal.

The miner validates TLS, signature, chain/subnet, expected miner hotkey, registered validator admission and the task's absolute deadline before processing. Reject reused task IDs or return the original response idempotently. Never accept an old response as a new assignment.

Return HTTP 200 with [signed-response.schema.json](signed-response.schema.json):

```json
{"result":{"request_hash":"...","response":{}},"signature":"..."}
```

`request_hash` is lowercase SHA-256 of `canonical(task)`, binding both hotkeys, chain, subnet and complete request. `response` follows [response.schema.json](response.schema.json). Sign `b"validity.rn-response.v1\n" + canonical(result)` with the miner hotkey. Match the request task ID and provider reference. Maximum response size is 64 KiB; no compression or redirects. Return before the absolute UTC deadline. Validators accept only the first terminal result; an interrupted assignment is retried with a fresh task ID.

## Data and scoring

Public board snapshots and separate reviewed labels are in `validator/src/validator/synthetic/`. Requests exclude expected answers, label version and expected digest. The five cases exercise active, suspended, ambiguous, unavailable-source and no-match outcomes. Their public nature permits memorization; this phase measures operation and conformance, not real-world competitive accuracy.

Expected availability, findings and evidence must match. Unavailable sources cannot support findings; unmatched or ambiguous identities cannot support a clean license claim. See [the subnet design](../../subnet_design.md) for the reward policy.
