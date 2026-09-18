# Validity

Validity is an RN credential-verification subnet for travel nursing agencies. Initial jurisdiction targets
are CA, TX, FL, NY and IL; these are product targets, not established source coverage. NPs/PAs follow RNs;
physicians follow commercial validation. Public naming is Validity only. Keep README introductory, link
https://www.validitysg.io, and keep operator guides in docs/validator.md and docs/miner.md.

## Active scope

The user moved testing to Bittensor testnet with synthetic RN evaluations and production deployment
practices. Read subnet_design.md and docs/validator.md. Do not revive the retired local chain or same-host
multi-validator harness. The user registered testnet subnet 568; defaults are network=test, netuid=568.
Hotkey registration is handled manually by the user. Do not register hotkeys or spend funds
automatically. Keep actual operator wallet names, the operator roster and registration status out of
documentation and templates. Wallet configuration follows Bittensor defaults: path=~/.bittensor/wallets,
wallet name=default, hotkey name=default. Never reuse prior development journals/wallets.
The target Hippius bucket is validity-testnet. Every operator runs independently with its own hotkey,
ACL token and private recovery journal. Secrets belong in ignored mode-0600 .env files; cryptographic
wallet key files are protected read-only mounts. Never print secrets, resolved compose environments,
wallet seeds, private key files or signed S3 URLs.

Weights go to chain; signed raw scores go directly to real Hippius. Object layout is
<validator-hotkey>/<epoch-start>.json, containing every miner in the latest complete round of that epoch.
Use a separate bucket per network/subnet. Bucket-wide tokens do not establish prefix isolation. Signatures
establish authorship, not availability or truth. Private SQLite journals must not be shared across
operators or used as the combined leaderboard store. Storage failure must not block weights. Test doubles
live only under validator/tests, miner/tests and pylon/tests; deployed code has no local storage backend or upload gateway.
Hippius publishing defaults to enabled. For the user's chain-only testnet checks, HIPPIUS_ENABLED=false
disables the publisher and storage preflight; evaluation and weights still run, without a storage substitute.

Synthetic evaluation is the only implemented RN source. Keep scope=synthetic-rn and the fictional ZZ-TEST
identifiers explicit. Real-source adapters and agency workflows follow. Do not claim that preparation,
a successful PUT, or a queued weight proposal establishes live testnet completion.

## Layout and tools

- validator/: independent Python 3.14 uv project, src, tests, locked dependencies, Dockerfile.
- validator/src/validator/synthetic/: public fictional board snapshots and reviewed expected labels.
- miner/: independent Python 3.14 uv project and runnable synthetic RN miner.
- protocol/: shared Python wire models plus versioned RN exchange and score-report schemas.
- envs/deployed/: one-operator testnet Compose deployment and .env.example.
- installer/: private config initialization and explicit digest-pinned deployment.
- docs/: operator guides. knowledge/: internal domain/runtime references.

Run uv sync --frozen and uv run --frozen inside validator/ or miner/. There is no root uv project.
Before subnet design/code work, read knowledge/bittensor/INDEX.yaml and
knowledge/bittensor/subnet.invariants.yaml directly; re-read indices after compaction. Then read relevant
knowledge files. The user explicitly requires runnable miner code for this pilot, overriding the generic
validator-only reference guidance. Maintain miner/ as a synthetic testnet reference implementation,
independent of validator expected-answer labels. Do not add real RN-source claims or restore a local chain.

## Runtime requirements

Use the installed Nexus actor runtime and Pylon chain sidecar. Before validator edits read
validator/.venv/lib/python3.14/site-packages/nexus/docs/nexus.md and discover the public nexus.v1 APIs.
Import only versioned public runtime modules. The user explicitly requires the latest stable Bittensor SDK;
use bittensor==11.1.0 and its bundled wallet API instead of the superseded bittensor-wallet package.
The Validity Pylon image also uses bittensor==11.1.0 for its wallet API. Explicit patches migrate Pylon
2.3.2 and TurboBT 1.3.1 imports/dependencies and convert signing payloads to bytes. Both packages are rebuilt
with a +validity.sdk11 suffix; the legacy wallet package is removed. Dependency and offline compatibility
checks run during the image build. Keep the SDK pin aligned across all projects, pylon/sdk-requirements.in,
both source patches and their test. Build pylon/Dockerfile and deploy its immutable image through
PYLON_IMAGE. See docs/chain-service.md.
Keep chain operations in the actor-owned chain-service integration. Do not create background work outside
actor ownership. Reuse built-in components and public extension points.
CredentialHTTP extends the public communicator: synchronous HTTP plus hotkey signatures, bounded
responses, no redirects/proxy discovery, registered public IP endpoints, no public callback listener.
The user requires plain HTTP only between miners and validators, with hotkey-based participant
authentication. Do not introduce TLS, HTTPS, certificates or TLS key mounts for this connection.
Signatures and admission/replay checks remain mandatory. Payloads are unencrypted and strictly synthetic;
real credential data requires a separate confidentiality design. The miner's default HTTP port is 8080.
One request is in flight; durable assignments recover after restart. Weight and report clocks use separate
contexts. Do not replace durable state with process memory. One journal lock prevents duplicate processes
on a host; operators must also ensure only one active host uses their hotkey.

Keep structured logging and OpenTelemetry event counters/duration histograms for each subsystem. Do not
log credential validation inputs or untrusted response bodies. Default deployment has bounded local logs
and a heartbeat healthcheck, no external telemetry forwarding. Trace export remains optional.

## Quality gates

Read knowledge/guidelines.coding-and-qa.md. Use Python 3.14, strict basedpyright and no weakened rules.
Run in order: ruff check --fix; ruff format; basedpyright; pytest -q --tb=line -r f.
Exercise security boundaries, durable retries, chain identity gates and real signed HTTP exchanges. Keep docs,
schemas, examples and tests aligned. Build and smoke-test the container before describing it as usable.
Live chain/Hippius checks require the actual netuid, registered wallets, public miner endpoints and authorized tokens.
