#!/usr/bin/env bash
# Apply a reviewed, digest-pinned release. No remote script execution or unattended updates.
set -euo pipefail
VALIDITY_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "${VALIDITY_ROOT}/envs/deployed"
python3 - <<'PYENV'
import re
from pathlib import Path
path = Path('.env')
if path.stat().st_mode & 0o077:
    raise SystemExit('.env must have mode 0600')
entries = dict(line.split('=', 1) for line in path.read_text().splitlines() if line and not line.startswith('#') and '=' in line)
for name in ('VALIDATOR_IMAGE', 'PYLON_IMAGE'):
    if not re.fullmatch(r'[^\s]+@sha256:[0-9a-f]{64}', entries.get(name, '')):
        raise SystemExit(f'{name} must reference a registry image by sha256 digest')
PYENV
docker compose config --quiet
docker compose pull pylon validator preflight
docker compose up -d pylon
docker compose run --rm --no-deps preflight
docker compose up -d validator
