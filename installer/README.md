# Validity operator setup

Use a reviewed checkout and follow [the validator guide](../docs/validator.md).

- `bash installer/install.sh` creates a private `.env`, generates distinct sidecar tokens, and preserves existing configuration.
- Defaults are `network=test` and `netuid=568`.
- Fill the actual subnet tempo, operator wallet paths, Hippius credentials and immutable validator image digest. Miner communication uses signed HTTP and requires no certificates. Start only after hotkey registration and validator admission.
- `bash installer/update_compose.sh` validates configuration, runs read-only preflight and starts the stack.

Updates are explicit. No automatic download-and-execute cron job is installed. Publish and review a new image, set its digest in `.env`, then run the update script. Preserve the private state volume across upgrades.
