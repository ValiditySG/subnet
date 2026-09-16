# Context

Validity is a Bittensor subnet for evidence-backed healthcare credential verification. The MVP serves RNs
at travel nursing agencies, targeting CA, TX, FL, NY and IL. NPs/PAs follow the RN proof; physicians follow
commercial validation. The user's localnet-first direction is authoritative.

Validity's local MVP runs RN evaluation across eight fixture profiles, persists complete rounds and scores,
submits weight batches and verifies them directly on the chain. Real-source adapters and agency workflows follow. Read `subnet_design.md` and `localnet/README.md` first.

## Repository layout

This is a monorepo with two **independent** uv projects plus shared local-development tooling:

- `validator/` — Validity subnet validator (own `pyproject.toml`, `uv.lock`, `.venv`); also holds the
  production `Dockerfile`
- `miner/` — Bittensor subnet miner (own `pyproject.toml`, `uv.lock`, `.venv`)
- `localnet/` — Local subtensor + pylon + bootstrap + miner fixtures for end-to-end development
- `installer/` — Copier-rendered validator installer scripts (`install.sh`,
  `update_compose.sh`, `README.md`); rendered by `copier copy` when adapting the template
- `envs/deployed/` — Copier-rendered production `docker-compose.yml` (validator + pylon);
  the rendered repo is promoted on the `deploy-config-production` branch, with this compose file and the
  installer scripts as the operator-critical files
- `.github/workflows/` — Copier-rendered CI; `build-validator.yml` builds and pushes the validator
  image to a registry on push to `deploy-build-*` branches
- `protocol/localnet-v1/` — local RN request/response JSON schemas and semantics
- `knowledge/` — Bittensor, validator runtime, and localnet domain knowledge
- `docs/` — validator and miner guides (`validator.md`, `miner.md`) and implementation reports

There is **no** top-level Python project and **no** uv workspace. Run `uv sync` inside `validator/` or `miner/`
before working on it (use `--frozen`). There is no global `uv run` from the repo root.

Developer quickstart for the end-to-end dev environment (subtensor + pylon + validator + miner): see
`localnet/README.md`.

Ruff and basedpyright config is duplicated between `validator/pyproject.toml` and `miner/pyproject.toml`. When
changing tooling config, keep both in sync.

## Current implementation workflow

Rendering is complete; do not render this working repository again. The accepted local scope is in
`subnet_design.md`. Follow the user-approved incremental order: local baseline, RN exchange, durable
scoring/weights, failure/transition tests, real-source adapters, website integration. The parent workspace
contains the detailed plan and research. External-source access does not block fixture development.

Use `uv sync --frozen` and `uv run --frozen` within each independent project. The local fixture and smoke
checker run through the miner project's frozen environment. All newly added miner behavior stays under
`localnet/`; the existing `miner/` echo server remains a baseline.

# Knowledge base

## Preparing for tasks

Start by discovering the information available in the knowledge base with `find knowledge -type f | sort`
Crucially: Never summarize index files. Never delegate reading indices to agents or exploration tools. During
your tasks and conversations, eagerly read additional files if they could be relevant. After compaction,
re-read indices directly and read relevant files again so as not to forget crucial details.

## Bittensor domain

Whenever Bittensor domain knowledge is required, focus on the Bittensor knowledge files and skip the rest. It
is important to first understand the specifics of the Bittensor ecosystem, work with high-level concepts, and
iterate on the subnet's design rather than jumping straight into implementation details. Designing a subnet is
a complex reasoning process and requires careful consideration on multiple levels.

Contains, among others:

- how to frame subnet ideas into the bittensor ecosystem
- requirements and invariants that must be satisfied by a good subnet design
- theory behind validation, mining, incentives, miner-validator contract
- suggested external integrations and tools in the ecosystem

Index: knowledge/bittensor/INDEX.yaml

Recommended subnet design location: ./subnet_design.md (create when needed)

## Nexus

Nexus is the framework for building Bittensor subnet validators. It replaces the bittensor SDK for validator
development. All validator code runs inside Nexus — it is the complete runtime. You must use Nexus for
implementing the validator.

Nexus provides a large set of reusable components that handle common validator concerns. Before writing any
code, making any decisions, or responding with recommendations — discover what Nexus offers. It will likely
already handle most of the requirements of the subnet you are working on.

The Nexus knowledge base ships with the Nexus package — find it in `validator/.venv` within the installed
Nexus package under `docs/`. Make sure Nexus is installed first by running `uv sync` in `validator/`. Read
`docs/nexus.md` in the Nexus package — it is the grounding document for all validator implementation work.

