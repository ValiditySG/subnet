#!/usr/bin/env bash
set -euo pipefail

SESSION="localnet"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCALNET_DIR="$REPO_ROOT/localnet"

command -v tmux >/dev/null || { echo "tmux not found" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker not found" >&2; exit 1; }
command -v uv >/dev/null || { echo "uv not found" >&2; exit 1; }

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists. Run: tmux kill-session -t $SESSION" >&2
  exit 1
fi

if [ ! -f "$LOCALNET_DIR/.env" ]; then
  cp "$LOCALNET_DIR/.env.example" "$LOCALNET_DIR/.env"
  echo "Created localnet/.env from .env.example"
fi

echo "Starting docker compose..."
(cd "$LOCALNET_DIR" && docker compose up -d --wait)

echo "Syncing validator deps..."
(cd "$REPO_ROOT/validator" && uv sync --frozen)
echo "Syncing miner deps..."
(cd "$REPO_ROOT/miner" && uv sync --frozen)

echo "Running bootstrap..."
uv run --frozen --project "$REPO_ROOT/miner" python "$LOCALNET_DIR/bootstrap.py"

mkdir -p "$LOCALNET_DIR/logs"

tmux new-session -d -s "$SESSION" -n main \
  -c "$REPO_ROOT/miner" \
  "PYTHONUNBUFFERED=1 uv run --frozen python ../localnet/miners/miner-honest.py -n 1 2>&1 | tee ../localnet/logs/miner.log"

uv run --frozen --project "$REPO_ROOT/miner" python "$LOCALNET_DIR/check.py" wait-miner
for profile in slow stale unsafe timeout malformed duplicate late; do
  tmux new-window -t "$SESSION" -n "$profile" -c "$REPO_ROOT/miner" \
    "PYTHONUNBUFFERED=1 uv run --frozen python ../localnet/miners/miner-$profile.py 2>&1 | tee ../localnet/logs/miner-$profile.log"
  uv run --frozen --project "$REPO_ROOT/miner" python "$LOCALNET_DIR/check.py" wait-miner --profile "$profile"
done
(cd "$LOCALNET_DIR" && docker compose restart pylon)

tmux split-window -h -t "$SESSION:main" \
  -c "$REPO_ROOT/validator" \
  "uv run --frozen validator --env-file ../localnet/.env 2>&1 | tee ../localnet/logs/validator.log"

tmux select-pane -t "$SESSION:main.0"

if [ -t 1 ]; then
  tmux attach -t "$SESSION"
else
  echo "Session '$SESSION' started. Attach with: tmux attach -t $SESSION"
fi
