"""VNE Path B — join an existing network.

A join run is fundamentally different from a deploy:

  - We do *not* create an OPNsense VM. The user already has a router.
  - We do *not* configure VLANs/DHCP/DNS. We *observe* and record them.
  - We *do* run discovery, identify the gateway, pick a router adapter,
    and write a manifest VSE/VLE can consume.

The flow:

  1. Run discovery (shared.discovery.run_discovery).
  2. Pick a router adapter — auto-detect by default, override with --adapter.
  3. Show the operator what we found and (unless --yes) confirm.
  4. Run the adapter to enrich the join (e.g. OPNsense API pull when creds set).
  5. Write the JSON + Markdown discovery report to disk.
  6. Build the engines.vne manifest record with mode='join'.
  7. Validate against the VNE manifest schema.
  8. Write the manifest.

Joining is read-only end-to-end: we never write to the router during join.
A subsequent `vne deploy` against the joined manifest is what mutates state.

No state file is needed for join — the entire operation is a single transaction
that either produces a manifest or doesn't. Re-running is safe (and idempotent
in the trivial sense — same input, same output).
"""

from __future__ import annotations

import json
import logging
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from shared import manifest as mf
from shared.cli import fatal, warn
from shared.discovery import (
    DiscoveryReport,
    render_markdown,
    run_discovery,
)
from shared.discovery.adapters import lookup as lookup_adapter
from shared.discovery.adapters.base import autopick

log = logging.getLogger("vne.join")


# ---------------------------------------------------------------------------
# Public entry — called from deploy.py's `vne join` command
# ---------------------------------------------------------------------------

def run_join(
    *,
    cidrs: list[str],
    iface: str,
    passive_seconds: int,
    snmp_community: str,
    adapter_slug: str,
    work_dir: Path,
    output_dir: Path,
    vme_manifest: Path | None,
    yes: bool,
    vne_version: str,
) -> Path:
    """Execute a join run end-to-end. Returns the path to the written manifest."""
    started_at = datetime.now(timezone.utc)

    # 1. Discovery
    typer.echo("[1/6] Running discovery ...")
    report = run_discovery(
        cidrs=cidrs,
        iface=iface,
        passive_seconds=passive_seconds,
        snmp_community=snmp_community,
    )

    typer.echo(
        f"      observed {len(report.hosts)} hosts; "
        f"gateway candidate '{report.router.vendor}' "
        f"@ {report.router.ip or report.network.default_gateway} "
        f"(confidence {report.router.confidence:.2f})"
    )
    if report.warnings:
        for w in report.warnings:
            warn(w)

    # 2. Pick adapter
    typer.echo("[2/6] Selecting router adapter ...")
    chosen = adapter_slug or autopick(report)
    typer.echo(f"      adapter: {chosen}")
    try:
        adapter_cls = lookup_adapter(chosen)
    except KeyError as exc:
        fatal(str(exc))

    # 3. Show + confirm
    typer.echo("[3/6] Summary:")
    _print_summary(report, chosen)
    if not yes:
        if not typer.confirm("Proceed with join?", default=True):
            fatal("aborted by user")

    # 4. Reports to disk
    typer.echo("[4/6] Writing discovery reports ...")
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "discovery-report.json"
    md_path = output_dir / "discovery-report.md"
    json_path.write_text(report.to_json() + "\n")
    md_path.write_text(render_markdown(report))
    typer.echo(f"      JSON: {json_path}")
    typer.echo(f"      MD:   {md_path}")

    # 5. Run adapter
    typer.echo(f"[5/6] Running '{chosen}' adapter ...")
    adapter = adapter_cls(report=report, work_dir=work_dir, state_dir=work_dir / "state")
    try:
        adapter_result = adapter.execute()
    except NotImplementedError as exc:
        fatal(str(exc))
    if not adapter_result.success:
        fatal(f"adapter '{chosen}' failed: {adapter_result.error}")

    # Adjust the manifest fragment to point at the actual report path on disk.
    fragment = dict(adapter_result.manifest_fragment)
    fragment["discovery_report"] = json_path.name

    # 6. Build + write VNE manifest
    typer.echo("[6/6] Writing VNE manifest ...")
    completed_at = datetime.now(timezone.utc)
    base = _load_or_synthesize_vme(vme_manifest, report)

    vne_extra = {
        "mode": "join",
        "joined_network": fragment,
        "capabilities": [c.model_dump() for c in adapter_result.capabilities],
    }
    full = mf.append_engine(
        dict(base),
        engine="vne",
        version=vne_version,
        started_at=started_at,
        completed_at=completed_at,
        extra=vne_extra,
    )

    _validate_against_vne_schema(full)

    out_path = mf.write(full, output_dir)
    typer.echo(f"      manifest: {out_path}")

    typer.echo("")
    typer.echo("Join complete.")
    if chosen == "unmanaged":
        typer.echo(
            "  Adapter was 'unmanaged' — VSE/VLE will work in observation mode "
            "for this network."
        )
    typer.echo(f"  Hand off to VSE with:  vse deploy --manifest {out_path}")

    return out_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_summary(report: DiscoveryReport, adapter_slug: str) -> None:
    n = report.network
    r = report.router
    typer.echo(f"  Default gateway:  {n.default_gateway} via {n.default_gateway_iface}")
    typer.echo(f"  Detected vendor:  {r.vendor} ({r.product or '—'})  conf={r.confidence:.2f}")
    if r.api_endpoint:
        typer.echo(f"  Management API:   {r.api_endpoint} ({r.api_kind})")
    typer.echo(f"  DNS resolvers:    {', '.join(n.dns_resolvers) or '—'}")
    typer.echo(f"  Search domains:   {', '.join(n.search_domains) or '—'}")
    typer.echo(f"  Hosts observed:   {len(report.hosts)}")
    if report.vlans:
        ids = ", ".join(str(v.id) for v in sorted(report.vlans, key=lambda v: v.id))
        typer.echo(f"  VLANs observed:   {ids}")
    typer.echo(f"  Adapter:          {adapter_slug}")


