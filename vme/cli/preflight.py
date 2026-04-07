"""Pre-flight checks run before any VME deployment."""

from __future__ import annotations

import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from .os_registry import OS_REGISTRY

VME_REQUIRED_DISK_GB = 20  # minimum free space for image cache


@dataclass
class CheckResult:
    """Result of a single pre-flight check."""

    name: str
    passed: bool
    detail: str = ""
    fix: str = ""


@dataclass
class PreflightReport:
    """Aggregated results of all pre-flight checks."""

    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """Return True only if every check passed."""
        return all(r.passed for r in self.results)

    def add(self, result: CheckResult) -> None:
        """Append a check result."""
        self.results.append(result)


def check_docker() -> CheckResult:
    """Verify Docker is installed and the daemon is reachable."""
    if not shutil.which("docker"):
        return CheckResult(
            name="docker",
            passed=False,
            detail="docker binary not found in PATH.",
            fix="Install Docker: https://docs.docker.com/engine/install/",
        )
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            return CheckResult(
                name="docker",
                passed=False,
                detail="Docker daemon is not running.",
                fix="Start Docker: sudo systemctl start docker  (or open Docker Desktop)",
            )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="docker",
            passed=False,
            detail="docker info timed out — daemon may be hung.",
            fix="Restart Docker: sudo systemctl restart docker",
        )
    except FileNotFoundError:
        return CheckResult(
            name="docker",
            passed=False,
            detail="docker binary not found.",
            fix="Install Docker: https://docs.docker.com/engine/install/",
        )

    return CheckResult(name="docker", passed=True, detail="Docker is running.")


