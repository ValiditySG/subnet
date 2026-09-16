# Validity miner

Runnable synthetic RN reference miner for Bittensor testnet. Defaults: `network=test`, `netuid=568`. Participant hotkey registration is required before startup.

- [Operator setup and deployment](../docs/miner.md)
- [Service entry point](src/validity_miner/main.py)
- [Synthetic record resolver](src/validity_miner/resolver.py)
- [Signed exchange protocol](../protocol/synthetic-rn-v1/README.md)

Use a private `.env` based on `.env.example`. From this directory, install with `uv sync --frozen` and run `uv run --frozen miner --env-file .env` after configuring an existing registered wallet and trusted testnet sidecar. The miner serves hotkey-signed HTTP on port `8080` by default; no certificates are required.
