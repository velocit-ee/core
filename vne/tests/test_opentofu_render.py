"""OpenTofu renderer — the generated HCL must be syntactically valid.

Regression guard for the bug where variables.tf used comma-separated block
attributes (invalid HCL2), so `tofu init` could never have run. We parse
every generated .tf file with a real HCL2 parser.
"""
from __future__ import annotations

from pathlib import Path

import pytest

hcl2 = pytest.importorskip("hcl2", reason="python-hcl2 not installed (pip install '.[dev]')")

from shared.renderers.opentofu import OpenTofuRenderer


def _render(intent, tmp_path: Path) -> Path:
    r = OpenTofuRenderer(intent=intent, work_dir=tmp_path / "w", state_dir=tmp_path / "s")
    r._render_files()
    return r._tf_dir()


def test_all_generated_tf_is_valid_hcl(intent, tmp_path):
    tf_dir = _render(intent, tmp_path)
    tf_files = sorted(tf_dir.glob("*.tf"))
    assert tf_files, "renderer produced no .tf files"
    for f in tf_files:
        with open(f) as fh:
            hcl2.load(fh)  # raises on invalid HCL — that is the assertion


def test_tfvars_carries_iso_checksum_split(intent, tmp_path):
    tf_dir = _render(intent, tmp_path)
    tfvars = (tf_dir / "terraform.tfvars").read_text()
    assert 'iso_checksum = "' + "ab" * 32 + '"' in tfvars
    assert 'iso_checksum_algorithm = "sha256"' in tfvars


def test_execute_publishes_infra_manifest_path_output(intent, tmp_path, monkeypatch):
    """The Ansible phase locates the manifest via the `infra_manifest_path`
    output key — assert the OpenTofu renderer actually publishes it."""
    monkeypatch.setenv("PROXMOX_VE_ENDPOINT", "https://pve.example:8006")
    monkeypatch.setenv("PROXMOX_VE_API_TOKEN", "user@pam!t=s")

    r = OpenTofuRenderer(intent=intent, work_dir=tmp_path / "w", state_dir=tmp_path / "s")
    # Stub the subprocess-driven tofu calls; we only test output wiring.
    monkeypatch.setattr(r, "_render_files", lambda: None)
    monkeypatch.setattr(r, "_tofu", lambda *a, **k: None)
    monkeypatch.setattr(r, "_read_outputs", lambda: {
        "opnsense_ip": "10.10.10.1", "opnsense_vmid": 100, "proxmox_node": "pve",
    })
    result = r.execute()
    assert result.success
    assert "infra_manifest_path" in result.outputs
    assert result.outputs["infra_manifest_path"] == result.artifacts["infra_manifest"]
