#!/usr/bin/env bash
# Fetch and verify the Telia Vector boot artefacts (C-01).
#
# The kernel, initramfs and iPXE binaries are release assets, not source; git
# tracks only assets/SHA256SUMS and assets/PROVENANCE.md. This script downloads
# each listed file and refuses to keep anything whose SHA-256 does not match.
#
# Usage:
#   scripts/fetch-assets.sh                 # download from VECTOR_ASSET_BASE_URL
#   scripts/fetch-assets.sh --from-dir DIR  # copy from a local mirror instead
#   scripts/fetch-assets.sh --verify        # only check what is already present
#
# VECTOR_ASSET_BASE_URL defaults to the GitHub release that publishes them.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS="$(cd "$SCRIPT_DIR/../assets" && pwd)"
SUMS="$ASSETS/SHA256SUMS"
BASE_URL="${VECTOR_ASSET_BASE_URL:-https://github.com/velocit-ee/core/releases/download/vector-assets-v1}"
FROM_DIR=""
VERIFY_ONLY=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --from-dir) FROM_DIR="$2"; shift 2 ;;
    --verify)   VERIFY_ONLY=1; shift ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

[[ -f "$SUMS" ]] || { echo "missing $SUMS" >&2; exit 1; }

if command -v sha256sum >/dev/null 2>&1; then
  sha() { sha256sum "$1" | cut -d' ' -f1; }
else
  sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
fi

status=0
while read -r expected rel; do
  [[ -z "$expected" || "$expected" == \#* ]] && continue
  dest="$ASSETS/$rel"
  if [[ ! -f "$dest" && $VERIFY_ONLY -eq 0 ]]; then
    mkdir -p "$(dirname "$dest")"
    tmp="$dest.part"
    if [[ -n "$FROM_DIR" ]]; then
      cp "$FROM_DIR/$rel" "$tmp"
    else
      curl -fsSL --retry 3 -o "$tmp" "$BASE_URL/$(basename "$rel")"
    fi
    mv "$tmp" "$dest"
  fi
  if [[ ! -f "$dest" ]]; then
    echo "MISSING  $rel" ; status=1; continue
  fi
  actual="$(sha "$dest")"
  if [[ "$actual" == "$expected" ]]; then
    echo "OK       $rel"
  else
    echo "MISMATCH $rel (expected $expected, got $actual) — removing" >&2
    rm -f "$dest"
    status=1
  fi
done < "$SUMS"

exit "$status"
