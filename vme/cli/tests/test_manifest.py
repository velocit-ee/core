"""Tests for cli/manifest.py — schema validation and manifest build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import manifest as mf


_VALID_HARDWARE = {
    "cpu": "Intel Core i7-10700",
    "ram_mb": 16384,
    "disks": [{"device": "/dev/sda", "size_gb": 500}],
    "nics": [{"name": "eth0", "mac": "aa:bb:cc:dd:ee:ff"}],
}


def _valid_manifest(**overrides) -> dict:
    base = {
        "schema_version": "1.0",
        "vme_version": "0.1.0",
        "status": "success",
        "hostname": "node-01",
        "ip": "192.168.100.10",
        "os": "proxmox-ve",
        "os_version": "8.2-1",
        "hardware": _VALID_HARDWARE,
        "ssh_host_key_fingerprint": "SHA256:abc123",
        "deployed_at": "2026-04-05T14:32:00+00:00",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_passes_for_valid_manifest():
    errors = mf.validate(_valid_manifest())
    assert errors == []


def test_validate_catches_missing_required_field():
    m = _valid_manifest()
    del m["hostname"]
    errors = mf.validate(m)
    assert any("hostname" in e for e in errors)


def test_validate_catches_wrong_status():
    errors = mf.validate(_valid_manifest(status="pending"))
    assert errors


def test_validate_catches_invalid_os():
    errors = mf.validate(_valid_manifest(os="windows"))
    assert errors


def test_validate_catches_bad_mac():
    hw = dict(_VALID_HARDWARE)
    hw["nics"] = [{"name": "eth0", "mac": "not-a-mac"}]
    errors = mf.validate(_valid_manifest(hardware=hw))
    assert errors


def test_validate_failure_requires_reason():
    m = _valid_manifest(status="failure")
    # No failure_reason — should fail
    errors = mf.validate(m)
    assert any("failure_reason" in e for e in errors)


def test_validate_failure_with_reason_passes():
    m = _valid_manifest(status="failure", failure_reason="Target unreachable after PXE boot.")
    errors = mf.validate(m)
    assert errors == []


# ---------------------------------------------------------------------------
# build()
# ---------------------------------------------------------------------------


def test_build_returns_valid_manifest():
    m = mf.build(
        hostname="node-01",
        ip="192.168.100.10",
        os="proxmox-ve",
        os_version="8.2-1",
        hardware=_VALID_HARDWARE,
        ssh_host_key_fingerprint="SHA256:abc123",
    )
    assert m["schema_version"] == "1.0"
    assert m["status"] == "success"
    assert "deployed_at" in m


def test_build_sets_vme_version():
    m = mf.build(
        hostname="node-01",
        ip="192.168.100.10",
        os="ubuntu-server",
        os_version="24.04",
        hardware=_VALID_HARDWARE,
        ssh_host_key_fingerprint="SHA256:xyz",
    )
    assert m["vme_version"] == mf.VME_VERSION


def test_build_failure_manifest():
    m = mf.build(
        hostname="node-01",
        ip="192.168.100.10",
        os="proxmox-ve",
        os_version="8.2-1",
        hardware=_VALID_HARDWARE,
        ssh_host_key_fingerprint="SHA256:abc",
        status="failure",
        failure_reason="Timeout waiting for PXE response.",
    )
    assert m["status"] == "failure"
    assert "failure_reason" in m


# ---------------------------------------------------------------------------
# write() / load()
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path):
    m = mf.build(
        hostname="node-01",
        ip="10.0.0.1",
        os="ubuntu-server",
        os_version="24.04",
        hardware=_VALID_HARDWARE,
        ssh_host_key_fingerprint="SHA256:xyz",
    )
    path = mf.write(m, tmp_path / "out")
    assert path.exists()
    with open(path) as fh:
        loaded = json.load(fh)
    assert loaded["hostname"] == "node-01"


def test_load_valid_manifest(tmp_path):
    m = mf.build(
        hostname="node-02",
        ip="10.0.0.2",
        os="proxmox-ve",
        os_version="8.2-1",
        hardware=_VALID_HARDWARE,
        ssh_host_key_fingerprint="SHA256:abc",
    )
    path = mf.write(m, tmp_path / "out")
    loaded = mf.load(path)
    assert loaded["hostname"] == "node-02"


def test_load_invalid_manifest_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1.0", "hostname": "x"}))
    with pytest.raises(ValueError, match="schema validation"):
        mf.load(bad)
