#!/usr/bin/env bash
# ==============================================================================
# Telia Vector: Dual-Console Interactive Launcher
# Opens separate, live console windows for:
#   1. Router Gateway VM (Port 8088, Technicolor DGA4330 CPE)
#   2. Target Sovereign Server VM (Port 8081, 1U Edge MicroServer)
# ==============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TELIA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

_purple() { printf '\033[1;35m%s\033[0m\n' "$*"; }
_green()  { printf '\033[1;32m%s\033[0m\n' "$*"; }
_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }

echo
_purple "  ╔══════════════════════════════════════════════════════════════════╗"
_purple "  ║   TELIA VECTOR -- DUAL-CONSOLE APPLIANCE ENVIRONMENT            ║"
_purple "  ║   Opening Native Terminal Windows for Router & Server VMs        ║"
_purple "  ╚══════════════════════════════════════════════════════════════════╝"
echo

# 1. Launch Router Console in Terminal.app
_bold "1. Spawning Telia Router Gateway Console (Port 8088) ..."
osascript <<EOF
tell application "Terminal"
    activate
    do script "cd '$TELIA_DIR' && printf '\033]0;TELIA ROUTER CPE GATEWAY (Port 8088)\007' && clear && echo '\033[1;35m[TELIA ROUTER CPE GATEWAY -- TECHNICOLOR DGA4330 / TELIA X2]\033[0m' && python3 daemon/agent.py"
end tell
EOF

sleep 1.5

# 2. Launch Target Server Node Console in Terminal.app
_bold "2. Spawning Target Sovereign Server Node Console (Port 8081) ..."
osascript <<EOF
tell application "Terminal"
    do script "cd '$TELIA_DIR' && printf '\033]0;TARGET SOVEREIGN SERVER VM (Port 8081)\007' && clear && echo '\033[1;32m[TARGET SOVEREIGN SERVER NODE -- LIVE SERIAL CONSOLE]\033[0m' && python3 target/sovereign_hub.py"
end tell
EOF

sleep 1.5

# 3. Open Web Portal
_bold "3. Opening Telia Vector Portal in browser ..."
open "http://localhost:8088"

echo
_green "✓ Both VM consoles are active in separate Terminal windows!"
echo   "  • Arrange the two terminal windows side-by-side with your browser."
echo   "  • In the browser at http://localhost:8088, click 'Authenticate & Zero-Touch Deploy'."
echo   "  • Watch the Router window generate HMAC tokens and stream RFC 5424 syslogs live."
echo   "  • Watch the Server window log provisioning access and serve the Sovereign Hub."
echo
