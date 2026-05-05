"""velocitee-discover — network scanning, documentation, and diagnostics tool.

Used by VNE (Path B — join existing network), VSE (service inventory), and
VLE (drift detection baseline). One scan, one report; downstream code reads
the report rather than re-scanning.

Public API:

    from shared.discovery import run_discovery, render_markdown, DiscoveryReport

    report = run_discovery(cidrs=["192.168.1.0/24"])
    print(report.to_json())
    print(render_markdown(report))

Adapters live in `shared.discovery.adapters` — they take a DiscoveryReport
and a vendor-specific configuration block and produce the resources the
downstream engine needs (e.g., the OPNsense adapter wires its API client
into VNE state when an OPNsense gateway is detected).
"""

from .markdown import render as render_markdown
from .report import (
    Capability,
    DiscoveryReport,
    Host,
    LocalInterface,
    NetworkInfo,
    RouterInfo,
    ScanScope,
    Service,
    VLANObservation,
)
from .scan import run_discovery

__all__ = [
    "Capability",
    "DiscoveryReport",
    "Host",
    "LocalInterface",
    "NetworkInfo",
    "RouterInfo",
    "ScanScope",
    "Service",
    "VLANObservation",
    "render_markdown",
    "run_discovery",
]
