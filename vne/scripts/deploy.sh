#!/usr/bin/env bash
# Thin shell wrapper around vne/deploy.py — no logic here.
# All argument parsing, validation, and orchestration live in deploy.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VNE_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Run from the parent of vne/ so 'from vne import …' resolves cleanly.
cd "$(dirname "${VNE_ROOT}")"

exec python -m vne.deploy "$@"
