"""Interactive setup wizard — builds vme-config.yml through guided prompts.

No YAML knowledge required. Detects network interfaces, suggests defaults,
and writes a ready-to-use config file.
"""

from __future__ import annotations

import getpass
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

def _line(char: str = "─", width: int = 54) -> str:
    return char * width

def _header(title: str) -> None:
    print()
    print(_line("═"))
    print(f"  {title}")
    print(_line("═"))
    print()

def _step(n: int, total: int, title: str) -> None:
    print()
    print(_line())
    print(f"  Step {n}/{total} — {title}")
    print(_line())
    print()

def _ask(prompt: str, default: str = "") -> str:
    """Prompt the user for input, returning default on empty enter."""
    hint = f" [{default}]" if default else ""
    try:
        raw = input(f"  {prompt}{hint}: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSetup cancelled.")
        raise SystemExit(0)
    return raw if raw else default

def _ask_choice(prompt: str, options: list[str], default: int = 1) -> int:
    """Present a numbered list and return the 1-based index chosen."""
    for i, opt in enumerate(options, 1):
        print(f"    [{i}]  {opt}")
    print()
    while True:
        raw = _ask(prompt, str(default))
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(options)}.")

def _ask_yes(prompt: str, default: bool = True) -> bool:
    """Yes/no prompt. Returns bool."""
    hint = "Y/n" if default else "y/N"
    raw = _ask(prompt, hint).lower()
    if raw in ("y/n", "y", "yes", ""):
        return default if raw == "" else True
    return False


# ---------------------------------------------------------------------------
# Network interface detection
# ---------------------------------------------------------------------------

@dataclass
class NetworkInterface:
    """Detected network interface."""
    name: str
    ip: Optional[str]
    connected: bool

    def label(self) -> str:
        ip_str = self.ip if self.ip else "no IP"
        conn = "connected" if self.connected else "not connected"
        return f"{self.name:<12} {ip_str:<18} ({conn})"


