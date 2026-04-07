#!/usr/bin/env python3
"""VME — Velocitee Metal Provisioning Engine CLI."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from string import Template
from typing import Optional

import typer
import yaml

from . import preflight as pf
from . import manifest as mf
from . import images as img
from . import setup as wizard

app = typer.Typer(
    name="vme",
    help="Velocitee Metal Provisioning Engine — PXE-boot and unattended OS install.",
    add_completion=False,
)
images_app = typer.Typer(help="Manage cached OS images.")
app.add_typer(images_app, name="images")

_CONFIG_DEFAULT = Path("vme-config.yml")
_REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config(config_path: Path) -> dict:
    """Load and return the YAML config. Exits with a message on failure."""
    if not config_path.exists():
        typer.echo(
            f"[error] Config file not found: {config_path}\n"
            "Run 'vme setup' to create one.",
            err=True,
        )
        raise typer.Exit(1)
    try:
        with open(config_path) as fh:
            return yaml.safe_load(fh) or {}
    except yaml.YAMLError as exc:
        typer.echo(f"[error] Config file is not valid YAML: {exc}", err=True)
        raise typer.Exit(1)


def _print_preflight(report: pf.PreflightReport) -> None:
    """Pretty-print a preflight report."""
    typer.echo()
    for result in report.results:
        icon = "  [pass]" if result.passed else "  [FAIL]"
        typer.echo(f"{icon}  {result.name:<16} {result.detail}")
        if not result.passed and result.fix:
            typer.echo(f"         fix: {result.fix}")
    typer.echo()


# ---------------------------------------------------------------------------
# Network helpers
# ---------------------------------------------------------------------------


def _get_interface_ip(interface: str) -> Optional[str]:
    """Return the current IPv4 address on *interface*, or None."""
    try:
        result = subprocess.run(
            ["ip", "addr", "show", interface],
            capture_output=True, text=True, timeout=5,
        )
        m = re.search(r"inet (\d+\.\d+\.\d+\.\d+)/", result.stdout)
        return m.group(1) if m else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _assign_ip(interface: str, ip: str) -> None:
    """Assign *ip*/24 to *interface* if it has no address yet."""
    existing = _get_interface_ip(interface)
    if existing:
        return
    typer.echo(f"  Assigning {ip}/24 to {interface} ...")
    result = subprocess.run(
        ["sudo", "ip", "addr", "add", f"{ip}/24", "dev", interface],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        typer.echo(
            f"[error] Could not assign IP to {interface}:\n{result.stderr}\n"
            f"Run manually: sudo ip addr add {ip}/24 dev {interface}",
            err=True,
        )
        raise typer.Exit(1)
    subprocess.run(["sudo", "ip", "link", "set", interface, "up"], capture_output=True)


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _render_templates(cfg: dict, run_dir: Path, iso_path: Optional[Path] = None) -> None:
    """Render all config templates into *run_dir* before compose starts.

    Substitutes values from vme-config.yml into dnsmasq.conf, boot.ipxe,
    answer.toml, and preseed.cfg so the seed stack gets real values, not
    placeholder strings. iso_path is the resolved cached ISO — its filename
    is substituted into boot.ipxe so the boot script references the real file.
    """
    target = cfg.get("target", {})
    interface = cfg["provisioning_interface"]
    seed_ip = cfg.get("seed_ip") or _get_interface_ip(interface) or "192.168.100.1"
    os_name = target.get("os", "")

    # Resolve the real ISO filename for the boot script.
    # The other OS slot gets a placeholder so safe_substitute leaves it alone.
    if iso_path:
        proxmox_iso = iso_path.name if os_name == "proxmox-ve" else "proxmox-ve-not-cached.iso"
        ubuntu_iso  = iso_path.name if os_name == "ubuntu-server" else "ubuntu-server-not-cached.iso"
    else:
        proxmox_iso = "proxmox-ve-not-cached.iso"
        ubuntu_iso  = "ubuntu-server-not-cached.iso"

    subs = {
        "PROVISIONING_INTERFACE": interface,
        "DHCP_RANGE_START": cfg["dhcp_range_start"],
        "DHCP_RANGE_END": cfg["dhcp_range_end"],
        "DHCP_LEASE_TIME": cfg.get("dhcp_lease_time", "12h"),
        "SEED_IP": seed_ip,
        "NGINX_IP": seed_ip,
        "PROXMOX_ISO": proxmox_iso,
        "UBUNTU_ISO": ubuntu_iso,
        "TARGET_HOSTNAME": target.get("hostname", "node-01"),
        "TARGET_DOMAIN": target.get("domain", "local"),
        "TARGET_IP": target.get("ip", ""),
        "TARGET_PREFIX": target.get("prefix", "24"),
        "TARGET_GATEWAY": target.get("gateway", ""),
        "TARGET_NETMASK": target.get("netmask", "255.255.255.0"),
        "TARGET_DNS": target.get("dns", "8.8.8.8"),
        "TARGET_DISK": target.get("disk", "/dev/sda"),
        "TARGET_NIC": "eth0",
        "TARGET_SSH_PUBLIC_KEY": target.get("ssh_public_key", ""),
        "TARGET_ROOT_PASSWORD": target.get("root_password", "changeme"),
        "TARGET_EMAIL": target.get("email", "root@localhost"),
        "TARGET_PASSWORD_HASH": target.get("password_hash", "$6$rounds=4096$placeholder"),
    }

    templates = {
        _REPO_ROOT / "seed" / "dnsmasq" / "dnsmasq.conf": run_dir / "dnsmasq" / "dnsmasq.conf",
        _REPO_ROOT / "seed" / "ipxe" / "boot.ipxe":        run_dir / "ipxe" / "boot.ipxe",
        _REPO_ROOT / "targets" / "proxmox" / "answer.toml": run_dir / "proxmox" / "answer.toml",
        _REPO_ROOT / "targets" / "ubuntu" / "preseed.cfg":  run_dir / "cloud-init" / "user-data",
        _REPO_ROOT / "targets" / "ubuntu" / "meta-data":    run_dir / "cloud-init" / "meta-data",
    }

    for src, dest in templates.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_text()
        rendered = Template(raw).safe_substitute(subs)
        dest.write_text(rendered)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def setup(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to write vme-config.yml"),
) -> None:
    """Guided setup wizard — creates your config file interactively."""
    wizard.run(config)


@app.command()
def preflight(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
) -> None:
    """Run pre-flight checks and report pass/fail without starting a deployment."""
    typer.echo("Running pre-flight checks ...")
    report = pf.run_all(config)
    _print_preflight(report)
    if report.passed:
        typer.echo("All checks passed. Ready to deploy.")
    else:
        typer.echo("[error] One or more checks failed. Fix the issues above before deploying.", err=True)
        raise typer.Exit(1)


@app.command()
def deploy(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
    skip_preflight: bool = typer.Option(False, "--skip-preflight", help="Skip pre-flight checks (not recommended)."),
) -> None:
    """Deploy an OS to target hardware over PXE."""
    cfg = _load_config(config)

    if not skip_preflight:
        typer.echo("Running pre-flight checks ...")
        report = pf.run_all(config)
        _print_preflight(report)
        if not report.passed:
            typer.echo("[error] Pre-flight failed. Aborting deployment.", err=True)
            raise typer.Exit(1)
        typer.echo("Pre-flight passed.\n")

    # Ensure the provisioning interface has an IP.
    interface = cfg.get("provisioning_interface", "")
    seed_ip = cfg.get("seed_ip", "192.168.100.1")
    if interface:
        _assign_ip(interface, seed_ip)

    # Download / verify the OS image.
    os_name: str = cfg.get("target", {}).get("os", "")
    typer.echo(f"Ensuring {os_name} image is cached ...")
    try:
        iso_path = img.ensure_image(os_name, cfg)
        typer.echo(f"  Image ready: {iso_path.name}\n")
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)

    # Render all config templates into ./run/ before compose starts.
    run_dir = config.parent / "run"
    typer.echo("Preparing seed stack config ...")
    _render_templates(cfg, run_dir, iso_path=iso_path)

    typer.echo("Starting seed stack ...")
    _run_compose(cfg, config.parent, iso_path, up=True)

    typer.echo("\nSeed stack is running.")
    typer.echo("Power on the target machine now. It will PXE boot and install automatically.")
    typer.echo("Press Ctrl+C here when provisioning is complete to stop the seed stack.\n")

    try:
        _compose_logs(config.parent)
    except KeyboardInterrupt:
        typer.echo("\nStopping seed stack ...")
        _run_compose(cfg, config.parent, iso_path, up=False)
        typer.echo("Done.")


@app.command()
def status(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
) -> None:
    """Show the current state of the seed stack."""
    result = subprocess.run(
        ["docker", "compose", "ps"],
        cwd=config.parent,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        typer.echo("[error] Could not query Docker Compose status.", err=True)
        typer.echo(result.stderr, err=True)
        raise typer.Exit(1)
    typer.echo(result.stdout)


# ---------------------------------------------------------------------------
# Images subcommands
# ---------------------------------------------------------------------------


@images_app.command("list")
def images_list(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
) -> None:
    """List cached OS images."""
    cfg = _load_config(config)
    cached = img.list_cached(cfg)
    if not cached:
        typer.echo("No images cached yet. Run 'vme images pull' to download.")
        return
    typer.echo(f"{'Filename':<50} {'Size':>10}")
    typer.echo("-" * 62)
    for entry in cached:
        typer.echo(f"{entry['filename']:<50} {entry['size_mb']:>9.1f} MB")


@images_app.command("pull")
def images_pull(
    os_name: str = typer.Argument(..., help="OS to pre-cache: 'proxmox-ve' or 'ubuntu-server'"),
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
) -> None:
    """Pre-cache an OS image before deployment."""
    cfg = _load_config(config)
    typer.echo(f"Pulling {os_name} image ...")
    try:
        dest = img.ensure_image(os_name, cfg)
        typer.echo(f"Cached: {dest}")
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)


@images_app.command("clean")
def images_clean(
    config: Path = typer.Option(_CONFIG_DEFAULT, "--config", "-c", help="Path to vme-config.yml"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete all cached OS images."""
    cfg = _load_config(config)
    cache = img.cache_dir_for(cfg)
    if not yes:
        typer.confirm(f"Delete all cached images in {cache}?", abort=True)
    removed = img.clean_cache(cfg)
    typer.echo(f"Removed {removed} file(s) from {cache}.")


