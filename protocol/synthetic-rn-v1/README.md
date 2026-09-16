# Synthetic RN exchange v1

`validity.synthetic-rn.v1` uses fictional RN identifiers in `ZZ-TEST`. The transport envelope is `validity.rn-exchange.v1`. This protocol is explicitly synthetic and is not a real-board verification API.

Both runnable roles use the Python models in [`validity_protocol`](../src/validity_protocol). See the [miner guide](../../docs/miner.md) for the reference server and deployment instructions.

## Signed HTTP exchange

The miner registers its public IP/port with axon protocol `4` and accepts `POST http://<registered-ip>:<port>/v1/evaluate` over **plain HTTP only**. The miner's default port is `8080`. This connection uses no TLS, HTTPS, or certificates. Both participants authenticate messages using their registered SR25519 hotkeys; miners also check current validator admission. Signatures protect message authenticity and integrity, not confidentiality: the fictional RN payloads travel unencrypted.

The HTTP request is `SignedRequest` from [signed-request.schema.json](signed-request.schema.json):

```json
{"task":{"version":"validity.rn-exchange.v1","chain_genesis":"0x...","netuid":568,"validator_hotkey":"...","miner_hotkey":"...","request":{}},"signature":"..."}
```

`request` follows [request.schema.json](request.schema.json). The deployment defaults are `network=test`, `netuid=568`. Signed messages always include the chain genesis and netuid explicitly; readers never infer missing signed fields from deployment defaults. Canonical bytes are UTF-8 JSON with recursively sorted keys, compact separators, ASCII escaping, and all schema default fields included. Timestamps use UTC and the serializer's `Z` representation. Sign `b"validity.rn-request.v1\n" + canonical(task)`; encode the 64-byte SR25519 signature as lowercase hexadecimal.

The miner validates the signature, chain/subnet, expected miner hotkey, registered validator admission and the task's absolute deadline before processing. Reject reused task IDs or return the original response idempotently. Never accept an old response as a new assignment.

Return HTTP 200 with [signed-response.schema.json](signed-response.schema.json):

```json
{"result":{"request_hash":"...","response":{}},"signature":"..."}
```

`request_hash` is lowercase SHA-256 of `canonical(task)`, binding both hotkeys, chain, subnet and complete request. `response` follows [response.schema.json](response.schema.json). Sign `b"validity.rn-response.v1\n" + canonical(result)` with the miner hotkey. Match the request task ID and provider reference. Maximum response size is 64 KiB; no compression or redirects. Return before the absolute UTC deadline. Validators accept only the first terminal result; an interrupted assignment is retried with a fresh task ID.

## Data and scoring

Public board snapshots and separate reviewed labels are in `validator/src/validator/synthetic/`. Requests exclude expected answers, label version and expected digest. The five cases exercise active, suspended, ambiguous, unavailable-source and no-match outcomes. Their public nature permits memorization; this phase measures operation and conformance, not real-world competitive accuracy.

Expected availability, findings and evidence must match. Unavailable sources cannot support findings; unmatched or ambiguous identities cannot support a clean license claim. See [the subnet design](../../subnet_design.md) for the reward policy.