def _detect_interfaces() -> list[NetworkInterface]:
    """Parse `ip addr` output to find usable network interfaces.

    Filters out loopback, Docker bridges, and virtual interfaces.
    """
    try:
        result = subprocess.run(
            ["ip", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return []

    ifaces: list[NetworkInterface] = []
    current_name: Optional[str] = None
    current_ip: Optional[str] = None
    current_connected = False

    skip_prefixes = ("lo", "docker", "br-", "veth", "virbr", "tun", "tap", "dummy")

    for line in output.splitlines():
        # New interface block: "2: eth0: <FLAGS> ..."
        m = re.match(r"^\d+: (\S+):", line)
        if m:
            if current_name and not any(current_name.startswith(p) for p in skip_prefixes):
                ifaces.append(NetworkInterface(current_name, current_ip, current_connected))
            current_name = m.group(1).rstrip("@").split("@")[0]
            current_ip = None
            current_connected = "LOWER_UP" in line

        # IPv4 address line: "    inet 192.168.1.5/24 ..."
        m = re.match(r"^\s+inet (\d+\.\d+\.\d+\.\d+)/", line)
        if m and current_name:
            current_ip = m.group(1)

    if current_name and not any(current_name.startswith(p) for p in skip_prefixes):
        ifaces.append(NetworkInterface(current_name, current_ip, current_connected))

    return ifaces


def _get_interface_ip(name: str) -> Optional[str]:
    """Return the current IPv4 address on *name*, or None."""
    for iface in _detect_interfaces():
        if iface.name == name:
            return iface.ip
    return None


def _configure_firewall(interface: str) -> None:
    """Check UFW state and open ports VME requires on *interface*.

    Ports needed:
      67/udp  — DHCP (target machines request an IP)
      69/udp  — TFTP (target machines download iPXE)
      80/tcp  — HTTP (iPXE fetches boot script and OS image from nginx)

    Silently skips if UFW is not installed or not active.
    """
    # Check if ufw is available.
    try:
        status = subprocess.run(
            ["sudo", "ufw", "status"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return  # ufw not installed — nothing to do

    if "Status: active" not in status.stdout:
        return  # ufw installed but inactive — no rules needed

    out = status.stdout

    # Determine which ports are missing.
    iface_blanket = f"on {interface}" in out
    missing: list[tuple[str, str, str]] = []

    if not iface_blanket:
        if ":67" not in out and "67/udp" not in out:
            missing.append(("67", "udp", "DHCP"))
        if ":69" not in out and "69/udp" not in out:
            missing.append(("69", "udp", "TFTP"))
        if ":80" not in out and "80/tcp" not in out and "Nginx" not in out:
            missing.append(("80", "tcp", "HTTP"))

    if not missing:
        print(f"\n  Firewall: required ports already open on {interface}.")
        return

    print(f"\n  UFW is active. The following ports need to be opened on {interface}:")
    for port, proto, service in missing:
        print(f"    {port}/{proto}  ({service})")

    open_now = _ask_yes("Open these ports now?", default=True)
    if not open_now:
        print("\n  Skipped. Add the rules manually before running 'vme deploy':")
        for port, proto, _ in missing:
            print(f"    sudo ufw allow in on {interface} to any port {port} proto {proto}")
        return

    all_ok = True
    for port, proto, service in missing:
        result = subprocess.run(
            ["sudo", "ufw", "allow", "in", "on", interface, "to", "any",
             "port", port, "proto", proto],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            print(f"  Opened {port}/{proto} ({service}).")
        else:
            print(f"  [!] Could not open {port}/{proto}: {result.stderr.strip()}")
            all_ok = False

    if all_ok:
        subprocess.run(["sudo", "ufw", "reload"], capture_output=True)
        print(f"  Firewall updated.")
    else:
        print(f"  Some rules may need to be added manually.")


def _hash_password(plaintext: str) -> str:
    """Return a SHA-512 crypt hash of *plaintext* using openssl."""
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=plaintext,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise RuntimeError(f"openssl passwd failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _find_ssh_key() -> Optional[str]:
    """Return the first SSH public key found in ~/.ssh/, or None."""
    candidates = [
        Path.home() / ".ssh" / "id_ed25519.pub",
        Path.home() / ".ssh" / "id_rsa.pub",
        Path.home() / ".ssh" / "id_ecdsa.pub",
    ]
    for path in candidates:
        if path.exists():
            content = path.read_text().strip()
            if content:
                return content
    return None


# ---------------------------------------------------------------------------
# Wizard
# ---------------------------------------------------------------------------

def run(config_path: Path) -> None:
    """Run the interactive setup wizard and write *config_path*."""

    _header("VME Setup")

    print("  This wizard builds your config file through a few simple questions.")
    print("  Press Enter to accept the suggested value shown in [brackets].")

    # -------------------------------------------------------------------------
    # Step 1: provisioning interface
    # -------------------------------------------------------------------------
    _step(1, 5, "Provisioning network")

    print("  Detected network interfaces:\n")
    ifaces = _detect_interfaces()

    if not ifaces:
        print("  [!] Could not detect network interfaces automatically.")
        print("      Run `ip link` to find your interface name.\n")
        interface = _ask("Interface name (e.g. eth1)")
    else:
        choice = _ask_choice(
            "Which interface is connected to your provisioning switch?",
            [i.label() for i in ifaces],
            default=next(
                (n + 1 for n, i in enumerate(ifaces) if not i.ip and i.connected),
                1,
            ),
        )
        interface = ifaces[choice - 1].name

    existing_ip = _get_interface_ip(interface)
    if existing_ip:
        print(f"\n  {interface} already has IP {existing_ip}.")
        seed_ip = existing_ip
    else:
        print(f"\n  {interface} has no IP address.")
        print("  VME needs an IP on this interface so target machines can reach the seed stack.")
        seed_ip = _ask("Seed machine IP on this interface", "192.168.100.1")

    dhcp_start = _ask("First IP to hand out to target machines", "192.168.100.100")
    dhcp_end   = _ask("Last IP in that range",                  "192.168.100.200")

    _configure_firewall(interface)

    # -------------------------------------------------------------------------
    # Step 2: target machine
    # -------------------------------------------------------------------------
    _step(2, 5, "Target machine")

    os_choice = _ask_choice(
        "Which OS do you want to install?",
        ["Proxmox VE", "Ubuntu Server"],
        default=1,
    )
    os_name = "proxmox-ve" if os_choice == 1 else "ubuntu-server"

    hostname  = _ask("Hostname for this machine", "node-01")
    target_ip = _ask("Fixed IP address for the installed OS", "192.168.100.10")
    gateway   = _ask("Gateway for the installed OS", seed_ip)
    dns       = _ask("DNS server", "8.8.8.8")
    disk      = _ask("Install disk on the target machine", "/dev/sda")
    timezone  = _ask("Timezone for the installed OS", "Europe/Berlin")

    # -------------------------------------------------------------------------
    # Step 3: OS user account
    # -------------------------------------------------------------------------
    _step(3, 5, "User account")

    print("  The installed OS creates an 'ubuntu' user (Ubuntu) or 'root' (Proxmox).")
    print("  Set a password for this account. SSH key login is preferred but a")
    print("  password is required by the installer.\n")

    password_hash = ""
    while True:
        try:
            pw1 = getpass.getpass("  Password: ")
            if not pw1:
                print("  [!] Password cannot be empty.")
                continue
            pw2 = getpass.getpass("  Confirm:  ")
            if pw1 != pw2:
                print("  Passwords do not match. Try again.\n")
                continue
        except (KeyboardInterrupt, EOFError):
            print("\n\nSetup cancelled.")
            raise SystemExit(0)
        try:
            password_hash = _hash_password(pw1)
            print("  Password hashed.")
            break
        except RuntimeError as exc:
            print(f"  [!] Could not hash password: {exc}")
            print("      Is openssl installed? (sudo apt-get install openssl)\n")
            break

    # -------------------------------------------------------------------------
    # Step 4: SSH key
    # -------------------------------------------------------------------------
    _step(4, 5, "SSH access")

    found_key = _find_ssh_key()
    if found_key:
        short = found_key[:60] + "..." if len(found_key) > 60 else found_key
        print(f"  Found: {short}\n")
        use_found = _ask_yes("Use this key?", default=True)
        ssh_key = found_key if use_found else _ask("Paste your SSH public key")
    else:
        print("  No SSH key found in ~/.ssh/")
        print("  Generate one with: ssh-keygen -t ed25519\n")
        ssh_key = _ask("Paste your SSH public key")

    if not ssh_key:
        print("\n  [!] No SSH key provided. You will not be able to log in after provisioning.")
        print("      You can re-run `vme setup` to add one later.\n")

    # -------------------------------------------------------------------------
    # Step 5: write config
    # -------------------------------------------------------------------------
    _step(5, 5, "Saving config")

    # Derive /prefix length from a /24 default; keep it simple for now.
    prefix = "24"

    config = {
        "provisioning_interface": interface,
        "seed_ip": seed_ip,
        "dhcp_range_start": dhcp_start,
        "dhcp_range_end": dhcp_end,
        "dhcp_lease_time": "12h",
        "target": {
            "hostname": hostname,
            "ip": target_ip,
            "prefix": prefix,
            "gateway": gateway,
            "netmask": "255.255.255.0",
            "dns": dns,
            "os": os_name,
            "disk": disk,
            "timezone": timezone,
            "password_hash": password_hash,
            "ssh_public_key": ssh_key,
        },
    }

    import yaml
    with open(config_path, "w") as fh:
        yaml.dump(config, fh, default_flow_style=False, allow_unicode=True)

    print(f"  Config written to {config_path}")

    print()
    print(_line("═"))
    print("  Setup complete.")
    print()
    print("  Next steps:")
    print("    1. Power off the target machine.")
    print("    2. Set it to network-boot (PXE) in its BIOS/UEFI settings.")
    print("    3. Run:  vme deploy")
    print(_line("═"))
    print()
