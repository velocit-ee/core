"""Tests for VNE Path B — joining an existing network.

Join is the read-only path: it observes a network the operator already has and
writes a manifest describing it. Two properties matter most. First, it must
stay read-only — a join that mutates someone's production router is the worst
failure this engine can have. Second, the manifest it synthesises for a
standalone join (no VME run) has to validate, or the whole path dead-ends at
the last step.
"""

from __future__ import annotations

import json

import pytest
import typer

from shared.discovery import DiscoveryReport
from shared.discovery.adapters.base import AdapterResult
from shared.discovery.adapters.unmanaged import UnmanagedAdapter
from vne import join as vne_join


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def report() -> DiscoveryReport:
    """A discovery report shaped like a small flat network behind one gateway."""
    return DiscoveryReport.model_validate({
        "network": {
            "default_gateway": "192.168.1.1",
            "default_gateway_iface": "eth0",
            "dns_resolvers": ["192.168.1.1"],
            "search_domains": ["lan"],
            "interfaces": [
                {"name": "lo", "ipv4": ["127.0.0.1"]},
                {"name": "eth0", "ipv4": ["192.168.1.50"]},
            ],
        },
        "router": {
            "ip": "192.168.1.1",
            "vendor": "OPNsense",
            "product": "OPNsense 24.7",
            "confidence": 0.9,
            "api_endpoint": "https://192.168.1.1/api",
            "api_kind": "opnsense",
        },
        "hosts": [],
        "vlans": [],
        "warnings": [],
    })


class CountingUnmanagedAdapter(UnmanagedAdapter):
    """The real unmanaged adapter, instrumented so the tests can count calls.

    Using the real adapter rather than a stub means the manifest fragment under
    test is the one that actually ships — a hand-written fragment would have
    happily passed tests while the real one failed schema validation.
    """
    calls: list[str] = []

    def execute(self):
        CountingUnmanagedAdapter.calls.append("execute")
        return super().execute()


@pytest.fixture
def joined(monkeypatch, report, tmp_path):
    """Wire run_join up against a scripted discovery and the real adapter."""
    CountingUnmanagedAdapter.calls = []
    monkeypatch.setattr(vne_join, "run_discovery", lambda **kw: report)
    monkeypatch.setattr(vne_join, "autopick", lambda r: "unmanaged")
    monkeypatch.setattr(vne_join, "lookup_adapter", lambda slug: CountingUnmanagedAdapter)
    return {
        "work_dir": tmp_path / "work",
        "output_dir": tmp_path / "out",
    }


def run(joined, **overrides):
    kwargs = dict(
        cidrs=[], iface="", passive_seconds=0, snmp_community="", use_nmap="off",
        adapter_slug="", work_dir=joined["work_dir"], output_dir=joined["output_dir"],
        vme_manifest=None, yes=True, vne_version="0.1.0",
    )
    kwargs.update(overrides)
    return vne_join.run_join(**kwargs)


# ---------------------------------------------------------------------------
# End-to-end join
# ---------------------------------------------------------------------------

def test_join_writes_a_manifest_and_both_reports(joined):
    out_path = run(joined)
    out = joined["output_dir"]
    assert out_path.exists()
    assert (out / "discovery-report.json").exists()
    assert (out / "discovery-report.md").exists()


def test_synthesised_manifest_validates_against_the_vne_schema(joined):
    # The standalone join path builds its own base manifest. If that base does
    # not validate, every join without a prior VME run fails at the last step
    # after all the work is done.
    out_path = run(joined)
    data = json.loads(out_path.read_text())
    vne_join._validate_against_vne_schema(data)


def test_join_manifest_records_join_mode(joined):
    data = json.loads(run(joined).read_text())
    assert data["engines"]["vne"]["mode"] == "join"


def test_join_marks_vme_as_skipped_when_run_standalone(joined):
    # Downstream engines must be able to tell "VME provisioned this" from
    # "someone pointed us at a network that already existed".
    data = json.loads(run(joined).read_text())
    assert data["engines"]["vme"]["version"] == "skipped"
    assert "VME was not run" in data["engines"]["vme"]["note"]


def test_join_points_the_fragment_at_the_report_on_disk(joined):
    data = json.loads(run(joined).read_text())
    assert data["engines"]["vne"]["joined_network"]["discovery_report"] == "discovery-report.json"


def test_join_runs_the_adapter_exactly_once(joined):
    run(joined)
    assert CountingUnmanagedAdapter.calls == ["execute"]


def test_explicit_adapter_slug_overrides_autopick(joined, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        vne_join, "lookup_adapter",
        lambda slug: (seen.setdefault("slug", slug), CountingUnmanagedAdapter)[1],
    )
    monkeypatch.setattr(vne_join, "autopick", lambda r: "opnsense")
    run(joined, adapter_slug="unmanaged")
    assert seen["slug"] == "unmanaged"


