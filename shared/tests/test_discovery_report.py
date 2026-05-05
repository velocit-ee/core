"""Tests for shared.discovery report models, Markdown renderer, and adapters.

These tests deliberately avoid touching the network — the orchestrator and
network introspection modules are tested only at the boundary they expose
(e.g. the report shape they produce when given fixtures), not by running
real probes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.discovery import (
    DiscoveryReport,
    Host,
    LocalInterface,
    NetworkInfo,
    RouterInfo,
    Service,
    VLANObservation,
    render_markdown,
)
from shared.discovery.adapters import lookup, OPNsenseAdapter, UnmanagedAdapter
from shared.discovery.adapters.base import autopick
from shared.discovery.routers import identify


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def opnsense_report() -> DiscoveryReport:
    gateway = Host(
        ip="192.168.1.1",
        mac="00:11:22:33:44:55",
        discovered_via=["arp", "tcp-connect"],
        services=[
            Service(
                port=443, name="https", tls=True,
                http_title="OPNsense",
                http_server="OPNsense",
                tls_san=["opnsense.lab.local"],
            ),
            Service(port=22, name="ssh", banner="SSH-2.0-OpenSSH_9.3p1"),
        ],
    )
    client = Host(
        ip="192.168.1.50",
        discovered_via=["arp"],
        services=[Service(port=22, name="ssh")],
    )
    return DiscoveryReport(
        network=NetworkInfo(
            default_gateway="192.168.1.1",
            default_gateway_iface="eth0",
            dns_resolvers=["192.168.1.1"],
            search_domains=["lab.local"],
            interfaces=[
                LocalInterface(name="eth0", mac="aa:bb:cc:dd:ee:ff",
                               ipv4=["192.168.1.50"], cidr=["192.168.1.0/24"]),
            ],
        ),
        router=RouterInfo(),
        hosts=[gateway, client],
        vlans=[VLANObservation(id=10, cidr="10.10.10.0/24", source="iface:eth0.10")],
    )


@pytest.fixture
def unknown_report() -> DiscoveryReport:
    """A network with a gateway we can't identify."""
    gateway = Host(ip="10.0.0.1", services=[Service(port=80, name="http")])
    return DiscoveryReport(
        network=NetworkInfo(default_gateway="10.0.0.1", default_gateway_iface="eth0"),
        router=RouterInfo(),
        hosts=[gateway],
    )


# ---------------------------------------------------------------------------
# DiscoveryReport — JSON round-trip
# ---------------------------------------------------------------------------

def test_report_json_roundtrip(opnsense_report: DiscoveryReport) -> None:
    payload = opnsense_report.to_json()
    rebuilt = DiscoveryReport.from_json(payload)
    assert rebuilt.model_dump() == opnsense_report.model_dump()


def test_report_helpers(opnsense_report: DiscoveryReport) -> None:
    assert opnsense_report.host_by_ip("192.168.1.1") is not None
    assert opnsense_report.host_by_ip("9.9.9.9") is None


# ---------------------------------------------------------------------------
# Router identification
# ---------------------------------------------------------------------------

def test_identify_opnsense(opnsense_report: DiscoveryReport) -> None:
    gw = opnsense_report.host_by_ip("192.168.1.1")
    assert gw is not None
    info = identify(gw, opnsense_report.hosts)
    assert info.vendor == "opnsense"
    assert info.api_kind == "opnsense"
    assert info.confidence > 0.5
    assert "OPNsense" in " ".join(info.evidence)


def test_identify_no_gateway() -> None:
    info = identify(None, [])
    assert info.vendor == "unknown"
    assert info.confidence == 0.0


def test_identify_unknown_falls_to_generic(unknown_report: DiscoveryReport) -> None:
    gw = unknown_report.host_by_ip("10.0.0.1")
    info = identify(gw, unknown_report.hosts)
    # generic-router is the fallback signature
    assert info.vendor == "generic-router"
    # no api_kind for the generic fallback — adapters should pick unmanaged
    assert info.api_kind == ""


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def test_markdown_renders_known_sections(opnsense_report: DiscoveryReport) -> None:
    # Populate the router slot — render is happy without it but the test is
    # more useful when the gateway block is filled in.
    opnsense_report.router = identify(
        opnsense_report.host_by_ip("192.168.1.1"),
        opnsense_report.hosts,
    )
    md = render_markdown(opnsense_report)
    assert "# Network Discovery Report" in md
    assert "## Local host" in md
    assert "## Gateway / router" in md
    assert "## Hosts" in md
    assert "192.168.1.1" in md
    assert "OPNsense" in md  # vendor product surfaced


