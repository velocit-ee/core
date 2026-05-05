"""Discovery report — the structured output of a network scan.

Every discovery run produces one DiscoveryReport. It is the only artifact
downstream code (VNE join, VSE inventory, VLE drift) is allowed to consume.
Renderers and adapters never re-run a scan; they read the report.

The report is Pydantic-validated and JSON-serializable. Schema version is
bumped on incompatible changes; readers check the version field.
"""

from __future__ import annotations

import ipaddress
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


SCHEMA_VERSION = "1.0"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Service(_StrictModel):
    """One open TCP service on a host."""
    port: int = Field(ge=1, le=65535)
    proto: Literal["tcp", "udp"] = "tcp"
    name: str = Field(default="", description="Best-effort service name (http, ssh, ...).")
    banner: str = Field(default="", max_length=512)
    product: str = Field(default="", description="Product/vendor extracted from banner (nginx, OpenSSH, ...).")
    version: str = Field(default="")
    tls: bool = False
    tls_san: list[str] = Field(default_factory=list)
    http_title: str = Field(default="", max_length=256)
    http_server: str = Field(default="", max_length=256)
    # Populated by the optional nmap enrichment pass. Kept as separate fields
    # rather than overwriting the stdlib-derived ones so consumers can tell
    # which probe path produced which fact (and so a missing nmap binary
    # never silently degrades the older fields).
    nmap_product: str = Field(default="")
    nmap_version: str = Field(default="")
    nmap_extrainfo: str = Field(default="")
    nmap_cpe: list[str] = Field(default_factory=list)


class OSGuess(_StrictModel):
    """One OS family guess from nmap's TCP/IP stack fingerprinting."""
    name: str
    accuracy: int = Field(ge=0, le=100)
    family: str = Field(default="", description="osfamily attribute from nmap (Linux, Windows, ...).")
    vendor: str = Field(default="")


class Host(_StrictModel):
    """One host observed on the network."""
    ip: str
    mac: str = Field(default="")
    hostname: str = Field(default="", description="Reverse DNS or mDNS name if found.")
    vendor: str = Field(default="", description="MAC OUI vendor lookup, best-effort.")
    is_alive: bool = True
    discovered_via: list[str] = Field(
        default_factory=list,
        description="How we found this host: 'arp', 'mdns', 'ssdp', 'icmp', 'tcp-connect'.",
    )
    services: list[Service] = Field(default_factory=list)
    os_guesses: list[OSGuess] = Field(
        default_factory=list,
        description="Populated when nmap enrichment was used and produced OS fingerprints.",
    )
    role_hints: list[str] = Field(
        default_factory=list,
        description="Heuristic role tags: 'gateway', 'dns', 'dhcp', 'router', 'switch', 'opnsense', ...",
    )

    @field_validator("ip")
    @classmethod
    def _valid_ip(cls, v: str) -> str:
        ipaddress.ip_address(v)
        return v


class VLANObservation(_StrictModel):
    """A VLAN that we observed (via tagged subinterface, LLDP, or DHCP option 132)."""
    id: int = Field(ge=1, le=4094)
    cidr: str = ""
    gateway: str = ""
    dhcp_servers: list[str] = Field(default_factory=list)
    dns_servers: list[str] = Field(default_factory=list)
    domain: str = ""
    source: str = Field(
        default="",
        description="Where the VLAN was observed: 'iface', 'lldp', 'dhcp-option', ...",
    )


class RouterInfo(_StrictModel):
    """Identification of the network's primary gateway/router."""
    ip: str = ""
    mac: str = ""
    vendor: str = Field(default="unknown", description="Detected vendor/product slug, e.g. 'opnsense', 'mikrotik'.")
    product: str = Field(default="", description="Free-form product string from banner.")
    version: str = Field(default="")
    api_endpoint: str = Field(
        default="",
        description="Detected management API URL if present (used by adapters for richer integration).",
    )
    api_kind: str = Field(
        default="",
        description="API flavor: 'opnsense', 'pfsense', 'mikrotik-rest', 'unifi', 'edgeos', ''.",
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(
        default_factory=list,
        description="Human-readable bullet list explaining how we identified this router.",
    )


class LocalInterface(_StrictModel):
    """A local network interface on the host that ran the scan."""
    name: str
    mac: str = ""
    ipv4: list[str] = Field(default_factory=list)
    cidr: list[str] = Field(default_factory=list)
    is_up: bool = True
    mtu: int = Field(default=0, ge=0)
    is_vlan: bool = False
    vlan_id: int | None = Field(default=None, ge=1, le=4094)
    parent: str = ""


class NetworkInfo(_StrictModel):
    """Local-host view of the network, derived from system state (no probes)."""
    interfaces: list[LocalInterface] = Field(default_factory=list)
    default_gateway: str = ""
    default_gateway_iface: str = ""
    dns_resolvers: list[str] = Field(default_factory=list)
    search_domains: list[str] = Field(default_factory=list)
    dhcp_lease: dict[str, str] = Field(
        default_factory=dict,
        description="Parsed dhclient/networkd DHCP lease fields when available.",
    )


class ScanScope(_StrictModel):
    """Inputs that produced this report. Logged for reproducibility."""
    cidrs: list[str] = Field(default_factory=list)
    iface: str = ""
    ports: list[int] = Field(default_factory=list)
    passive_seconds: int = 0
    active: bool = True
    fingerprint: bool = True
    snmp_community: str = Field(default="", description="Empty = no SNMP.")
    use_nmap: str = Field(
        default="auto",
        description="'auto' (use if available), 'on' (require), 'off' (skip).",
    )
    notes: str = ""


class Capability(_StrictModel):
    """One capability the network exposes — written for VSE/VLE to gate features."""
    name: str
    available: bool
    reason: str = ""


class DiscoveryReport(_StrictModel):
    """Top-level discovery output. JSON-serialize this and you have the artifact."""
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    generated_by: str = Field(default="velocitee-discover")
    scan_scope: ScanScope = Field(default_factory=ScanScope)
    network: NetworkInfo = Field(default_factory=NetworkInfo)
    router: RouterInfo = Field(default_factory=RouterInfo)
    hosts: list[Host] = Field(default_factory=list)
    vlans: list[VLANObservation] = Field(default_factory=list)
    capabilities: list[Capability] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    duration_seconds: float = Field(default=0.0, ge=0.0)

    def to_json(self, *, indent: int = 2) -> str:
        return self.model_dump_json(indent=indent)

    @classmethod
    def from_json(cls, data: str) -> DiscoveryReport:
        return cls.model_validate_json(data)

    def host_by_ip(self, ip: str) -> Host | None:
        return next((h for h in self.hosts if h.ip == ip), None)

    def capability(self, name: str) -> Capability | None:
        return next((c for c in self.capabilities if c.name == name), None)
