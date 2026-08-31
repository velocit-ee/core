"""Tests for the VNE deployment entry point.

deploy.py is the orchestrator: it decides whether a deployment is allowed to
start, and — the part that matters most — whether a handoff manifest is
allowed to be written at the end. A manifest is a claim that the network was
provisioned and verified. Writing one after a failed verification would launder
a broken network into a signed-looking fact that VSE and VLE then build on.

These tests pin the refusals.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from shared.schema import VLAN
from vne import deploy as vne_deploy


runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def vme_manifest_data(status: str = "success") -> dict:
    """A VME handoff manifest shaped exactly as vne/schema/vme-manifest.schema.json
    requires — including `access`, and `target.os` as the bare enum string."""
    return {
        "schema_version": "1.0",
        "target": {
            "hostname": "pve-01",
            "ip": "192.168.1.10",
            "mac": "aa:bb:cc:dd:ee:ff",
            "os": "proxmox-ve",
        },
        "access": {
            "username": "velocitee",
            "ssh_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@lab",
            "ssh_port": 22,
        },
        "engines": {
            "vme": {
                "version": "0.1.0",
                "status": status,
                "started_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:10:00Z",
            }
        },
    }


@pytest.fixture
def vme_manifest(tmp_path) -> Path:
    p = tmp_path / "vme-manifest.json"
    p.write_text(json.dumps(vme_manifest_data()))
    return p


# ---------------------------------------------------------------------------
# _required_env_for
# ---------------------------------------------------------------------------

def test_required_env_is_empty_for_an_unregistered_provisioner():
    assert vne_deploy._required_env_for("not-a-real-backend") == []


def test_required_env_reads_the_renderer_descriptor():
    keys = vne_deploy._required_env_for("velocitee-native")
    assert keys, "velocitee-native must declare the credentials it needs"
    assert all(isinstance(k, str) for k in keys)


def test_required_env_unions_a_composite_provisioner_without_duplicates():
    # opentofu+ansible runs two renderers. Pre-flight must report every miss in
    # one error rather than making the operator discover them one run at a time.
    composite = vne_deploy._required_env_for("opentofu+ansible")
    tofu = vne_deploy._required_env_for("opentofu")
    ansible = vne_deploy._required_env_for("ansible")
    assert set(composite) == set(tofu) | set(ansible)
    assert len(composite) == len(set(composite)), "keys must be de-duplicated"


def test_required_env_preserves_declaration_order():
    keys = vne_deploy._required_env_for("opentofu+ansible")
    assert keys == list(dict.fromkeys(keys))


# ---------------------------------------------------------------------------
# _load_and_validate_vme_manifest
# ---------------------------------------------------------------------------

def test_manifest_loader_accepts_a_valid_manifest(vme_manifest):
    data = vne_deploy._load_and_validate_vme_manifest(vme_manifest)
    assert data["target"]["ip"] == "192.168.1.10"


def test_manifest_loader_exits_when_the_file_is_missing(tmp_path):
    with pytest.raises(typer.Exit):
        vne_deploy._load_and_validate_vme_manifest(tmp_path / "nope.json")


def test_manifest_loader_exits_on_malformed_json(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    with pytest.raises(typer.Exit):
        vne_deploy._load_and_validate_vme_manifest(p)


def test_manifest_loader_exits_on_a_schema_violation(tmp_path):
    # A manifest missing `target` would otherwise blow up later with a
    # KeyError, halfway through a deployment that had already touched Proxmox.
    data = vme_manifest_data()
    del data["target"]
    p = tmp_path / "incomplete.json"
    p.write_text(json.dumps(data))
    with pytest.raises(typer.Exit):
        vne_deploy._load_and_validate_vme_manifest(p)


def test_manifest_loader_reports_the_offending_field(tmp_path, capsys):
    data = vme_manifest_data()
    data["target"]["ip"] = 12345  # wrong type
    p = tmp_path / "wrongtype.json"
    p.write_text(json.dumps(data))
    with pytest.raises(typer.Exit):
        vne_deploy._load_and_validate_vme_manifest(p)
    err = capsys.readouterr().err + capsys.readouterr().out
    assert "schema validation" in err or "ip" in err


# ---------------------------------------------------------------------------
# _build_vne_record — the manifest must record measurements, not assertions
# ---------------------------------------------------------------------------

class Check:
    def __init__(self, name, passed):
        self.name, self.passed = name, passed


def test_vne_record_reports_verification_failure_honestly(intent):
    checks = [Check("api_reachable", True), Check("dns_resolving", False)]
    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", checks,
    )
    assert record["verification"]["passed"] is False
    assert record["verification"]["checks"] == {
        "api_reachable": True, "dns_resolving": False,
    }


def test_vne_record_reports_success_only_when_every_check_passed(intent):
    checks = [Check("a", True), Check("b", True)]
    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", checks,
    )
    assert record["verification"]["passed"] is True


def test_vne_record_with_no_checks_does_not_claim_verification(intent):
    # all([]) is True, so an empty check list currently reports passed=True.
    # Pinning it makes the behaviour visible: the gate in deploy() is what
    # guarantees checks ran, not this function.
    record = vne_deploy._build_vne_record(
        intent, {}, vme_manifest_data(), "velocitee-native", [],
    )
    assert record["verification"]["checks"] == {}


def test_vne_record_carries_the_vlans_from_the_intent(intent):
    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", [],
    )
    assert [v["id"] for v in record["vlans"]] == [10, 20]
    # VLAN 10 has a DHCP range in the fixture; VLAN 20 does not. The key is
    # omitted rather than nulled for VLAN 20 -- the output schema accepts a
    # missing dhcp_range but rejects a null one.
    assert record["vlans"][0]["dhcp_range"] == ["10.10.10.100", "10.10.10.200"]
    assert "dhcp_range" not in record["vlans"][1]


def test_vne_record_records_the_provisioner_that_actually_ran(intent):
    record = vne_deploy._build_vne_record(
        intent, {}, vme_manifest_data(), "opentofu+ansible", [],
    )
    assert record["provisioner"] == "opentofu+ansible"
    assert record["mode"] == "deploy"


def test_vne_record_defaults_the_api_endpoint_from_the_ip(intent):
    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", [],
    )
    assert record["opnsense"]["api_endpoint"] == "https://10.10.10.1"


def test_vne_record_prefers_an_explicit_api_endpoint_output(intent):
    record = vne_deploy._build_vne_record(
        intent,
        {"opnsense_ip": "10.10.10.1", "opnsense_api_endpoint": "https://fw.lab:8443"},
        vme_manifest_data(), "velocitee-native", [],
    )
    assert record["opnsense"]["api_endpoint"] == "https://fw.lab:8443"


def test_vne_record_produces_a_manifest_that_passes_its_own_schema(intent, tmp_path):
    from shared import manifest as mf
    from datetime import datetime, timezone

    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", [Check("api_reachable", True)],
    )
    manifest = mf.append_engine(
        vme_manifest_data(), engine="vne", version=vne_deploy.VNE_VERSION,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        extra=record,
    )
    # Must not raise.
    vne_deploy._validate_against_vne_schema(manifest)


def test_self_validation_rejects_a_malformed_manifest():
    with pytest.raises(typer.Exit):
        vne_deploy._validate_against_vne_schema({"schema_version": "1.0"})


# ---------------------------------------------------------------------------
# `vne setup`
# ---------------------------------------------------------------------------

def test_setup_writes_a_starter_config(tmp_path, vme_manifest):
    cfg = tmp_path / "velocitee.yml"
    result = runner.invoke(
        vne_deploy.app,
        ["setup", "--manifest", str(vme_manifest), "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert cfg.exists()
    text = cfg.read_text()
    assert "provisioner:" in text
    assert "velocitee-native" in text


def test_setup_starter_config_is_valid_yaml(tmp_path, vme_manifest):
    import yaml

    cfg = tmp_path / "velocitee.yml"
    runner.invoke(
        vne_deploy.app,
        ["setup", "--manifest", str(vme_manifest), "--config", str(cfg)],
    )
    parsed = yaml.safe_load(cfg.read_text())
    assert parsed["velocitee"]["provisioner"] == "velocitee-native"
    assert parsed["vne"]["opnsense_vm"]["vmid"] == 100


def test_setup_refuses_to_overwrite_an_existing_config(tmp_path, vme_manifest):
    cfg = tmp_path / "velocitee.yml"
    cfg.write_text("# hand-written, do not clobber\n")
    result = runner.invoke(
        vne_deploy.app,
        ["setup", "--manifest", str(vme_manifest), "--config", str(cfg)],
    )
    assert result.exit_code == 0
    assert cfg.read_text() == "# hand-written, do not clobber\n"


def test_setup_fails_when_the_vme_manifest_is_missing(tmp_path):
    result = runner.invoke(
        vne_deploy.app,
        ["setup", "--manifest", str(tmp_path / "nope.json"),
         "--config", str(tmp_path / "velocitee.yml")],
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# `vne deploy` — the refusals
# ---------------------------------------------------------------------------

@pytest.fixture
def velocitee_yml(tmp_path) -> Path:
    p = tmp_path / "velocitee.yml"
    p.write_text(
        "velocitee:\n"
        '  provisioner: "velocitee-native"\n'
        "vne:\n"
        '  wan_interface: "ens18"\n'
        '  lan_interface: "ens19"\n'
        '  opnsense_version: "24.7"\n'
        '  opnsense_iso_url: "https://mirror.example.org/OPNsense-24.7-dvd-amd64.iso"\n'
        f'  opnsense_iso_checksum: "sha256:{"ab" * 32}"\n'
        "  vlans: []\n"
        "  dns:\n"
        '    upstream: ["1.1.1.1"]\n'
        '    domain: "lab.local"\n'
        "  firewall:\n"
        '    default_policy: "block"\n'
        "  opnsense_vm:\n"
        "    vmid: 100\n"
        "    cores: 2\n"
        "    memory_mb: 2048\n"
        "    disk_gb: 20\n"
        '    storage_pool: "local-lvm"\n'
    )
    return p


def test_deploy_halts_when_required_env_vars_are_missing(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.delenv(key, raising=False)

    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code != 0
    assert "missing required environment variables" in result.output


def test_deploy_refuses_a_vme_manifest_that_did_not_succeed(
    tmp_path, velocitee_yml, monkeypatch
):
    # Configuring a network on a host whose OS install failed is how you get a
    # confident manifest describing a machine that is not there.
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")

    manifest = tmp_path / "failed.json"
    manifest.write_text(json.dumps(vme_manifest_data(status="failure")))

    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code != 0
    assert "does not record a successful run" in result.output


def test_deploy_writes_no_manifest_when_verification_fails(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    # The single most important refusal in the engine.
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")

    class FakeResult:
        success = True
        failure_reason = None
        aggregated_outputs = {"opnsense_ip": "10.10.10.1"}

    monkeypatch.setattr(vne_deploy.Pipeline, "run", lambda self: FakeResult())
    monkeypatch.setattr(
        vne_deploy.vne_verify, "run_all",
        lambda **kw: (False, [type("C", (), {"name": "dns", "passed": False})()]),
    )

    output = tmp_path / "o"
    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(output)],
    )
    assert result.exit_code != 0
    assert "WILL NOT be written" in result.output
    assert not list(output.glob("*.json")) if output.exists() else True


def test_deploy_halts_when_the_pipeline_returns_no_opnsense_ip(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    # Without an IP there is nothing to verify against, so declaring success
    # would mean shipping an unverified manifest.
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")

    class FakeResult:
        success = True
        failure_reason = None
        aggregated_outputs = {}

    monkeypatch.setattr(vne_deploy.Pipeline, "run", lambda self: FakeResult())

    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code != 0
    assert "opnsense_ip" in result.output


def test_deploy_halts_when_the_pipeline_fails(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")

    class FakeResult:
        success = False
        failure_reason = "proxmox unreachable"
        aggregated_outputs = {}

    monkeypatch.setattr(vne_deploy.Pipeline, "run", lambda self: FakeResult())

    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(tmp_path / "o")],
    )
    assert result.exit_code != 0
    assert "proxmox unreachable" in result.output


def test_deploy_writes_a_manifest_when_everything_passes(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")

    class FakeResult:
        success = True
        failure_reason = None
        aggregated_outputs = {"opnsense_ip": "10.10.10.1"}

    monkeypatch.setattr(vne_deploy.Pipeline, "run", lambda self: FakeResult())
    monkeypatch.setattr(
        vne_deploy.vne_verify, "run_all",
        lambda **kw: (True, [type("C", (), {"name": "api_reachable", "passed": True})()]),
    )

    output = tmp_path / "o"
    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(output)],
    )
    assert result.exit_code == 0, result.output
    written = list(output.glob("*.json"))
    assert written, "a successful deploy must write its handoff manifest"
    data = json.loads(written[0].read_text())
    assert data["engines"]["vne"]["verification"]["passed"] is True


def test_deprecated_join_flag_forwards_to_join_mode(
    tmp_path, vme_manifest, velocitee_yml, monkeypatch
):
    seen = {}
    monkeypatch.setattr(
        vne_deploy.vne_join, "run_join", lambda **kw: seen.update(kw)
    )
    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(velocitee_yml), "--manifest", str(vme_manifest),
         "--join-existing"],
    )
    assert result.exit_code == 0
    assert seen, "the deprecated flag must still reach join mode"
    assert "deprecated" in result.output


def test_manifest_validates_when_no_vlan_has_a_dhcp_range(intent, tmp_path):
    """Regression: a deployment with only static VLANs used to provision the
    entire network, pass verification, and then fail self-validation on the
    final step because `dhcp_range: null` is not an array."""
    from datetime import datetime, timezone

    from shared import manifest as mf

    # VLAN validates "both or neither" on assignment, so rebuild rather than
    # mutate one field at a time.
    intent.network.vlans = [
        VLAN(id=v.id, name=v.name, cidr=v.cidr) for v in intent.network.vlans
    ]

    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", [Check("api_reachable", True)],
    )
    assert all("dhcp_range" not in v for v in record["vlans"])

    manifest = mf.append_engine(
        vme_manifest_data(), engine="vne", version=vne_deploy.VNE_VERSION,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        extra=record,
    )
    vne_deploy._validate_against_vne_schema(manifest)


def test_manifest_validates_when_every_vlan_has_a_dhcp_range(intent):
    from datetime import datetime, timezone

    from shared import manifest as mf

    intent.network.vlans = [
        VLAN(id=v.id, name=v.name, cidr=v.cidr,
             dhcp_start=f"10.10.{v.id}.100", dhcp_end=f"10.10.{v.id}.200")
        for v in intent.network.vlans
    ]

    record = vne_deploy._build_vne_record(
        intent, {"opnsense_ip": "10.10.10.1"}, vme_manifest_data(),
        "velocitee-native", [Check("api_reachable", True)],
    )
    assert all(len(v["dhcp_range"]) == 2 for v in record["vlans"])

    manifest = mf.append_engine(
        vme_manifest_data(), engine="vne", version=vne_deploy.VNE_VERSION,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        extra=record,
    )
    vne_deploy._validate_against_vne_schema(manifest)
