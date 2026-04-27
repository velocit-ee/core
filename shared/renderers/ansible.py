"""Ansible renderer — reads infra_manifest.json, configures running OPNsense.

Phase 2 of the OpenTofu+Ansible backend. Reads the OpenTofu phase's
infra_manifest.json (the OPNsense IP, the VLAN/DHCP/DNS intent), generates an
inventory file, then runs the bundled playbook.

All generated roles are idempotent. We enforce that structurally — every task
has either:
  - a 'changed_when' clause that pins the change condition, or
  - a module that's idempotent by design (the ansibleguy.opnsense modules are).

The renderer never edits roles by hand at runtime; it only generates
inventory + group_vars and invokes the static playbook tree we ship.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import yaml
from pathlib import Path
from typing import Any

from ..renderer import Renderer
from ..renderer_registry import register
from ..schema import ProvisioningResult

log = logging.getLogger("velocitee.renderer.ansible")

# Pinned. Bumped here, nowhere else.
ANSIBLE_REQUIRED_MIN = "2.15"
OPNSENSE_COLLECTION  = "ansibleguy.opnsense"
OPNSENSE_COLLECTION_VERSION = "1.30.1"

_REQUIRED_ENV = (
    "OPNSENSE_API_KEY",
    "OPNSENSE_API_SECRET",
)


class AnsibleRenderer(Renderer):
    name = "ansible"
    phase = "config"

    def validate(self) -> list[str]:
        errors: list[str] = []
        for k in _REQUIRED_ENV:
            if not os.environ.get(k):
                errors.append(f"missing env: {k}")
        if not shutil.which("ansible-playbook"):
            errors.append(
                f"ansible-playbook not in PATH — install Ansible >= {ANSIBLE_REQUIRED_MIN}"
            )
        return errors

    def execute(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
        prior_outputs = prior_outputs or {}
        infra_manifest_path = self._locate_infra_manifest(prior_outputs)
        if infra_manifest_path is None or not infra_manifest_path.exists():
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase=self.phase,
                error="infra_manifest.json not found — OpenTofu phase must run first",
            )

        infra = json.loads(infra_manifest_path.read_text())
        opnsense_ip = (infra.get("outputs") or {}).get("opnsense_ip")
        if not opnsense_ip:
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase=self.phase,
                error="opnsense_ip is null in infra_manifest — guest agent did not report",
            )

        try:
            self._render_inventory(opnsense_ip, infra)
            self._render_group_vars(infra)
            self._ensure_collections()
            self._run_playbook()
        except (subprocess.CalledProcessError, OSError) as exc:
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase=self.phase,
                error=f"Ansible run failed: {exc}",
            )

        return ProvisioningResult(
            success=True,
            renderer=self.name,
            phase=self.phase,
            outputs={
                "opnsense_ip": opnsense_ip,
                "opnsense_api_endpoint": f"https://{opnsense_ip}",
            },
        )

    # -----------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------

    def _ansible_dir(self) -> Path:
        d = self.work_dir / "ansible"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _locate_infra_manifest(self, prior_outputs: dict[str, Any]) -> Path | None:
        explicit = prior_outputs.get("infra_manifest_path")
        if explicit:
            return Path(explicit)
        # Convention: OpenTofuRenderer wrote it to <work_dir>/infra_manifest.json
        candidate = self.work_dir / "infra_manifest.json"
        if candidate.exists():
            return candidate
        return None

    def _render_inventory(self, opnsense_ip: str, infra: dict[str, Any]) -> None:
        d = self._ansible_dir()
        (d / "inventory").mkdir(parents=True, exist_ok=True)
        inv = {
            "opnsense": {
                "hosts": {
                    "opnsense": {
                        "ansible_host": opnsense_ip,
                        "ansible_connection": "local",  # API-driven, no SSH
                    }
                }
            }
        }
        with open(d / "inventory" / "hosts.yml", "w") as fh:
            yaml.safe_dump(inv, fh, sort_keys=False)

    def _render_group_vars(self, infra: dict[str, Any]) -> None:
        d = self._ansible_dir()
        (d / "group_vars").mkdir(parents=True, exist_ok=True)
        intent = infra.get("intent", {})
        vars_payload = {
            "opnsense_api_endpoint": f"https://{infra['outputs']['opnsense_ip']}",
            "opnsense_api_key": "{{ lookup('env', 'OPNSENSE_API_KEY') }}",
            "opnsense_api_secret": "{{ lookup('env', 'OPNSENSE_API_SECRET') }}",
            "opnsense_ssl_verify": False,
            "vlans": intent.get("vlans", []),
            "dns_upstream": intent.get("dns_upstream", []),
            "dns_domain": intent.get("domain", "lab.local"),
            "lan_interface": intent.get("lan_interface"),
            "wan_interface": intent.get("wan_interface"),
            "firewall_default_policy": intent.get("firewall_default_policy", "block"),
        }
        with open(d / "group_vars" / "opnsense.yml", "w") as fh:
            yaml.safe_dump(vars_payload, fh, sort_keys=False)

    def _ensure_collections(self) -> None:
        """Install ansibleguy.opnsense at the pinned version into a project-local
        collections path so we don't pollute ~/.ansible/collections."""
        d = self._ansible_dir()
        req = d / "requirements.yml"
        req.write_text(
            f"---\ncollections:\n  - name: {OPNSENSE_COLLECTION}\n    version: {OPNSENSE_COLLECTION_VERSION}\n"
        )
        coll_path = d / "collections"
        subprocess.run(
            [
                "ansible-galaxy",
                "collection",
                "install",
                "-r",
                str(req),
                "-p",
                str(coll_path),
                "--force",  # idempotent: ensures pinned version is what's there
            ],
            check=True,
        )

    def _playbook_root(self) -> Path:
        # Bundled inside vne/ansible/. Resolved relative to the velocitee core repo.
        # We try two locations: the running source tree, and an installed package.
        # For now, copy the playbook tree from <vne_pkg>/ansible into work_dir.
        from importlib import resources
        try:
            # Best-effort: use vne package data
            return Path(resources.files("vne") / "ansible")  # type: ignore[arg-type]
        except (ModuleNotFoundError, AttributeError):
            # Fallback: assume monorepo layout
            return Path(__file__).resolve().parent.parent.parent / "vne" / "ansible"

    def _run_playbook(self) -> None:
        d = self._ansible_dir()
        src = self._playbook_root()
        if not src.exists():
            raise FileNotFoundError(
                f"vne/ansible/ playbook tree not found at {src}"
            )

        # Materialise playbooks into work_dir alongside the rendered inventory.
        for sub in ("playbooks", "roles"):
            target = d / sub
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(src / sub, target)

        env = os.environ.copy()
        env["ANSIBLE_COLLECTIONS_PATH"] = str(d / "collections")
        env["ANSIBLE_ROLES_PATH"] = str(d / "roles")
        env["ANSIBLE_INVENTORY"] = str(d / "inventory" / "hosts.yml")
        # Disable host-key checking — we just talk to the OPNsense API, no SSH.
        env["ANSIBLE_HOST_KEY_CHECKING"] = "False"

        cmd = [
            "ansible-playbook",
            str(d / "playbooks" / "configure-network.yml"),
            "-i", str(d / "inventory" / "hosts.yml"),
        ]
        log.info("ansible: %s", " ".join(cmd))
        subprocess.run(cmd, cwd=d, env=env, check=True)


register("ansible", AnsibleRenderer)