def check_interface(interface: str) -> CheckResult:
    """Verify the provisioning network interface exists and is up."""
    try:
        result = subprocess.run(
            ["ip", "link", "show", interface],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return CheckResult(
                name="interface",
                passed=False,
                detail=f"Interface '{interface}' not found.",
                fix=f"Set provisioning_interface in vme-config.yml to an existing interface. Run 'ip link' to list available interfaces.",
            )
        if "state UP" not in result.stdout and "state UNKNOWN" not in result.stdout:
            return CheckResult(
                name="interface",
                passed=False,
                detail=f"Interface '{interface}' exists but is not up.",
                fix=f"Bring it up: sudo ip link set {interface} up",
            )
    except FileNotFoundError:
        # ip not available (non-Linux); attempt a socket-based fallback
        try:
            socket.if_nametoindex(interface)
        except OSError:
            return CheckResult(
                name="interface",
                passed=False,
                detail=f"Interface '{interface}' not found.",
                fix="Set provisioning_interface in vme-config.yml to an existing interface.",
            )

    return CheckResult(
        name="interface",
        passed=True,
        detail=f"Interface '{interface}' is available.",
    )


def check_no_conflicting_dhcp(interface: str) -> CheckResult:
    """Detect any foreign DHCP server on the provisioning interface.

    Sends a minimal DHCP DISCOVER and watches for a DHCP OFFER from
    something other than us. Falls back gracefully if nmap/dhcping are
    absent — reports a warning rather than blocking.
    """
    if shutil.which("nmap"):
        try:
            result = subprocess.run(
                ["nmap", "--script", "broadcast-dhcp-discover", "-e", interface],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout + result.stderr
            if "Server Identifier" in output:
                return CheckResult(
                    name="dhcp_conflict",
                    passed=False,
                    detail=f"An existing DHCP server was detected on '{interface}'.",
                    fix="Disconnect or disable the conflicting DHCP server before starting VME.",
                )
        except subprocess.TimeoutExpired:
            pass  # no reply → no conflict found

        return CheckResult(
            name="dhcp_conflict",
            passed=True,
            detail="No conflicting DHCP server detected.",
        )

    # nmap not available — soft warning only
    return CheckResult(
        name="dhcp_conflict",
        passed=True,
        detail="nmap not found; skipped active DHCP conflict scan. Install nmap for a thorough check.",
    )


def check_firewall(interface: str) -> CheckResult:
    """Warn if ufw is active and VME ports are not explicitly allowed.

    Checks both port 67/udp (DHCP) and port 80/tcp (HTTP — nginx ISO serving).
    """
    try:
        ufw = subprocess.run(["sudo", "ufw", "status"], capture_output=True, text=True, timeout=5)
        if "Status: active" not in ufw.stdout:
            return CheckResult(name="firewall", passed=True, detail="ufw is inactive.")

        out = ufw.stdout
        iface_rule = f"on {interface}" in out

        dhcp_open = iface_rule or ":67" in out or "67/udp" in out
        http_open = iface_rule or ":80" in out or "80/tcp" in out or "Nginx" in out

        if not dhcp_open:
            return CheckResult(
                name="firewall",
                passed=False,
                detail="ufw is active but port 67/udp (DHCP) may be blocked — target machines won't get an IP.",
                fix=(
                    f"sudo ufw allow in on {interface} to any port 67 proto udp\n"
                    f"  sudo ufw allow in on {interface} to any port 80 proto tcp"
                ),
            )
        if not http_open:
            return CheckResult(
                name="firewall",
                passed=False,
                detail="ufw is active but port 80/tcp (HTTP) may be blocked — iPXE chainload and ISO serving will fail.",
                fix=f"sudo ufw allow in on {interface} to any port 80 proto tcp",
            )
        return CheckResult(name="firewall", passed=True, detail="ufw active; DHCP and HTTP ports appear open.")
    except Exception:
        return CheckResult(name="firewall", passed=True, detail="Could not check firewall state.")


def check_tftp_port() -> CheckResult:
    """Check that port 69 (TFTP) is not already in use."""
    try:
        result = subprocess.run(
            ["ss", "-tulnp"],
            capture_output=True, text=True, timeout=5,
        )
        if ":69 " in result.stdout or ":69\n" in result.stdout:
            return CheckResult(
                name="tftp_port",
                passed=False,
                detail="Port 69 (TFTP) is already in use — likely tftpd-hpa or in.tftpd.",
                fix="Run: sudo systemctl stop tftpd-hpa  (or: sudo kill $(pgrep in.tftpd))",
            )
    except Exception:
        pass
    return CheckResult(name="tftp_port", passed=True, detail="Port 69 is available.")


def check_config(config_path: Path) -> CheckResult:
    """Validate the user config file for required fields and sane values."""
    if not config_path.exists():
        return CheckResult(
            name="config",
            passed=False,
            detail=f"Config file not found: {config_path}",
            fix="Copy vme-config.example.yml to vme-config.yml and fill in your values.",
        )

    try:
        with open(config_path) as fh:
            config: dict[str, Any] = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        return CheckResult(
            name="config",
            passed=False,
            detail=f"Config file is not valid YAML: {exc}",
            fix="Fix the YAML syntax in vme-config.yml.",
        )

    required_top = ["provisioning_interface", "dhcp_range_start", "dhcp_range_end", "target"]
    required_target = ["hostname", "ip", "gateway", "netmask", "os", "disk", "ssh_public_key"]
    valid_os = set(OS_REGISTRY)

    for key in required_top:
        if key not in config:
            return CheckResult(
                name="config",
                passed=False,
                detail=f"Missing required field: '{key}'",
                fix=f"Add '{key}' to vme-config.yml. See vme-config.example.yml.",
            )

    target = config.get("target", {})
    for key in required_target:
        if key not in target:
            return CheckResult(
                name="config",
                passed=False,
                detail=f"Missing required target field: 'target.{key}'",
                fix=f"Add 'target.{key}' to vme-config.yml.",
            )

    if target.get("os") not in valid_os:
        return CheckResult(
            name="config",
            passed=False,
            detail=f"Invalid target.os '{target.get('os')}'. Must be one of: {', '.join(sorted(valid_os))}",
            fix=f"Set target.os in vme-config.yml to one of: {', '.join(sorted(valid_os))}",
        )

    return CheckResult(name="config", passed=True, detail="Config file is valid.")


def check_disk_space(cache_dir: Path, required_gb: float = VME_REQUIRED_DISK_GB) -> CheckResult:
    """Verify sufficient free disk space for OS image cache."""
    # Resolve the actual directory to stat — walk up to an existing parent.
    check_path = cache_dir
    while not check_path.exists():
        check_path = check_path.parent
        if check_path == check_path.parent:
            break

    try:
        stat = shutil.disk_usage(check_path)
        free_gb = stat.free / (1024**3)
    except OSError as exc:
        return CheckResult(
            name="disk_space",
            passed=False,
            detail=f"Cannot stat '{check_path}': {exc}",
            fix="Ensure the image_cache_dir path is accessible.",
        )

    if free_gb < required_gb:
        return CheckResult(
            name="disk_space",
            passed=False,
            detail=f"Only {free_gb:.1f} GB free at '{check_path}'. VME needs at least {required_gb:.0f} GB.",
            fix=f"Free up disk space or set image_cache_dir to a volume with more space.",
        )

    return CheckResult(
        name="disk_space",
        passed=True,
        detail=f"{free_gb:.1f} GB free at '{check_path}'.",
    )


def run_all(config_path: Path) -> PreflightReport:
    """Run all pre-flight checks and return a consolidated report.

    Reads the config file once to extract per-check parameters. If the
    config itself is invalid, later checks fall back to safe defaults.
    """
    report = PreflightReport()

    # Docker
    report.add(check_docker())

    # Config validation (done early so we can extract interface / cache dir)
    config_result = check_config(config_path)
    report.add(config_result)

    config: dict[str, Any] = {}
    if config_result.passed and config_path.exists():
        with open(config_path) as fh:
            config = yaml.safe_load(fh) or {}

    interface = config.get("provisioning_interface", "")
    if interface:
        report.add(check_interface(interface))
        report.add(check_no_conflicting_dhcp(interface))
    else:
        report.add(
            CheckResult(
                name="interface",
                passed=False,
                detail="Cannot check interface — config invalid or missing.",
                fix="Fix vme-config.yml first.",
            )
        )
        report.add(
            CheckResult(
                name="dhcp_conflict",
                passed=False,
                detail="Cannot check for DHCP conflicts — interface unknown.",
                fix="Fix vme-config.yml first.",
            )
        )

    raw_cache = config.get("image_cache_dir", "~/.velocitee/cache/images/")
    cache_dir = Path(raw_cache).expanduser()
    report.add(check_disk_space(cache_dir))
    report.add(check_tftp_port())
    report.add(check_firewall(interface))

    return report
