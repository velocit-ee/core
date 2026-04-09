"""Tests for cli/manifest.py — schema validation, build, write, load."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cli import manifest as mf


def _make_cfg(**overrides) -> dict:
    """Return a minimal vme-config-style dict for build_vme()."""
    target = {
        "hostname": "node-01",
        "ip": "192.168.100.10",
        "prefix": "24",
        "gateway": "192.168.100.1",
        "dns": "8.8.8.8",
        "disk": "/dev/sda",
        "os": "proxmox-ve",
        "ssh_public_key": "ssh-ed25519 AAAAC3Nz test@host",
    }
    target.update(overrides)
    return {"target": target}


def _timestamps() -> tuple[datetime, datetime]:
    t0 = datetime(2026, 4, 9, 10, 0, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 4, 9, 10, 12, 34, tzinfo=timezone.utc)
    return t0, t1


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_passes_for_valid_manifest():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac="aa:bb:cc:dd:ee:ff",
        started_at=t0,
        completed_at=t1,
    )
    errors = mf.validate(m)
    assert errors == []


def test_validate_catches_missing_required_field():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    del m["target"]
    errors = mf.validate(m)
    assert any("target" in e for e in errors)


def test_validate_catches_non_string_os():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    m["target"]["os"] = 42  # must be a string
    errors = mf.validate(m)
    assert errors


def test_validate_catches_missing_access():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    del m["access"]
    errors = mf.validate(m)
    assert any("access" in e for e in errors)


# ---------------------------------------------------------------------------
# build_vme()
# ---------------------------------------------------------------------------


def test_build_returns_valid_manifest():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac="aa:bb:cc:dd:ee:ff",
        started_at=t0,
        completed_at=t1,
    )
    assert m["schema_version"] == "1.0"
    assert m["target"]["hostname"] == "node-01"
    assert m["target"]["mac"] == "aa:bb:cc:dd:ee:ff"
    assert m["engines"]["vme"]["status"] == "success"


def test_build_sets_vme_version():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(os="ubuntu-server"),
        iso_path=Path("ubuntu-24.04.4-live-server-amd64.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    assert m["engines"]["vme"]["version"] == mf.VME_VERSION


def test_build_records_duration():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    assert m["engines"]["vme"]["duration_seconds"] == 754.0


def test_build_ubuntu_sets_correct_username():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(os="ubuntu-server"),
        iso_path=Path("ubuntu-24.04.4-live-server-amd64.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    assert m["access"]["username"] == "ubuntu"


def test_build_proxmox_sets_root_username():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(os="proxmox-ve"),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    assert m["access"]["username"] == "root"


def test_build_mac_omitted_when_none():
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    assert "mac" not in m["target"]


# ---------------------------------------------------------------------------
# write() / load()
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path):
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    path = mf.write(m, tmp_path / "out")
    assert path.exists()
    loaded = json.loads(path.read_text())
    assert loaded["target"]["hostname"] == "node-01"


def test_write_sanitizes_hostname_in_filename(tmp_path):
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(hostname="node/../etc/passwd"),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    path = mf.write(m, tmp_path / "out")
    assert "/" not in path.name
    assert path.exists()


def test_load_valid_manifest(tmp_path):
    t0, t1 = _timestamps()
    m = mf.build_vme(
        cfg=_make_cfg(hostname="node-02"),
        iso_path=Path("proxmox-ve_8.2-1.iso"),
        mac=None,
        started_at=t0,
        completed_at=t1,
    )
    path = mf.write(m, tmp_path / "out")
    loaded = mf.load(path)
    assert loaded["target"]["hostname"] == "node-02"


def test_load_invalid_manifest_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "1.0", "hostname": "x"}))
    with pytest.raises(ValueError, match="failed validation"):
        mf.load(bad)