Whenever working on validator code, double-check compliance with Nexus's best practices, coding guidelines,
requirements, and correct and optimal usage of Nexus components.

Skip reading Nexus KB for higher level tasks that do not touch the code.

### Pylon

Sidecar subtensor communication proxy. Nexus uses Pylon for all subtensor (blockchain) communication. The pylon
client's source code can be found and inspected in `validator/.venv`.

Skip for higher level tasks that do not touch the code.

### Observability

`envs/deployed/docker-compose.yml` ships a Prometheus-based metrics stack:
`cadvisor` (per-container metrics), `node-exporter` (host metrics), a local
`prometheus` service (image `bittensor_prometheus`) that scrapes `cadvisor`,
the host `node-exporter`, and Pylon's `/metrics` (using the Bearer token from
`PYLON_METRICS_TOKEN`, generated by `installer/install.sh`), and a
`prometheus-proxy` sidecar that remote-writes to `https://prometheus.bactensor.io`.

The validator has credential event and operation-duration instruments in `credentials/pipeline.py`.
It does **not** expose a `/metrics` endpoint or configure a metrics exporter in the current local slice. When you
extend the validator (new payload creators, scorers, nodes, weight setters),
treat metrics as first-class and follow Nexus's own conventions: inspect the
installed Nexus package (`validator/.venv` after `uv sync`, starting from
`docs/nexus.md` and the package sources) to see how Nexus exposes and registers
metrics for its components (actors, engine...), and mirror that
approach when adding observability to your validator. Every new subsystem
should ship with at least one event counter and one latency histogram, named
consistently with the Nexus patterns you find there. If you expose a validator
`/metrics` endpoint, add it into `envs/deployed/docker-compose.yml`
scrape targets and update `installer/README.md`.

#### Distributed tracing

The validator emits OpenTelemetry traces, configured in `validator/src/validator/otel.py`
(rendered to `otel.py`) and wired in from `main()` right after `configure_logging`. Resource
attributes **deliberately carry no operator hotkey** — the observability proxy adds it downstream;
the structlog processors in `logging_config.py` stamp the same attributes onto every log line so logs
and traces correlate.

In deployment the validator exports to a `grafana/alloy` sidecar that tail-samples and forwards to an
OTLP/HTTP upstream (`envs/deployed/alloy/config.alloy`). **`TRACES_UPSTREAM_*` are required by
the sidecar** — Alloy crash-loops on startup without an endpoint and credentials. `update_compose.sh`
keeps both `docker-compose.yml` and `alloy/config.alloy` in sync on operator hosts.

#### Structured logging

The validator logs exclusively via `structlog`. Logging and structlog are configured in
`validator/src/validator/logging_config.py`, tunable via `VALIDATOR_LOGGING_`-prefixed
environment variables.

Skip for higher level tasks that do not touch the code.

## localnet

Local development environment that allows running a subnet locally, as opposed to testnet or mainnet. KB
contains everything needed to set it up and operate it: templates, recipes, requirements, operational
guidelines, best practices, gotchas, and much more.

Index: knowledge/localnet/INDEX.md Localnet resources: localnet/*

Read when working on or debugging issues during development on localnet. Skip for higher level tasks that do
not touch the code.

## Coding guidelines

Location: knowledge/guidelines.coding-and-qa.md

Conventions, tooling, best practices, QA gates, comments, documentation, and more.

Read when working with any kind of code, be it validator, localnet, or any other code in this repository. Skip
for higher level tasks that do not touch the code.

# General hints

- use `uv` instead of `python` for managing dependencies, running scripts, entrypoints, ad-hoc code
    - `uv add ...` / `uv remove ...` / `uv sync` (+ `--all-groups`, `--all-extras`)
    - `uv run --with foo,bar ...` (with temporary dependencies)
    - `uv run python -c '...'` / `uv run some/script.py` (code or script)

# Documentation rules

Keep public documentation and user-facing communication focused on Validity, its RN pilot, and its
implementation. Avoid upstream framework branding, template history, and comparisons. Exact dependency
identifiers and internal runtime guidance remain technical maintenance references, not product messaging.

Keep the root README introductory, with the project website URL (`https://www.validitysg.io`) and links to
the guides. Keep validator/miner setup, configuration, and quality checks in `docs/validator.md` and
`docs/miner.md`. Do not describe a separate website repository in the README.

Keep README.md, AGENTS.md, tests, docstrings, and code up to date and in sync. If one changes, update the
others. Whenever updated, all information, claims, guides, commands, etc. in these files must be verified and
tested. Take great care to avoid drift between these files.


---

Note: CLAUDE.md and .cursorrules both link to CLAUDE.md - they are all the same file. No need to re-read it.
