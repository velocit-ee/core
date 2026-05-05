"""Router/gateway identification heuristics.

Given a Host (the gateway) and the surrounding context (other hosts on the
same network), we score against a small library of router/firewall vendors
and return the highest-confidence match plus the evidence we used.

The scoring is intentionally simple — additive points per signal — because
the alternative (per-vendor regex/state machines) becomes a maintenance
burden and lulls callers into trusting matches more than they should. The
'unknown' result is a first-class outcome; downstream code (the unmanaged
adapter) handles it cleanly.

Each signature lists *positive* signals only. We never penalize for a missing
signal — many of these probes only fire when the right port is open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .report import Host, RouterInfo, Service


@dataclass
class _Signature:
    slug: str
    product: str
    api_kind: str = ""
    # Each signal: (description, weight). Weight ≈ confidence delta.
    signals: list[tuple[str, float]] = field(default_factory=list)


def identify(gateway: Host | None, all_hosts: list[Host]) -> RouterInfo:
    """Pick the best vendor match for `gateway`. Returns a populated RouterInfo.

    If `gateway` is None (no default gateway resolvable), returns a RouterInfo
    with vendor='unknown' and confidence=0 — callers should still treat this
    as a valid result and fall through to the unmanaged adapter.
    """
    if gateway is None:
        return RouterInfo(vendor="unknown", confidence=0.0, evidence=["no gateway host found"])

    scores: dict[str, _Signature] = {}
    for sig_factory in _SIGNATURE_FACTORIES:
        sig = sig_factory(gateway)
        if sig:
            scores[sig.slug] = sig

    if not scores:
        return RouterInfo(
            ip=gateway.ip,
            mac=gateway.mac,
            vendor="unknown",
            confidence=0.0,
            evidence=["gateway found but no vendor signals matched"],
        )

    # Pick the slug with the most evidence weight.
    best_slug, best_sig = max(
        scores.items(),
        key=lambda item: sum(w for _, w in item[1].signals),
    )
    weight = sum(w for _, w in best_sig.signals)
    confidence = min(1.0, weight / 2.0)  # 2.0 weight ≈ certain

    api_endpoint = ""
    for svc in gateway.services:
        if best_sig.api_kind == "opnsense" and svc.port in {443, 8443} and svc.tls:
            api_endpoint = f"https://{gateway.ip}:{svc.port}/api"
        elif best_sig.api_kind == "pfsense" and svc.port in {443, 8443} and svc.tls:
            api_endpoint = f"https://{gateway.ip}:{svc.port}"
        elif best_sig.api_kind == "mikrotik-rest" and svc.port in {443, 8443} and svc.tls:
            api_endpoint = f"https://{gateway.ip}/rest"
        elif best_sig.api_kind == "unifi" and svc.port == 443 and svc.tls:
            api_endpoint = f"https://{gateway.ip}/api"
        elif best_sig.api_kind == "edgeos" and svc.port == 443 and svc.tls:
            api_endpoint = f"https://{gateway.ip}"

    return RouterInfo(
        ip=gateway.ip,
        mac=gateway.mac,
        vendor=best_slug,
        product=best_sig.product,
        version=_pick_version(gateway),
        api_endpoint=api_endpoint,
        api_kind=best_sig.api_kind,
        confidence=confidence,
        evidence=[desc for desc, _ in best_sig.signals],
    )


# ---------------------------------------------------------------------------
# Per-vendor signatures
#
# Each function inspects a Host and returns _Signature with the signals it
# matched, or None. Adding a vendor means adding a function and listing it in
# _SIGNATURE_FACTORIES.
# ---------------------------------------------------------------------------

def _opnsense(host: Host) -> _Signature | None:
    sig = _Signature("opnsense", product="OPNsense", api_kind="opnsense")
    for svc in host.services:
        if svc.port in {443, 8443}:
            if "OPNsense" in svc.http_title:
                sig.signals.append((f"port {svc.port} HTTP title contains 'OPNsense'", 1.5))
            if any("opnsense" in s.lower() for s in svc.tls_san):
                sig.signals.append((f"TLS SAN on {svc.port} mentions 'opnsense'", 0.8))
            if "OPNsense" in (svc.http_server or ""):
                sig.signals.append((f"port {svc.port} Server header is OPNsense", 1.2))
    return sig if sig.signals else None


def _pfsense(host: Host) -> _Signature | None:
    sig = _Signature("pfsense", product="pfSense", api_kind="pfsense")
    for svc in host.services:
        if svc.port in {443, 8443}:
            if "pfSense" in svc.http_title:
                sig.signals.append((f"port {svc.port} HTTP title contains 'pfSense'", 1.5))
            if any("pfsense" in s.lower() for s in svc.tls_san):
                sig.signals.append((f"TLS SAN on {svc.port} mentions 'pfsense'", 0.8))
    return sig if sig.signals else None


def _mikrotik(host: Host) -> _Signature | None:
    sig = _Signature("mikrotik", product="MikroTik RouterOS", api_kind="mikrotik-rest")
    for svc in host.services:
        if svc.port in {8728, 8729}:
            sig.signals.append((f"port {svc.port} (MikroTik API) open", 1.5))
        if svc.port == 22 and "ROSSSH" in svc.banner:
            sig.signals.append(("SSH banner contains 'ROSSSH'", 1.2))
        if svc.port in {80, 443} and "RouterOS" in (svc.http_title + svc.http_server):
            sig.signals.append((f"port {svc.port} title/Server mentions RouterOS", 1.0))
    snmp = " ".join(host.role_hints).lower()
    if "routeros" in snmp or "mikrotik" in snmp:
        sig.signals.append(("SNMP sysDescr mentions MikroTik/RouterOS", 1.2))
    return sig if sig.signals else None


def _unifi(host: Host) -> _Signature | None:
    sig = _Signature("unifi", product="Ubiquiti UniFi", api_kind="unifi")
    for svc in host.services:
        if svc.port in {443, 8443} and ("UniFi" in svc.http_title or "Ubiquiti" in svc.http_title):
            sig.signals.append((f"port {svc.port} HTTP title mentions UniFi/Ubiquiti", 1.5))
        if svc.port == 8080 and "UniFi" in svc.http_title:
            sig.signals.append(("port 8080 HTTP title mentions UniFi", 1.0))
    snmp = " ".join(host.role_hints).lower()
    if "ubnt" in snmp or "unifi" in snmp:
        sig.signals.append(("SNMP sysDescr mentions Ubiquiti/UniFi", 1.2))
    return sig if sig.signals else None


def _edgeos(host: Host) -> _Signature | None:
    sig = _Signature("edgeos", product="Ubiquiti EdgeOS", api_kind="edgeos")
    for svc in host.services:
        if svc.port == 443 and "EdgeOS" in svc.http_title:
            sig.signals.append(("port 443 HTTP title mentions EdgeOS", 1.5))
    snmp = " ".join(host.role_hints).lower()
    if "edgeos" in snmp:
        sig.signals.append(("SNMP sysDescr mentions EdgeOS", 1.2))
    return sig if sig.signals else None


def _openwrt(host: Host) -> _Signature | None:
    sig = _Signature("openwrt", product="OpenWrt")
    for svc in host.services:
        if svc.port in {80, 443}:
            blob = (svc.http_title + " " + svc.http_server).lower()
            if "openwrt" in blob or "luci" in blob:
                sig.signals.append((f"port {svc.port} mentions OpenWrt/LuCI", 1.5))
        if svc.port == 22 and "Dropbear" in svc.banner:
            sig.signals.append(("SSH banner is Dropbear (OpenWrt default)", 0.5))
    snmp = " ".join(host.role_hints).lower()
    if "openwrt" in snmp:
        sig.signals.append(("SNMP sysDescr mentions OpenWrt", 1.2))
    return sig if sig.signals else None


def _cisco_ios(host: Host) -> _Signature | None:
    sig = _Signature("cisco", product="Cisco IOS")
    for svc in host.services:
        if svc.port == 22 and re.search(r"Cisco|IOS", svc.banner):
            sig.signals.append(("SSH banner mentions Cisco/IOS", 1.2))
        if svc.port == 23 and "Cisco" in svc.banner:
            sig.signals.append(("Telnet banner mentions Cisco", 1.0))
    snmp = " ".join(host.role_hints).lower()
    if "cisco ios" in snmp or "cisco internetwork" in snmp:
        sig.signals.append(("SNMP sysDescr mentions Cisco IOS", 1.5))
    return sig if sig.signals else None


def _fortigate(host: Host) -> _Signature | None:
    sig = _Signature("fortigate", product="Fortinet FortiGate")
    for svc in host.services:
        if svc.port in {443, 8443} and "Forti" in svc.http_title:
            sig.signals.append((f"port {svc.port} HTTP title mentions Fortinet", 1.5))
    snmp = " ".join(host.role_hints).lower()
    if "fortigate" in snmp or "fortinet" in snmp:
        sig.signals.append(("SNMP sysDescr mentions Fortinet/FortiGate", 1.2))
    return sig if sig.signals else None


def _proxmox(host: Host) -> _Signature | None:
    sig = _Signature("proxmox", product="Proxmox VE")
    for svc in host.services:
        if svc.port == 8006 and svc.tls:
            sig.signals.append(("port 8006 (Proxmox VE) reachable over TLS", 1.5))
        if svc.port in {443, 8006} and "Proxmox" in svc.http_title:
            sig.signals.append((f"port {svc.port} HTTP title mentions Proxmox", 1.0))
    return sig if sig.signals else None


def _generic_router(host: Host) -> _Signature | None:
    """Fallback: catches anything offering a web UI and SSH but no specific match."""
    sig = _Signature("generic-router", product="Generic router/firewall")
    has_web = any(svc.port in {80, 443, 8080, 8443} for svc in host.services)
    has_ssh = any(svc.port == 22 for svc in host.services)
    if has_web:
        sig.signals.append(("gateway exposes a web UI", 0.4))
    if has_ssh:
        sig.signals.append(("gateway exposes SSH", 0.2))
    return sig if sig.signals else None


_SIGNATURE_FACTORIES = (
    _opnsense, _pfsense, _mikrotik, _unifi, _edgeos,
    _openwrt, _cisco_ios, _fortigate, _proxmox,
    _generic_router,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pick_version(host: Host) -> str:
    """Best-effort version string from any service we fingerprinted."""
    for svc in host.services:
        if svc.version:
            return svc.version
    # Look in HTTP title for a 'X.Y' or 'X.Y.Z' suffix.
    for svc in host.services:
        m = re.search(r"\b(\d+\.\d+(?:\.\d+)?)\b", svc.http_title or "")
        if m:
            return m.group(1)
    return ""


def annotate_role_hints(hosts: list[Host], gateway_ip: str, dns_ips: set[str]) -> None:
    """Add 'gateway', 'dns', 'router' role tags to the matching hosts in place."""
    for host in hosts:
        if host.ip == gateway_ip:
            for tag in ("gateway", "router"):
                if tag not in host.role_hints:
                    host.role_hints.append(tag)
        if host.ip in dns_ips and "dns" not in host.role_hints:
            host.role_hints.append("dns")


def merge_router_into_host_hints(report_router: RouterInfo, hosts: list[Host]) -> None:
    """If we identified a vendor, surface it on the gateway host's role_hints."""
    if not report_router.ip or report_router.vendor in {"unknown", ""}:
        return
    for host in hosts:
        if host.ip == report_router.ip:
            tag = f"vendor:{report_router.vendor}"
            if tag not in host.role_hints:
                host.role_hints.append(tag)
            break


__all__ = [
    "identify",
    "annotate_role_hints",
    "merge_router_into_host_hints",
]


# Suppress F401 lint on the unused Service import — it's part of the public type
# surface readers expect to be available alongside Host.
assert Service is not None
