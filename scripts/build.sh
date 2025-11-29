#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BACKEND_DIR="${BACKEND_DIR:-$ROOT/backend}"
FRONTEND_DIR="${FRONTEND_DIR:-$ROOT/frontend}"
AGENT_DIR="${AGENT_DIR:-$ROOT/agent}"
AGENT_VENV="${AGENT_VENV:-$ROOT/.venv}"              # align with scripts/start_agent.sh
AGENT_REQUIREMENTS="${AGENT_REQUIREMENTS:-$ROOT/requirements.txt}"

require_cmd() {
  for cmd in "$@"; do
    if ! command -v "$cmd" >/dev/null 2>&1; then
      echo "error: missing required command: $cmd" >&2
      exit 1
    fi
  done
}

echo "==> Checking prerequisites"
require_cmd python3 cargo npm

echo "==> Backend (cargo fmt/clippy/build)"
pushd "$BACKEND_DIR" >/dev/null
cargo clippy --all-targets --all-features -- -D warnings
cargo build --release
popd >/dev/null

echo "==> Frontend (npm install + npm run build for typecheck)"
pushd "$FRONTEND_DIR" >/dev/null
npm install --prefer-offline --no-audit --no-fund
npm run build -- --emptyOutDir=false
popd >/dev/null

echo "==> Agent (venv deps + ruff lint + byte-compile)"
if [[ ! -d "$AGENT_DIR" ]]; then
  echo "error: agent dir not found: $AGENT_DIR" >&2
  exit 1
fi
python3 -m venv "$AGENT_VENV"
# shellcheck source=/dev/null
source "$AGENT_VENV/bin/activate"
python -m pip install --upgrade pip
if [[ ! -f "$AGENT_REQUIREMENTS" ]]; then
  echo "error: requirements file not found: $AGENT_REQUIREMENTS" >&2
  exit 1
fi
python -m pip install -r "$AGENT_REQUIREMENTS" "ruff>=0.5"
python -m ruff check --fix "$AGENT_DIR"
python -m compileall "$AGENT_DIR"

echo "All checks/builds completed successfully."
