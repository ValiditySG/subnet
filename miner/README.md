# Validity miner

Runnable synthetic RN reference miner for Bittensor testnet.

- [Operator setup and deployment](../docs/miner.md)
- [Service entry point](src/validity_miner/main.py)
- [Synthetic record resolver](src/validity_miner/resolver.py)
- [Signed exchange protocol](../protocol/synthetic-rn-v1/README.md)

Use a private `.env` based on `.env.example`. From this directory, install with `uv sync --frozen` and run `uv run --frozen miner --env-file .env` after configuring an existing registered wallet, trusted testnet sidecar and TLS certificates.
