#!/usr/bin/env bash
# Prepare private operator configuration from a reviewed checkout; never starts chain writes.
set -euo pipefail
umask 077
VALIDITY_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
VALIDITY_DEPLOY_DIR="${VALIDITY_ROOT}/envs/deployed"
if [[ -e "${VALIDITY_DEPLOY_DIR}/.env" ]]; then
    echo 'Existing .env preserved.'
    exit 0
fi
cp "${VALIDITY_DEPLOY_DIR}/.env.example" "${VALIDITY_DEPLOY_DIR}/.env"
python3 - "${VALIDITY_DEPLOY_DIR}/.env" <<'PYENV'
import secrets
import sys
from pathlib import Path
path = Path(sys.argv[1])
data = path.read_text()
for name in ('VALIDATOR_PYLON_OPEN_ACCESS_TOKEN', 'VALIDATOR_PYLON_IDENTITY_TOKEN', 'PYLON_METRICS_TOKEN'):
    data = data.replace(name + '=\n', name + '=' + secrets.token_hex(32) + '\n')
path.write_text(data)
path.chmod(0o600)
PYENV
echo 'Private .env prepared. Complete it using docs/validator.md before running preflight.'
