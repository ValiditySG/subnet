# Validity operator setup

Use a reviewed checkout and follow [the validator guide](../docs/validator.md).

- `bash installer/install.sh` creates a private `.env`, generates distinct sidecar tokens, and preserves existing configuration.
- Fill the registered testnet netuid, tempo, operator wallet/TLS paths, Hippius credentials and immutable validator image digest.
- `bash installer/update_compose.sh` validates configuration, runs read-only preflight and starts the stack.

Updates are explicit. No automatic download-and-execute cron job is installed. Publish and review a new image, set its digest in `.env`, then run the update script. Preserve the private state volume across upgrades.
