# Signed score report v1

Each file contains the latest completed synthetic RN round for one validator in one epoch. Scope is `synthetic-rn`. See [the schema](signed-score-report.schema.json), [public-key interoperability vector](test-vector.json), and [operations guide](../../docs/score-reporting.md).

Canonical bytes are UTF-8 JSON with recursively sorted keys, compact separators, ASCII escaping, all report fields included and integer score totals. `report_id` is the lowercase SHA-256 digest of those bytes. Sign `b"validity.score-report.v1\n" + canonical_report` with the validator SR25519 hotkey; encode the signature as lowercase hexadecimal.

The object key is `<validator-hotkey>/<epoch-start>.json`. Verify the signature, digest, admitted author, expected chain/subnet, object path, policy and source context. Scores are `score_sum / assigned_tasks`. Keep every validator's reports separate; missing reports are not zero scores. A valid signature proves authorship, not truthful scoring or latest-state freshness.
