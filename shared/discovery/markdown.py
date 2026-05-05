"""Render a DiscoveryReport as human-readable Markdown.

The Markdown report is paired 1:1 with the JSON report and serves two
audiences:

  - operators reviewing what discovery found before joining a network
  - VLE / VSE documentation consumers who want a snapshot of the network
    they're being deployed into

Output is deterministic given the same input — no timestamps embedded in
section bodies (only at the top), stable sort orders, no random IDs.
"""

from __future__ import annotations

from .report import DiscoveryReport, Host, RouterInfo, Service


def render(report: DiscoveryReport) -> str:
    parts: list[str] = []
    parts.append(_header(report))
    parts.append(_local(report))
    parts.append(_router(report.router))
    parts.append(_hosts(report.hosts))
    parts.append(_vlans(report))
    parts.append(_capabilities(report))
    parts.append(_warnings(report))
    return "\n\n".join(p for p in parts if p).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def _header(report: DiscoveryReport) -> str:
    scope = report.scan_scope
    return (
        f"# Network Discovery Report\n"
        f"\n"
        f"- **Generated:** {report.generated_at.isoformat()}\n"
        f"- **By:** `{report.generated_by}`\n"
        f"- **Duration:** {report.duration_seconds:.1f} s\n"
        f"- **Scope:** "
        f"CIDRs `{', '.join(scope.cidrs) or '(local)'}`, "
        f"iface `{scope.iface or 'auto'}`, "
        f"passive `{scope.passive_seconds}s`, "
        f"active `{scope.active}`, "
        f"fingerprint `{scope.fingerprint}`, "
        f"SNMP `{scope.snmp_community or 'off'}`"
    )


def _local(report: DiscoveryReport) -> str:
    n = report.network
    lines = ["## Local host\n"]
    if n.default_gateway:
        lines.append(
            f"- **Default gateway:** `{n.default_gateway}` via `{n.default_gateway_iface}`"
        )
    else:
        lines.append("- **Default gateway:** _not detected_")
    if n.dns_resolvers:
        lines.append(f"- **DNS resolvers:** {', '.join('`' + r + '`' for r in n.dns_resolvers)}")
    if n.search_domains:
        lines.append(f"- **Search domains:** {', '.join('`' + s + '`' for s in n.search_domains)}")
    if n.dhcp_lease:
        lines.append(
            "- **DHCP lease:** "
            + ", ".join(f"`{k}={v}`" for k, v in sorted(n.dhcp_lease.items()))
        )
    if n.interfaces:
        lines.append("\n### Interfaces\n")
        lines.append("| Name | MAC | Address(es) | MTU | Up | VLAN |")
        lines.append("|---|---|---|---:|:-:|---|")
        for iface in n.interfaces:
            addrs = ", ".join(iface.cidr) if iface.cidr else ", ".join(iface.ipv4) or "—"
            vlan = f"{iface.vlan_id}@{iface.parent}" if iface.is_vlan and iface.vlan_id else ""
            up = "✓" if iface.is_up else " "
            lines.append(f"| `{iface.name}` | `{iface.mac or '—'}` | {addrs} | {iface.mtu} | {up} | {vlan} |")
    return "\n".join(lines)


def _router(router: RouterInfo) -> str:
    if not router.ip:
        return "## Gateway / router\n\n_No gateway host could be confirmed._"
    lines = [
        "## Gateway / router\n",
        f"- **IP:** `{router.ip}`",
        f"- **MAC:** `{router.mac or '—'}`",
        f"- **Vendor:** `{router.vendor}` (confidence {router.confidence:.2f})",
    ]
    if router.product:
        lines.append(f"- **Product:** {router.product}")
    if router.version:
        lines.append(f"- **Version:** `{router.version}`")
    if router.api_endpoint:
        lines.append(f"- **API endpoint:** `{router.api_endpoint}` ({router.api_kind})")
    if router.evidence:
        lines.append("\n**Evidence:**")
        for item in router.evidence:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _hosts(hosts: list[Host]) -> str:
    if not hosts:
        return "## Hosts\n\n_No hosts observed._"
    lines = ["## Hosts\n"]
    lines.append(f"_{len(hosts)} hosts observed._\n")
    for host in hosts:
        lines.append(_host_block(host))
        lines.append("")
    return "\n".join(lines).rstrip()


def _host_block(host: Host) -> str:
    head = f"### `{host.ip}`"
    if host.hostname:
        head += f" — {host.hostname}"
    parts = [head]
    meta_bits: list[str] = []
    if host.mac:
        meta_bits.append(f"MAC `{host.mac}`")
    if host.vendor:
        meta_bits.append(f"vendor `{host.vendor}`")
    if host.discovered_via:
        meta_bits.append(f"via {', '.join('`' + v + '`' for v in host.discovered_via)}")
    if host.role_hints:
        meta_bits.append("roles: " + ", ".join(f"`{r}`" for r in host.role_hints))
    if meta_bits:
        parts.append("- " + " · ".join(meta_bits))
    if host.services:
        parts.append("")
        parts.append("| Port | Svc | Product | Version | TLS | Title |")
        parts.append("|---:|---|---|---|:-:|---|")
        for svc in sorted(host.services, key=lambda s: s.port):
            parts.append(_service_row(svc))
    return "\n".join(parts)


def _service_row(svc: Service) -> str:
    title = svc.http_title or (svc.banner[:60] if svc.banner else "")
    title = title.replace("|", "\\|")
    tls = "✓" if svc.tls else " "
    return (
        f"| {svc.port} | {svc.name or '—'} | "
        f"{svc.product or svc.http_server or '—'} | "
        f"{svc.version or '—'} | {tls} | {title or '—'} |"
    )


def _vlans(report: DiscoveryReport) -> str:
    if not report.vlans:
        return ""
    lines = ["## VLANs observed\n", "| ID | CIDR | Source |", "|---:|---|---|"]
    for v in sorted(report.vlans, key=lambda x: x.id):
        lines.append(f"| {v.id} | {v.cidr or '—'} | `{v.source}` |")
    return "\n".join(lines)


def _capabilities(report: DiscoveryReport) -> str:
    if not report.capabilities:
        return ""
    lines = ["## Capabilities\n", "| Name | Available | Reason |", "|---|:-:|---|"]
    for cap in report.capabilities:
        lines.append(f"| `{cap.name}` | {'✓' if cap.available else '✗'} | {cap.reason} |")
    return "\n".join(lines)


def _warnings(report: DiscoveryReport) -> str:
    if not report.warnings:
        return ""
    lines = ["## Warnings\n"]
    for w in report.warnings:
        lines.append(f"- {w}")
    return "\n".join(lines)
