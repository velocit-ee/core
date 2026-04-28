"""OpenTofu renderer — generates HCL, runs `tofu apply`, parses outputs.

Produces an infra_manifest.json that the Ansible renderer consumes in the next
phase. We deliberately keep the rendered HCL minimal: one OPNsense VM resource,
plus outputs for the IP and resource IDs. Network configuration (VLANs, DHCP,
DNS, firewall) is the Ansible phase's job — splitting at this seam matches
the OpenTofu/Ansible convention (infra vs. config).

Provider versions are pinned in versions.tf, written every run from a literal
inside this module — no '>= x.y' anywhere. Pinning lives in code, not docs.

State: vne/terraform/terraform.tfstate (local backend). The orchestrator
re-runs `tofu apply` on retries — Terraform's own state handles idempotency.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from ..renderer import ConfigKey, Renderer
from ..renderer_registry import register
from ..schema import ProvisioningResult

log = logging.getLogger("velocitee.renderer.opentofu")

# Pinned versions. Change here; nowhere else.
TERRAFORM_REQUIRED = ">= 1.6.0, < 2.0.0"
PROXMOX_PROVIDER_VERSION = "0.66.2"   # bpg/proxmox

class OpenTofuRenderer(Renderer):
    name = "opentofu"
    phase = "infra"

    config_keys = [
        ConfigKey(
            env="PROXMOX_VE_ENDPOINT",
            type="url",
            required=True,
            description="Proxmox VE API endpoint, e.g. https://proxmox.lab:8006",
        ),
        ConfigKey(
            env="PROXMOX_VE_API_TOKEN",
            type="password",
            required=True,
            description="Proxmox VE API token (user@realm!tokenid=secret)",
        ),
        ConfigKey(
            env="PROXMOX_VE_INSECURE",
            type="bool",
            required=False,
            description="Set to '1' for self-signed Proxmox certs",
        ),
        ConfigKey(
            env="PROXMOX_VE_NODE",
            type="string",
            required=False,
            description="Pin Proxmox node when the cluster has more than one",
        ),
    ]

    def validate(self) -> list[str]:
        errors: list[str] = []
        for key in self.required_env():
            if not os.environ.get(key):
                errors.append(f"missing env: {key}")
        if not shutil.which("tofu"):
            errors.append(
                "OpenTofu CLI 'tofu' not found in PATH — install OpenTofu >= 1.6"
            )
        if self.intent.opnsense.vm.iso_url is None:
            errors.append("opnsense.vm.iso_url required for OpenTofu backend")
        return errors

    def execute(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
        try:
            self._render_files()
            self._tofu("init", "-input=false", "-no-color")
            self._tofu("apply", "-auto-approve", "-input=false", "-no-color")
            outputs = self._read_outputs()
        except (subprocess.CalledProcessError, OSError) as exc:
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase=self.phase,
                error=f"OpenTofu run failed: {exc}",
            )

        manifest_path = self._write_infra_manifest(outputs)

        return ProvisioningResult(
            success=True,
            renderer=self.name,
            phase=self.phase,
            outputs={
                "opnsense_ip": outputs.get("opnsense_ip"),
                "opnsense_vmid": outputs.get("opnsense_vmid"),
                "proxmox_node": outputs.get("proxmox_node"),
            },
            artifacts={"infra_manifest": str(manifest_path)},
        )

    # -----------------------------------------------------------------
    # File rendering
    # -----------------------------------------------------------------

    def _tf_dir(self) -> Path:
        d = self.work_dir / "terraform"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _render_files(self) -> None:
        tf = self._tf_dir()
        (tf / "versions.tf").write_text(self._versions_tf())
        (tf / "variables.tf").write_text(self._variables_tf())
        (tf / "main.tf").write_text(self._main_tf())
        (tf / "outputs.tf").write_text(self._outputs_tf())
        (tf / "terraform.tfvars").write_text(self._tfvars())

    def _versions_tf(self) -> str:
        return f"""terraform {{
  required_version = "{TERRAFORM_REQUIRED}"
  required_providers {{
    proxmox = {{
      source  = "bpg/proxmox"
      version = "{PROXMOX_PROVIDER_VERSION}"
    }}
  }}
}}

provider "proxmox" {{
  endpoint  = var.proxmox_endpoint
  api_token = var.proxmox_api_token
  insecure  = var.proxmox_insecure
}}
"""

    def _variables_tf(self) -> str:
        return """variable "proxmox_endpoint"   { type = string }
variable "proxmox_api_token"  { type = string, sensitive = true }
variable "proxmox_insecure"   { type = bool, default = false }
variable "proxmox_node"       { type = string }
variable "vmid"               { type = number }
variable "vm_name"            { type = string }
variable "cores"              { type = number }
variable "memory_mb"          { type = number }
variable "disk_gb"            { type = number }
variable "storage_pool"       { type = string }
variable "iso_url"            { type = string }
variable "iso_checksum"       { type = string }
variable "iso_checksum_algorithm" { type = string }
variable "wan_bridge"         { type = string, default = "vmbr0" }
variable "lan_bridge"         { type = string, default = "vmbr1" }
"""

    def _main_tf(self) -> str:
        return """resource "proxmox_virtual_environment_download_file" "opnsense_iso" {
  content_type        = "iso"
  datastore_id        = "local"
  node_name           = var.proxmox_node
  url                 = var.iso_url
  file_name           = "opnsense-${var.vmid}.iso"
  checksum            = var.iso_checksum
  checksum_algorithm  = var.iso_checksum_algorithm
  overwrite           = false
}

