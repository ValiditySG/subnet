# Validity local credential contract

`validity.localnet.v1` exercises RN license verification with fictional identifiers in the `ZZ-TEST` jurisdiction. The local MVP scores this contract across controlled fixture profiles. The signed public-network protocol remains a later milestone.

The source models are in `validator/src/validator/credentials/protocol.py`. JSON schemas describe field validation. Python validators additionally enforce relationships between identity, source availability, and findings; the validator checks deadlines, request binding and evidence against persisted assignment metadata.

## Exchange

1. The validator posts `{request_id, callback_url, input}` to the fixture's `/task` endpoint. `input` follows `request.schema.json`.
2. The fixture immediately acknowledges with HTTP 202.
3. It posts `{request_id, output, error}` to the supplied local callback. `output` follows `response.schema.json`; failures use `error`.
4. The validator compares the result with the expected case and commits one terminal outcome.

The envelope request ID correlates callbacks; the inner task UUID binds the credential result to its persisted assignment. These identifiers are not signatures. The fixture accepts callbacks only to `http://127.0.0.1:<port>/callback`, and its server binds `127.0.0.2`. No production authentication or remote-source fetching is implemented here.

## Semantics

- The provider query requires profession `RN`, a fictional provider reference, `TEST-*` license number, and `ZZ-TEST` jurisdiction. No NPI is required.
- Exactly one `fixture_license` check is required. Unknown fields, versions and checks are rejected.
- `matched` permits a source-backed `active` or `suspended` finding. `ambiguous` and `not_found` cannot assert either finding.
- An unavailable source leaves identity `unresolved`, with no finding or evidence. It cannot become an active license or a definitive no-match.
- Available evidence identifies the fixture snapshot's SHA-256 digest and matching record IDs. A digest claim alone is insufficient: expected findings and records must also agree.
- Issue/deadline times must be timezone-aware UTC; the deadline must follow issuance. The deadline is checked again when the observation actor processes the result. That timestamp includes any actor-queue delay and is not an ingress-latency measurement.

The public board fixture and separately maintained expected labels live in `localnet/fixtures/`. Expected answers, label versions, and expected digests stay off the request wire. These public cases exercise deterministic scoring and recovery; competitive accuracy on real nursing sources remains unmeasured.

Evidence accepts `fixture-board-v1` and `fixture-board-v2`. Each scored round pins one catalog and digest; source changes void unfinished rounds. See the [scoring policy](../../subnet_design.md) for stale evidence, unsafe assertions and failed work.

## Regenerate schemas

From the subnet repository root:

```sh
uv run --frozen --project validator python - <<'PY'
import json
from pathlib import Path
from validator.credentials.protocol import CredentialRequest, CredentialResponse
for name, model in [('request', CredentialRequest), ('response', CredentialResponse)]:
    schema = model.model_json_schema()
    schema['$schema'] = 'https://json-schema.org/draft/2020-12/schema'
    Path(f'protocol/localnet-v1/{name}.schema.json').write_text(json.dumps(schema, indent=2) + '\n')
PY
```
