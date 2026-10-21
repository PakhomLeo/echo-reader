#!/usr/bin/env bash
# Cross-platform counterpart of scripts/verify.ps1 (Linux/macOS).
# Requires: uv, cargo (rustfmt + clippy), bun, Node.js >= 22 for Vitest.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

uv sync --locked
uv run pytest -q tests components/medialab/tests

(
  cd components/echo/backend
  cargo fmt --check
  cargo clippy --all-targets --locked -- -D warnings
  cargo test --locked
)

(
  cd components/echo/frontend
  bun install --frozen-lockfile
  bun run typecheck
  bun run lint
  bun run test
  bun run build
  if [[ "${1:-}" == "--e2e" ]]; then
    bun run test:e2e
  fi
)
echo "verify: all checks passed"