def _load_or_synthesize_vme(path: Path | None, report: DiscoveryReport) -> dict[str, Any]:
    """When --manifest is given, validate it. Otherwise synthesize a minimal one
    rooted in the seed machine's identity so the engines manifest still validates.

    The synthesized record stores a 'skipped' marker for VME so downstream
    engines see that no bare-metal provisioning happened. Operators joining a
    pre-existing network are a first-class flow, not a fallback.
    """
    if path:
        if not path.exists():
            fatal(f"VME manifest not found: {path}")
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            fatal(f"VME manifest is not valid JSON: {exc}")
        return data

    # Synthesize.
    hostname = socket.gethostname() or "unknown"
    primary_ip = ""
    for li in report.network.interfaces:
        if li.name == report.network.default_gateway_iface and li.ipv4:
            primary_ip = li.ipv4[0]
            break
    if not primary_ip:
        for li in report.network.interfaces:
            if li.name != "lo" and li.ipv4:
                primary_ip = li.ipv4[0]
                break

    return {
        "schema_version": "1.0",
        "target": {
            "hostname": hostname,
            "ip": primary_ip,
            "prefix": 24,
            "gateway": report.network.default_gateway,
            "dns": (report.network.dns_resolvers or [""])[0],
            "disk": "",
            "os": "joined",
        },
        "access": {
            "username": "",
            "ssh_public_key": "",
            "ssh_port": 22,
        },
        "engines": {
            "vme": {
                "status": "success",
                "version": "skipped",
                "started_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": 0,
                "note": "VME was not run for this manifest — VNE join was invoked standalone.",
            }
        },
    }


def _validate_against_vne_schema(manifest: dict[str, Any]) -> None:
    """Local copy of the validator used by deploy.py; avoids a circular import."""
    import jsonschema

    schema_path = Path(__file__).parent / "schema" / "vne-manifest.schema.json"
    schema = json.loads(schema_path.read_text())
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    if errors:
        msg = "VNE join manifest failed self-validation:\n" + "\n".join(
            f"  - {'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
            for e in errors
        )
        fatal(msg)
