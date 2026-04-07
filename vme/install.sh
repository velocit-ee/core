#!/usr/bin/env bash
# VME installer — run this once on the seed machine to get everything set up.
# Supports Ubuntu 22.04+ and Debian 12+.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/velocit-ee/core/main/vme/install.sh | bash
# or if you already have the repo:
#   bash install.sh

set -euo pipefail

REPO_URL="https://github.com/velocit-ee/core.git"
INSTALL_DIR="$HOME/vme"
BIN_DIR="/usr/local/bin"

_green()  { printf '\033[0;32m%s\033[0m\n' "$*"; }
_yellow() { printf '\033[0;33m%s\033[0m\n' "$*"; }
_red()    { printf '\033[0;31m%s\033[0m\n' "$*"; }
_bold()   { printf '\033[1m%s\033[0m\n' "$*"; }
_line()   { printf '%.0s─' {1..54}; printf '\n'; }

_need() {
    if ! command -v "$1" &>/dev/null; then
        return 1
    fi
}

_section() {
    echo
    _line
    _bold "  $*"
    _line
    echo
}

# ─── Banner ────────────────────────────────────────────────────────────────
echo
_bold "  VME — Velocitee Metal Provisioning Engine"
_bold "  Installer"
echo
echo "  This script will:"
echo "    • Install Docker (if not already installed)"
echo "    • Install Python 3.11+ (if not already installed)"
echo "    • Download VME"
echo "    • Create the 'vme' command"
echo
read -rp "  Press Enter to continue, or Ctrl+C to cancel. " _ignored

# ─── OS detection ──────────────────────────────────────────────────────────
if [[ ! -f /etc/os-release ]]; then
    _red "  Cannot detect OS. This installer supports Ubuntu 22.04+ and Debian 12+."
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release

if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
    _yellow "  Warning: detected '$ID'. This installer is tested on Ubuntu and Debian."
    _yellow "  Proceeding anyway — install steps may need manual adjustment."
fi

# ─── Docker ────────────────────────────────────────────────────────────────
_section "Docker"

if ! _need docker; then
    echo "  Installing Docker ..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq ca-certificates curl gnupg lsb-release

    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/${ID}/gpg \
        | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
        "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/${ID} \
$(lsb_release -cs) stable" \
        | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update -qq
    sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io docker-compose-plugin

    # Enable and start the daemon now.
    sudo systemctl enable --now docker

    # On Ubuntu with systemd-resolved, 127.0.0.53 is not reachable from inside
    # Docker containers, causing apt and git to fail during image builds.
    if [[ ! -f /etc/docker/daemon.json ]]; then
        echo '{"dns": ["8.8.8.8", "8.8.4.4"]}' | sudo tee /etc/docker/daemon.json > /dev/null
        sudo systemctl restart docker
        _green "  Docker DNS configured (8.8.8.8)."
    fi

    # Allow the current user to run docker without sudo.
    sudo usermod -aG docker "$USER"
    _green "  Docker installed."
else
    _green "  Docker already installed: $(docker --version)"

    # Check the daemon is running.
    if ! docker info &>/dev/null; then
        echo "  Starting Docker ..."
        sudo systemctl start docker
        sudo systemctl enable docker
    fi
fi

# ─── Python ────────────────────────────────────────────────────────────────
_section "Python"

PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
    if _need "$candidate"; then
        ver=$($candidate --version 2>&1 | grep -oP '\d+\.\d+' | head -1)
        major=${ver%%.*}
        minor=${ver##*.}
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
            PYTHON_BIN="$candidate"
            break
        fi
    fi
done

if [[ -z "$PYTHON_BIN" ]]; then
    echo "  Installing Python 3.12 ..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.12 python3.12-venv python3-pip
    PYTHON_BIN="python3.12"
    _green "  Python installed."
else
    _green "  Python already installed: $($PYTHON_BIN --version)"
fi

# Also need git.
if ! _need git; then
    echo "  Installing git ..."
    sudo apt-get install -y -qq git
fi

# ─── Download VME ──────────────────────────────────────────────────────────
_section "VME"

if [[ -d "$INSTALL_DIR/.git" ]]; then
    echo "  Updating existing VME installation at $INSTALL_DIR ..."
    git -C "$INSTALL_DIR" pull --ff-only
else
    echo "  Downloading VME to $INSTALL_DIR ..."
    # Clone just the core repo (sparse checkout — only the vme/ directory).
    git clone --filter=blob:none --sparse "$REPO_URL" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" sparse-checkout set vme
fi

VME_DIR="$INSTALL_DIR/vme"

# ─── Python venv + dependencies ────────────────────────────────────────────
echo "  Setting up Python environment ..."
"$PYTHON_BIN" -m venv "$VME_DIR/.venv"
"$VME_DIR/.venv/bin/pip" install -q --upgrade pip
"$VME_DIR/.venv/bin/pip" install -q -r "$VME_DIR/requirements.txt"

# ─── Create the `vme` command ──────────────────────────────────────────────
echo "  Installing 'vme' command to $BIN_DIR ..."
sudo tee "$BIN_DIR/vme" > /dev/null <<EOF
#!/usr/bin/env bash
# VME launcher — created by install.sh
# If docker isn't accessible in the current session (group not yet active),
# re-exec with sg docker so the user never has to run newgrp manually.
cd "$VME_DIR"
if ! docker info &>/dev/null 2>&1 && groups | grep -qv docker && getent group docker &>/dev/null; then
    exec sg docker -c "$VME_DIR/.venv/bin/python -m cli.vme \$*"
fi
exec "$VME_DIR/.venv/bin/python" -m cli.vme "\$@"
EOF
sudo chmod +x "$BIN_DIR/vme"

# ─── Done ──────────────────────────────────────────────────────────────────
echo
_line
_green "  VME installed successfully."
echo
echo "  To get started:"
echo
echo "    cd $VME_DIR"
echo "    vme setup"
echo
_line
echo
