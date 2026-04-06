#!/usr/bin/env python3
"""VME — Velocitee Metal Provisioning Engine CLI."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer
import yaml

from . import preflight as pf
from . import manifest as mf
from . import images as img

app = typer.Typer(
    name="vme",
    help="Velocitee Metal Provisioning Engine — PXE-boot and unattended OS install.",
    add_completion=False,
)
images_app = typer.Typer(help="Manage cached OS images.")
app.add_typer(images_app, name="images")

_CONFIG_DEFAULT = Path("vme-config.yml")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_config(config_path: Path) -> dict:
    """Load and return the YAML config. Exits with a message on failure."""
    if not config_path.exists():
        typer.echo(
            f"[error] Config file not found: {config_path}\n"
            "Copy vme-config.example.yml to vme-config.yml and fill in your values.",
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
# Commands
# ---------------------------------------------------------------------------


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

    os_name: str = cfg.get("target", {}).get("os", "")
    typer.echo(f"Ensuring {os_name} image is cached ...")
    try:
        iso_path = img.ensure_image(os_name, cfg)
        typer.echo(f"  Image ready: {iso_path.name}\n")
    except (RuntimeError, ValueError) as exc:
        typer.echo(f"[error] {exc}", err=True)
        raise typer.Exit(1)

    typer.echo("Starting seed stack ...")
    _run_compose(cfg, config.parent, up=True)

    typer.echo("\nSeed stack is running.")
    typer.echo("Power on the target machine and wait for it to PXE boot.")
    typer.echo("Press Ctrl+C to stop the seed stack when provisioning is complete.")

    try:
        _compose_logs(config.parent)
    except KeyboardInterrupt:
        typer.echo("\nStopping seed stack ...")
        _run_compose(cfg, config.parent, up=False)
        typer.echo("Seed stack stopped.")


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
    os_name: str = typer.Argument(
        ...,
        help="OS to pre-cache: 'proxmox-ve' or 'ubuntu-server'",
    ),
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
# Internal Docker Compose helpers
# ---------------------------------------------------------------------------


def _run_compose(cfg: dict, cwd: Path, *, up: bool) -> None:
    """Start or stop the Docker Compose seed stack."""
    if up:
        cmd = ["docker", "compose", "up", "-d", "--build"]
    else:
        cmd = ["docker", "compose", "down"]
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
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
