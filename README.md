# Validity

Validity is a Bittensor subnet for evidence-backed healthcare credential verification. Miners collect source evidence, and validators evaluate the reliability of their findings.

[Website](https://www.validitysg.io)
[X (Twitter)](https://x.com/validitysg)

## RN-first MVP

The MVP focuses on **registered nurses at travel nursing agencies**, targeting California, Texas, Florida, New York, and Illinois. RNs prove the verification loop, NPs and PAs prove the revenue model, and physicians follow commercial validation.

The local MVP verifies fictional RN cases, scores miner responses, submits chain weights and preserves results across restarts. Real-source coverage and agency workflows follow. See the [implementation report](docs/localnet-implementation.md) for the current milestone.

## Documentation

- [Validator guide](docs/validator.md) — setup, configuration, persistence, and checks.
- [Miner guide](docs/miner.md) — RN fixture, local operation, and protocol.
- [Localnet guide](localnet/README.md) — start and verify the complete development environment.
- [Subnet design](subnet_design.md) — current scope and implementation sequence.
- [Local RN protocol](protocol/localnet-v1/README.md) — request, response, and evidence semantics.
