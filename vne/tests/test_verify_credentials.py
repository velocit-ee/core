"""The verification gate must receive the credentials the deploy generated.

N-01: velocitee-native generates the OPNsense API key pair itself and kept the
secret half only in its state file. deploy.py read the secret from
OPNSENSE_API_SECRET, which that path never sets, so verify.py called the API
with auth=None, got 401, and the gate failed after an otherwise successful
deployment. The only workaround was to read the secret out of
vne/state/vne.state.json by hand.

The secret must reach the gate and must not reach the manifest.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from shared.renderers.velocitee_native import VelociteeNativeRenderer
from vne import deploy as vne_deploy
from vne.tests.test_deploy import vme_manifest_data


runner = CliRunner()

SECRET = "s3cr3t-generated-by-the-renderer"
KEY = "k3y-generated-by-the-renderer"


def _velocitee_yml(tmp_path: Path) -> Path:
    p = tmp_path / "velocitee.yml"
    p.write_text(
        "velocitee:\n"
        '  provisioner: "velocitee-native"\n'
        "vne:\n"
        '  wan_interface: "ens18"\n'
        '  lan_interface: "ens19"\n'
        '  opnsense_iso_url: "https://mirror.example.org/OPNsense-24.7-dvd-amd64.iso"\n'
        f'  opnsense_iso_checksum: "sha256:{"ab" * 32}"\n'
        "  vlans: []\n"
        "  dns:\n"
        '    upstream: ["1.1.1.1"]\n'
        '    domain: "lab.local"\n'
        "  opnsense_vm:\n"
        "    vmid: 100\n"
        '    storage_pool: "local-lvm"\n'
    )
    return p


def test_native_renderer_publishes_both_halves_of_the_key_pair(intent, tmp_path, monkeypatch):
    r = VelociteeNativeRenderer(
        intent=intent, work_dir=tmp_path / "w", state_dir=tmp_path / "s",
    )
    for step in [n for n in dir(r) if n.startswith("_step_")]:
        monkeypatch.setattr(r, step, lambda *a, **k: "stub", raising=True)
    monkeypatch.setattr(
        "shared.renderers.velocitee_native.ProxmoxClient",
        lambda **kw: type("P", (), {"first_node": lambda self: "pve"})(),
    )
    monkeypatch.setenv("PROXMOX_VE_ENDPOINT", "https://pve.example:8006")
    monkeypatch.setenv("PROXMOX_VE_API_TOKEN", "user@pam!t=s")

    result = r.execute()
    assert result.success, result.error
    r._state.shared_set("opnsense_api_key", KEY)
    r._state.shared_set("opnsense_api_secret_plain", SECRET)
    result = r.execute()

    assert result.outputs["opnsense_api_key"] == KEY
    assert result.outputs["opnsense_api_secret"] == SECRET, (
        "without the secret in outputs the verification gate cannot authenticate"
    )


def _run_deploy(tmp_path, monkeypatch, captured: dict, *, env_secret: str | None = None):
    for key in vne_deploy._required_env_for("velocitee-native"):
        monkeypatch.setenv(key, "placeholder")
    monkeypatch.delenv("OPNSENSE_API_SECRET", raising=False)
    if env_secret is not None:
        monkeypatch.setenv("OPNSENSE_API_SECRET", env_secret)

    manifest = tmp_path / "vme.json"
    manifest.write_text(json.dumps(vme_manifest_data()))

    class FakeResult:
        success = True
        failure_reason = None
        aggregated_outputs = {
            "opnsense_ip": "10.10.10.1",
            "opnsense_api_key": KEY,
            "opnsense_api_secret": SECRET,
        }

    monkeypatch.setattr(vne_deploy.Pipeline, "run", lambda self: FakeResult())

    def fake_run_all(**kw):
        captured.update(kw)
        return True, [type("C", (), {"name": "api_reachable", "passed": True})()]

    monkeypatch.setattr(vne_deploy.vne_verify, "run_all", fake_run_all)

    output = tmp_path / "o"
    result = runner.invoke(
        vne_deploy.app,
        ["deploy", "--config", str(_velocitee_yml(tmp_path)), "--manifest", str(manifest),
         "--work-dir", str(tmp_path / "w"), "--state-dir", str(tmp_path / "s"),
         "--output", str(output)],
    )
    return result, output


def test_deploy_hands_the_generated_secret_to_the_gate(tmp_path, monkeypatch):
    captured: dict = {}
    result, _ = _run_deploy(tmp_path, monkeypatch, captured)
    assert result.exit_code == 0, result.output
    assert captured["api_key"] == KEY
    assert captured["api_secret"] == SECRET


def test_an_operator_supplied_secret_still_wins(tmp_path, monkeypatch):
    captured: dict = {}
    result, _ = _run_deploy(tmp_path, monkeypatch, captured, env_secret="from-the-environment")
    assert result.exit_code == 0, result.output
    assert captured["api_secret"] == "from-the-environment"


def test_the_secret_never_reaches_the_written_manifest(tmp_path, monkeypatch):
    captured: dict = {}
    result, output = _run_deploy(tmp_path, monkeypatch, captured)
    assert result.exit_code == 0, result.output
    written = list(output.glob("*.json"))
    assert written, "a passing deploy must write a manifest"
    text = written[0].read_text()
    assert SECRET not in text, "the API secret must not be persisted into the handoff manifest"
    assert "opnsense_api_secret" not in text