resource "proxmox_virtual_environment_vm" "opnsense" {
  name        = var.vm_name
  node_name   = var.proxmox_node
  vm_id       = var.vmid
  bios        = "ovmf"
  machine     = "q35"
  on_boot     = true

  cpu {
    cores = var.cores
    type  = "host"
  }

  memory {
    dedicated = var.memory_mb
  }

  disk {
    datastore_id = var.storage_pool
    interface    = "scsi0"
    size         = var.disk_gb
    file_format  = "qcow2"
  }

  cdrom {
    enabled   = true
    file_id   = proxmox_virtual_environment_download_file.opnsense_iso.id
    interface = "ide2"
  }

  network_device {
    bridge   = var.wan_bridge
    model    = "virtio"
  }

  network_device {
    bridge   = var.lan_bridge
    model    = "virtio"
  }

  agent {
    enabled = true
  }

  boot_order = ["ide2", "scsi0"]
}
"""

    def _outputs_tf(self) -> str:
        return """output "opnsense_ip" {
  value       = try(proxmox_virtual_environment_vm.opnsense.ipv4_addresses[1][0], null)
  description = "First IPv4 reported by the LAN interface (index 1) — null until the guest agent reports."
}

output "opnsense_vmid" {
  value = proxmox_virtual_environment_vm.opnsense.vm_id
}

output "proxmox_node" {
  value = var.proxmox_node
}
"""

    def _tfvars(self) -> str:
        # We keep secrets out of tfvars; OpenTofu reads them from TF_VAR_*.
        vm = self.intent.opnsense.vm
        algo, _, hexdigest = (vm.iso_checksum or ":").partition(":")
        return f'''proxmox_node = "{_proxmox_node()}"
vmid         = {vm.vmid}
vm_name      = "{vm.name}"
cores        = {vm.cores}
memory_mb    = {vm.memory_mb}
disk_gb      = {vm.disk_gb}
storage_pool = "{vm.storage_pool}"
iso_url      = "{vm.iso_url}"
iso_checksum = "{hexdigest}"
iso_checksum_algorithm = "{algo or 'sha256'}"
'''

    # -----------------------------------------------------------------
    # Process execution
    # -----------------------------------------------------------------

    def _tofu(self, *args: str) -> None:
        env = os.environ.copy()
        env.setdefault("TF_VAR_proxmox_endpoint", os.environ["PROXMOX_VE_ENDPOINT"])
        env.setdefault("TF_VAR_proxmox_api_token", os.environ["PROXMOX_VE_API_TOKEN"])
        env.setdefault("TF_VAR_proxmox_insecure",
                       "true" if os.environ.get("PROXMOX_VE_INSECURE") == "1" else "false")
        cmd = ["tofu", *args]
        log.info("opentofu: %s", " ".join(cmd))
        subprocess.run(cmd, cwd=self._tf_dir(), env=env, check=True)

    def _read_outputs(self) -> dict[str, Any]:
        result = subprocess.run(
            ["tofu", "output", "-json"],
            cwd=self._tf_dir(),
            check=True,
            capture_output=True,
            text=True,
        )
        raw = json.loads(result.stdout or "{}")
        # tofu output -json returns {key: {value: …, type: …}}
        return {k: v.get("value") for k, v in raw.items()}

    def _write_infra_manifest(self, outputs: dict[str, Any]) -> Path:
        out = self.work_dir / "infra_manifest.json"
        payload = {
            "schema_version": "1.0",
            "renderer": self.name,
            "outputs": outputs,
            "intent": {
                "opnsense_vmid": self.intent.opnsense.vm.vmid,
                "lan_interface": self.intent.network.lan_interface,
                "wan_interface": self.intent.network.wan_interface,
                "domain": self.intent.network.dns.domain,
                "vlans": [
                    {
                        "id": v.id, "name": v.name, "cidr": v.cidr,
                        "gateway": v.gateway,
                        "dhcp_start": v.dhcp_start, "dhcp_end": v.dhcp_end,
                        "dhcp_lease_time": v.dhcp_lease_time,
                    }
                    for v in self.intent.network.vlans
                ],
                "dns_upstream": list(self.intent.network.dns.upstream),
                "firewall_default_policy": self.intent.network.firewall.default_policy,
            },
        }
        out.write_text(json.dumps(payload, indent=2))
        return out


def _proxmox_node() -> str:
    """Read PROXMOX_VE_NODE from env or default to 'pve' — operators with single
    node clusters never have to set this."""
    return os.environ.get("PROXMOX_VE_NODE", "pve")


register("opentofu", OpenTofuRenderer)
