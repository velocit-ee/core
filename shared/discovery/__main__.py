"""velocitee-discover CLI — `python -m shared.discovery` or the
`velocitee-discover` console script.

Two subcommands:

  scan    run a discovery scan, write JSON + Markdown reports
  show    render an existing JSON report as Markdown to stdout

Examples:

  velocitee-discover scan
  velocitee-discover scan --cidr 10.0.0.0/24 --cidr 10.10.0.0/24 \
      --json out.json --md out.md
  velocitee-discover show out.json
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from .. import logging as vlogging
from ..cli import fatal, make_app, run_app
from . import markdown, scan
from .report import DiscoveryReport

app = make_app("velocitee-discover")


@app.command(name="scan")
def cmd_scan(
    cidr: list[str] = typer.Option(
        None, "--cidr",
        help="CIDR(s) to scan. Can be passed multiple times. Defaults to the local subnet.",
    ),
    iface: str = typer.Option(
        "", "--iface",
        help="Network interface to bind multicast listeners to (passive discovery).",
    ),
    ports: str = typer.Option(
        "", "--ports",
        help="Comma-separated TCP ports to connect-scan. Default is the built-in management set.",
    ),
    passive_seconds: int = typer.Option(
        6, "--passive-seconds",
        help="How long to listen for mDNS/SSDP. 0 disables passive listening.",
    ),
    no_active: bool = typer.Option(
        False, "--no-active",
        help="Skip the active sweep + connect-scan. Useful for documentation runs.",
    ),
    no_fingerprint: bool = typer.Option(
        False, "--no-fingerprint",
        help="Skip per-service banner/HTTP/TLS grabs.",
    ),
    snmp_community: str = typer.Option(
        "", "--snmp-community",
        help="SNMP v2c community for sysDescr probe. Empty = no SNMP.",
    ),
    use_nmap: str = typer.Option(
        "auto", "--use-nmap",
        help="Use nmap if available: 'auto' (default), 'on' (require), 'off' (skip).",
    ),
    timeout_s: float = typer.Option(
        0.6, "--timeout",
        help="TCP connect timeout per probe (seconds).",
    ),
    workers: int = typer.Option(
        256, "--workers", help="Concurrency for sweep + connect-scan.",
    ),
    json_out: Path = typer.Option(
        Path("discovery-report.json"), "--json", "-j",
        help="Where to write the JSON report.",
    ),
    md_out: Path = typer.Option(
        Path("discovery-report.md"), "--md", "-m",
        help="Where to write the Markdown report.",
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a discovery scan and write JSON + Markdown reports."""
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    vlogging.configure(level=level)

    port_list: list[int] = []
    if ports:
        try:
            port_list = [int(p.strip()) for p in ports.split(",") if p.strip()]
        except ValueError:
            fatal("--ports must be comma-separated integers")

    typer.echo("velocitee-discover — scanning ...")
    report = scan.run_discovery(
        cidrs=list(cidr) if cidr else [],
        iface=iface,
        ports=port_list or None,
        passive_seconds=passive_seconds,
        do_active=not no_active,
        do_fingerprint=not no_fingerprint,
        snmp_community=snmp_community,
        use_nmap=use_nmap,
        timeout_s=timeout_s,
        workers=workers,
    )

    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(report.to_json() + "\n")
    md_out.parent.mkdir(parents=True, exist_ok=True)
    md_out.write_text(markdown.render(report))

    typer.echo(
        f"\n{len(report.hosts)} hosts, "
        f"router='{report.router.vendor}' "
        f"({report.router.confidence:.2f} confidence), "
        f"{len(report.warnings)} warnings."
    )
    typer.echo(f"  JSON:     {json_out}")
    typer.echo(f"  Markdown: {md_out}")


@app.command(name="show")
def cmd_show(
    report: Path = typer.Argument(..., help="Path to a discovery-report.json"),
) -> None:
    """Render an existing JSON discovery report as Markdown to stdout."""
    if not report.exists():
        fatal(f"report not found: {report}")
    try:
        data = report.read_text()
    except OSError as exc:
        fatal(f"could not read {report}: {exc}")
    try:
        rep = DiscoveryReport.from_json(data)
    except Exception as exc:  # noqa: BLE001 — pydantic ValidationError + JSON
        fatal(f"could not parse report: {exc}")
    typer.echo(markdown.render(rep))


def main() -> None:
    run_app(app)


if __name__ == "__main__":  # pragma: no cover
    main()
