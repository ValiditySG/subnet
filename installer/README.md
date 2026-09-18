# Validity operator setup

Use a reviewed checkout and follow [the validator guide](../docs/validator.md).

- `bash installer/install.sh` creates a private `.env`, generates distinct sidecar tokens, and preserves existing configuration.
- Defaults are `network=test`, `netuid=568`, wallet path `~/.bittensor/wallets`, wallet name `default` and hotkey name `default`.
- Fill the actual subnet tempo, Hippius credentials and immutable validator image digest. Override `VALIDATOR_WALLET_PATH`, `VALIDATOR_WALLET_NAME` and `VALIDATOR_HOTKEY_NAME` as needed for the existing wallet. Compose mounts that host path read-only at `/wallets`. Miner communication uses signed HTTP and requires no certificates. Start only after hotkey registration and validator admission.
- `bash installer/update_compose.sh` validates configuration, runs read-only preflight and starts the stack.

Updates are explicit. No automatic download-and-execute cron job is installed. Publish and review a new image, set its digest in `.env`, then run the update script. Preserve the private state volume across upgrades.
