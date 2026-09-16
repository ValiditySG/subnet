# Validator release procedure

Build from a reviewed source revision after the quality gates and container smoke tests pass. The build workflow accepts `deploy-build-*` branches and publishes the validator image. Operator deployment is explicit via installer/update_compose.sh; there are no unattended updates.

Use immutable registry digests in all deployed image references. envs/deployed/docker-compose.yml pins the chain sidecar; VALIDATOR_IMAGE in the private operator .env selects the reviewed validator image by digest. Python and uv base images are pinned in validator/Dockerfile.

A local build proves packaging, not live acceptance. Before promotion verify the registered netuid, subnet parameters, validator identity/permit, compatible miners, a complete evaluation round, direct chain weight readback, real Hippius readback and restart/outage recovery. Do not push a release or mutate chain registration unless authorized.

The runtime uses synthetic RN data and cannot establish real license status. Consult docs/validator.md and docs/score-reporting.md for current operations. No external metrics/traces endpoint is enabled by default. State is a private persistent volume; never discard it during an image update.