def test_markdown_handles_empty_report() -> None:
    """An entirely empty report still renders cleanly."""
    rep = DiscoveryReport()
    md = render_markdown(rep)
    assert md.startswith("# Network Discovery Report")
    assert "Hosts" in md
    assert "_No hosts observed._" in md


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------

def test_unmanaged_adapter_always_matches(opnsense_report: DiscoveryReport) -> None:
    assert UnmanagedAdapter.matches(opnsense_report)


def test_opnsense_adapter_matches_only_opnsense(opnsense_report: DiscoveryReport) -> None:
    opnsense_report.router = identify(
        opnsense_report.host_by_ip("192.168.1.1"),
        opnsense_report.hosts,
    )
    assert OPNsenseAdapter.matches(opnsense_report)


def test_opnsense_adapter_does_not_match_unknown(unknown_report: DiscoveryReport) -> None:
    unknown_report.router = identify(
        unknown_report.host_by_ip("10.0.0.1"), unknown_report.hosts,
    )
    assert not OPNsenseAdapter.matches(unknown_report)


def test_autopick_falls_back_to_unmanaged(unknown_report: DiscoveryReport) -> None:
    unknown_report.router = identify(
        unknown_report.host_by_ip("10.0.0.1"), unknown_report.hosts,
    )
    assert autopick(unknown_report) == "unmanaged"


def test_autopick_picks_opnsense(opnsense_report: DiscoveryReport) -> None:
    opnsense_report.router = identify(
        opnsense_report.host_by_ip("192.168.1.1"),
        opnsense_report.hosts,
    )
    assert autopick(opnsense_report) == "opnsense"


def test_unmanaged_execute_writes_fragment(tmp_path: Path, opnsense_report: DiscoveryReport) -> None:
    adapter = UnmanagedAdapter(
        report=opnsense_report,
        work_dir=tmp_path / "work",
        state_dir=tmp_path / "state",
    )
    result = adapter.execute()
    assert result.success
    assert result.adapter == "unmanaged"
    fragment = result.manifest_fragment
    assert fragment["mode"] == "join"
    assert fragment["adapter"] == "unmanaged"
    assert fragment["gateway"]["ip"] == "192.168.1.1"
    assert fragment["gateway"]["managed"] is False
    assert "discovery_report" in fragment


def test_opnsense_adapter_without_creds_degrades(
    tmp_path: Path, opnsense_report: DiscoveryReport, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPNSENSE_API_KEY", raising=False)
    monkeypatch.delenv("OPNSENSE_API_SECRET", raising=False)
    opnsense_report.router = identify(
        opnsense_report.host_by_ip("192.168.1.1"),
        opnsense_report.hosts,
    )
    adapter = OPNsenseAdapter(
        report=opnsense_report,
        work_dir=tmp_path / "work",
        state_dir=tmp_path / "state",
    )
    result = adapter.execute()
    assert result.success
    fragment = result.manifest_fragment
    assert fragment["gateway"]["managed"] is False
    # No live block (empty dict is also acceptable; assert one or the other).
    assert fragment.get("live", {}) == {}
    caps_by_name = {c.name: c for c in result.capabilities}
    assert caps_by_name["firewall_managed"].available is False
    assert "OPNSENSE_API_KEY" in caps_by_name["firewall_managed"].reason


def test_stub_adapter_lookup_returns_class() -> None:
    cls = lookup("pfsense")
    assert cls is not None
    # Stubs raise NotImplementedError at execute()
    rep = DiscoveryReport()
    inst = cls(report=rep, work_dir=Path("/tmp"), state_dir=Path("/tmp"))
    with pytest.raises(NotImplementedError):
        inst.execute()
