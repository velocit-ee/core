#!/usr/bin/env bash
# ==============================================================================
# Telia Vector Demo (VIBE_SPRINT 2026)
# Powered by Velocitee Vector
# Zero-touch bare-metal edge provisioning on Telia CPE hardware class (Telia X2)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELIA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DAEMON_DIR="$TELIA_DIR/daemon"
TARGET_DIR="$TELIA_DIR/target"

_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
_purple() { printf '\033[1;35m%s\033[0m\n' "$*"; }
_green()  { printf '\033[1;32m%s\033[0m\n' "$*"; }
_cyan()   { printf '\033[1;36m%s\033[0m\n' "$*"; }
_line()   { printf '%.0s─' {1..64}; printf '\n'; }

echo
_purple "  ╔══════════════════════════════════════════════════════════════════╗"
_purple "  ║   TELIA VECTOR -- ZERO-TOUCH SOVEREIGN CLUSTER DEMO            ║"
_purple "  ║   Powered by Velocitee Vector · VIBE_SPRINT 2026                 ║"
_purple "  ╚══════════════════════════════════════════════════════════════════╝"
echo

# 1. Check prerequisites
# The demo has no network dependencies (G-04): both services are local Python
# processes and the provisioning pipeline is simulated in-process. QEMU is
# only reported, never installed or launched here, so venue Wi-Fi that blocks
# Homebrew or package mirrors cannot stall the pitch.
_bold "1. Validating Hardware & Edge Environment ..."
if ! command -v python3 &>/dev/null; then
    echo "  [!] python3 is required and was not found on PATH." >&2
    exit 1
fi
if command -v qemu-system-aarch64 &>/dev/null; then
    _green "  [✓] QEMU present (optional; not used by this demo)."
else
    echo "  [i] QEMU not installed — not needed for the demo. Skipping."
fi
_green "  [✓] Router Spec Capped: Technicolor DGA4330 / Telia X2 (ARMv7/v8, 512MB RAM)."
_green "  [✓] Offline-safe: no package mirrors or external services are contacted."
if [[ ! -f "$TELIA_DIR/assets/boot/initrd" ]]; then
    echo "  [i] Boot artefacts not present (not needed for the demo). For a real PXE boot run:"
    echo "      $TELIA_DIR/scripts/fetch-assets.sh"
fi

# 2. Start Telia Vector Edge Daemon
_bold "\n2. Starting Telia Vector Daemon (Port 8088) ..."
python3 "$DAEMON_DIR/agent.py" &
AGENT_PID=$!
sleep 1

# 3. Start Target Sovereign Hub Server
_bold "3. Initializing Sovereign Target Node Environment (Port 8081) ..."
python3 "$TARGET_DIR/sovereign_hub.py" &
HUB_PID=$!
sleep 1

cleanup() {
    echo
    _cyan "\nShutting down demo environment ..."
    # Only stop the processes this script started. The previous pkill of
    # every qemu-system-aarch64 on the host would kill unrelated VMs.
    kill "$AGENT_PID" "$HUB_PID" 2>/dev/null || true
    _green "Telia Vector services stopped cleanly."
}
trap cleanup EXIT INT TERM

_line
_green "  TELIA VECTOR EDGE GATEWAY RUNNING:"
echo   "    • Telia Vector Portal:        http://localhost:8088"
echo   "    • Sovereign Node Hub:         http://localhost:8081"
echo   "    • REST API (CORS Enabled):    http://localhost:8088/api/v1/telemetry"
echo   "    • iPXE Dynamic Bootstrap:     http://localhost:8088/boot.ipxe"
echo   "    • Velocitee Manifest:         http://localhost:8088/api/v1/manifest"
_line

echo
_bold "Opening Telia Vector Portal in browser ..."
open "http://localhost:8088" || true

echo
_cyan "  DEMO INSTRUCTIONS FOR JUDGES:"
echo  "  1. Review the CPE telemetry (Technicolor DGA4330, < 40 MB RAM footprint)."
echo  "  2. Note the 'Unclaimed Server' quarantined in VLAN 999 on Port 4."
echo  "  3. Click 'Authenticate & Zero-Touch Deploy' to issue the HMAC token and stream the OS."
echo  "  4. Click 'Open Sovereign Legal Workspace' to reveal the live, operational node on Port 8081."
echo
_purple "Press Ctrl+C at any time to stop the demo."

wait "$AGENT_PID"