def test_autopick_is_used_when_no_adapter_is_named(joined, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        vne_join, "lookup_adapter",
        lambda slug: (seen.setdefault("slug", slug), CountingUnmanagedAdapter)[1],
    )
    monkeypatch.setattr(vne_join, "autopick", lambda r: "opnsense")
    run(joined)
    assert seen["slug"] == "opnsense"


def test_join_halts_on_an_unknown_adapter(joined, monkeypatch):
    def missing(slug):
        raise KeyError(f"unknown adapter '{slug}'")

    monkeypatch.setattr(vne_join, "lookup_adapter", missing)
    with pytest.raises(typer.Exit):
        run(joined, adapter_slug="nonexistent")


def test_join_halts_when_the_adapter_fails(joined, monkeypatch):
    class FailingAdapter(CountingUnmanagedAdapter):
        def execute(self):
            return AdapterResult(
                success=False, adapter="opnsense",
                manifest_fragment={}, capabilities=[], error="API credentials rejected",
            )

    monkeypatch.setattr(vne_join, "lookup_adapter", lambda slug: FailingAdapter)
    with pytest.raises(typer.Exit):
        run(joined)


def test_join_halts_when_the_adapter_is_not_implemented(joined, monkeypatch):
    class StubAdapter(CountingUnmanagedAdapter):
        def execute(self):
            raise NotImplementedError("the 'mikrotik' adapter is not implemented")

    monkeypatch.setattr(vne_join, "lookup_adapter", lambda slug: StubAdapter)
    with pytest.raises(typer.Exit):
        run(joined)


def test_join_aborts_when_the_operator_declines(joined, monkeypatch):
    monkeypatch.setattr(vne_join.typer, "confirm", lambda *a, **k: False)
    with pytest.raises(typer.Exit):
        run(joined, yes=False)


def test_join_writes_nothing_when_the_operator_declines(joined, monkeypatch):
    monkeypatch.setattr(vne_join.typer, "confirm", lambda *a, **k: False)
    with pytest.raises(typer.Exit):
        run(joined, yes=False)
    assert not joined["output_dir"].exists() or not list(joined["output_dir"].glob("*.json"))


def test_yes_flag_skips_the_confirmation_prompt(joined, monkeypatch):
    def should_not_be_called(*a, **k):
        raise AssertionError("--yes must not prompt")

    monkeypatch.setattr(vne_join.typer, "confirm", should_not_be_called)
    run(joined, yes=True)


# ---------------------------------------------------------------------------
# _load_or_synthesize_vme
# ---------------------------------------------------------------------------

def test_synthesised_base_prefers_the_gateway_interface_address(report):
    base = vne_join._load_or_synthesize_vme(None, report)
    assert base["target"]["ip"] == "192.168.1.50"
    assert base["target"]["gateway"] == "192.168.1.1"


def test_synthesised_base_skips_loopback_when_the_gateway_iface_is_unknown(report):
    report.network.default_gateway_iface = "does-not-exist"
    base = vne_join._load_or_synthesize_vme(None, report)
    assert base["target"]["ip"] == "192.168.1.50", "must not fall back to 127.0.0.1"


def test_synthesised_base_tolerates_a_host_with_no_addresses(report):
    report.network.interfaces = []
    base = vne_join._load_or_synthesize_vme(None, report)
    assert base["target"]["ip"] == ""


def test_synthesised_base_tolerates_no_dns_resolvers(report):
    report.network.dns_resolvers = []
    base = vne_join._load_or_synthesize_vme(None, report)
    assert base["target"]["dns"] == ""


def test_an_existing_vme_manifest_is_used_verbatim(tmp_path, report):
    src = tmp_path / "vme.json"
    src.write_text(json.dumps({"schema_version": "1.0", "marker": "from-vme"}))
    assert vne_join._load_or_synthesize_vme(src, report)["marker"] == "from-vme"


def test_a_missing_vme_manifest_path_is_fatal(tmp_path, report):
    with pytest.raises(typer.Exit):
        vne_join._load_or_synthesize_vme(tmp_path / "nope.json", report)


def test_a_malformed_vme_manifest_is_fatal(tmp_path, report):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(typer.Exit):
        vne_join._load_or_synthesize_vme(bad, report)


# ---------------------------------------------------------------------------
# Read-only guarantee
# ---------------------------------------------------------------------------

def test_join_writes_only_inside_the_output_and_work_directories(joined, tmp_path):
    # The read-only promise is about the router, which the fake adapter stands
    # in for. This pins the filesystem half: nothing lands outside the two
    # directories the operator named.
    run(joined)
    written = {p for p in tmp_path.rglob("*") if p.is_file()}
    allowed = {joined["output_dir"], joined["work_dir"]}
    for path in written:
        assert any(str(path).startswith(str(a)) for a in allowed), path