# ---------------------------------------------------------------------------
# Docker Compose helpers
# ---------------------------------------------------------------------------


def _run_compose(cfg: dict, cwd: Path, iso_path: Optional[Path] = None, *, up: bool) -> None:
    """Start or stop the Docker Compose seed stack, passing required env vars."""
    interface = cfg.get("provisioning_interface", "")
    seed_ip = cfg.get("seed_ip") or _get_interface_ip(interface) or "192.168.100.1"
    cache_dir = str(img.cache_dir_for(cfg))

    env_vars = {
        "PROVISIONING_INTERFACE": interface,
        "DHCP_RANGE_START": str(cfg.get("dhcp_range_start", "")),
        "DHCP_RANGE_END": str(cfg.get("dhcp_range_end", "")),
        "DHCP_LEASE_TIME": str(cfg.get("dhcp_lease_time", "12h")),
        "SEED_IP": seed_ip,
        "IMAGE_CACHE_DIR": cache_dir,
    }

    import os
    compose_env = {**os.environ, **env_vars}

    if up:
        cmd = ["docker", "compose", "up", "-d", "--build"]
    else:
        cmd = ["docker", "compose", "down"]

    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=compose_env)
    if result.returncode != 0:
        typer.echo(f"[error] docker compose failed:\n{result.stderr}", err=True)
        raise typer.Exit(1)


def _compose_logs(cwd: Path) -> None:
    """Tail Docker Compose logs (blocks until interrupted)."""
    subprocess.run(["docker", "compose", "logs", "-f"], cwd=cwd)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    app()


if __name__ == "__main__":
    main()
