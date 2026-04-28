"""Parse and validate the velocitee.yml VNE block.

velocitee.yml is the user's single source of truth. This module parses the
VNE-relevant sections into Pydantic models, validates them, then translates
them into the provisioner-agnostic shared/schema.py models. The translation
layer is one-way: edit velocitee.yml, re-run VNE — VNE does not write back.

Two-stage modeling:
  1. VNEFileConfig   — mirrors the YAML literally (ergonomic for users)
  2. shared.schema.VNEIntent — internal renderable form (what renderers consume)

Stage 1 is what we expose for "did the user write valid config" errors.
Stage 2 is what we hand to the orchestrator.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from shared import schema as core
from shared import renderer_registry


# ---------------------------------------------------------------------------
# velocitee.yml literal models
# ---------------------------------------------------------------------------

class _UserModel(BaseModel):
    """Strict on extras at the leaves; allow extras at the velocitee.yml root
    so other engines' blocks (vse, vle) don't break VNE parsing."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class VelociteeBlock(_UserModel):
    """The top-level 'velocitee:' block — provisioner selection lives here."""
    provisioner: str = Field(min_length=1)


class VLANBlock(_UserModel):
    id: int = Field(ge=1, le=4094)
    name: str = Field(min_length=1, max_length=32)
    cidr: str
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    dhcp_lease_time: int = 86400


class DNSBlock(_UserModel):
    upstream: list[str]
    domain: str = "lab.local"


class FirewallRuleBlock(_UserModel):
    description: str
    src_vlan: int | Literal["any"] = "any"
    dst_vlan: int | Literal["any"] = "any"
    dst: str = "any"
    proto: Literal["any", "tcp", "udp", "icmp"] = "any"
    action: Literal["allow", "block", "reject"] = "allow"
    port: int | None = None


class FirewallBlock(_UserModel):
    default_policy: Literal["allow", "block"] = "block"
    allow_rules: list[FirewallRuleBlock] = Field(default_factory=list)


class OPNsenseVMBlock(_UserModel):
    vmid: int = Field(ge=100)
    cores: int = 2
    memory_mb: int = 2048
    disk_gb: int = 20
    storage_pool: str = "local-lvm"


class VNEBlock(_UserModel):
    """The 'vne:' block in velocitee.yml."""
    wan_interface: str = Field(min_length=1)
    lan_interface: str = Field(min_length=1)
    opnsense_version: str = "24.7"
    opnsense_iso_url: str = Field(min_length=1)
    opnsense_iso_checksum: str = Field(min_length=1)
    vlans: list[VLANBlock] = Field(default_factory=list)
    dns: DNSBlock
    firewall: FirewallBlock = Field(default_factory=FirewallBlock)
    opnsense_vm: OPNsenseVMBlock

    @field_validator("opnsense_iso_checksum")
    @classmethod
    def _checksum_format(cls, v: str) -> str:
        if not (v.startswith("sha256:") or v.startswith("sha512:")):
            raise ValueError(
                "opnsense_iso_checksum must be 'sha256:<hex>' or 'sha512:<hex>'"
            )
        return v


class VelociteeFileConfig(BaseModel):
    """The whole velocitee.yml file (only the bits VNE cares about).

    Other engines' blocks (vse, vle, vme) may be present and are ignored.
    """
    model_config = ConfigDict(extra="allow")  # tolerate other engines' blocks
    velocitee: VelociteeBlock
    vne: VNEBlock


# ---------------------------------------------------------------------------
# Parse / translate
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised on any user-facing config problem. Message is human-readable."""


def load_file(path: Path) -> VelociteeFileConfig:
    """Read and Pydantic-validate velocitee.yml. Raises ConfigError on any issue."""
    if not path.exists():
        raise ConfigError(f"velocitee.yml not found: {path}")

    try:
        with open(path) as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"velocitee.yml is not valid YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("velocitee.yml must be a mapping at the top level")

    if "velocitee" not in raw:
        raise ConfigError("velocitee.yml is missing the required 'velocitee:' block")
    if "vne" not in raw:
        raise ConfigError("velocitee.yml is missing the required 'vne:' block")

    try:
        return VelociteeFileConfig.model_validate(raw)
    except ValidationError as exc:
        # Format errors for humans, not Python tracebacks.
        lines = ["velocitee.yml validation failed:"]
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"])
            lines.append(f"  - {loc}: {err['msg']}")
        raise ConfigError("\n".join(lines)) from exc


def validate_provisioner(name: str) -> None:
    """Confirm the named provisioner is registered. Fail early with a clear list."""
    renderer_registry.ensure_loaded()
    if not renderer_registry.is_registered(name):
        avail = ", ".join(renderer_registry.available()) or "(none)"
        raise ConfigError(
            f"unknown provisioner '{name}'.\n"
            f"  Set velocitee.provisioner to one of: {avail}"
        )


def to_intent(file_cfg: VelociteeFileConfig, *, proxmox_host: str) -> core.VNEIntent:
    """Translate the user-facing config into the internal renderable schema.

    proxmox_host is supplied by deploy.py from the VME manifest — config.py
    doesn't read VME manifests, the entry point does.
    """
    vne = file_cfg.vne

    vlans = [
        core.VLAN(
            id=v.id,
            name=v.name,
            cidr=v.cidr,
            dhcp_start=v.dhcp_start,
            dhcp_end=v.dhcp_end,
            dhcp_lease_time=v.dhcp_lease_time,
        )
        for v in vne.vlans
    ]

    dns = core.DNS(upstream=list(vne.dns.upstream), domain=vne.dns.domain)

    firewall = core.Firewall(
        default_policy=vne.firewall.default_policy,
        allow_rules=[
            core.FirewallRule(
                description=r.description,
                src_vlan=r.src_vlan,
                dst_vlan=r.dst_vlan,
                dst=r.dst,
                proto=r.proto,
                action=r.action,
                port=r.port,
            )
            for r in vne.firewall.allow_rules
        ],
    )

    network = core.NetworkConfig(
        wan_interface=vne.wan_interface,
        lan_interface=vne.lan_interface,
        vlans=vlans,
        dns=dns,
        firewall=firewall,
    )

    opnsense_vm = core.VMSpec(
        name=f"opnsense-{vne.opnsense_vm.vmid}",
        vmid=vne.opnsense_vm.vmid,
        cores=vne.opnsense_vm.cores,
        memory_mb=vne.opnsense_vm.memory_mb,
        disk_gb=vne.opnsense_vm.disk_gb,
        storage_pool=vne.opnsense_vm.storage_pool,
        iso_url=vne.opnsense_iso_url,
        iso_checksum=vne.opnsense_iso_checksum,
    )

    opnsense = core.OPNsense(vm=opnsense_vm, version=vne.opnsense_version)

    return core.VNEIntent(
        proxmox_host=proxmox_host,
        opnsense=opnsense,
        network=network,
    )
