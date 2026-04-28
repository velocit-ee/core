"""Provisioner-agnostic internal schema for velocitee engines.

These Pydantic models express *intent* — "create a VM with these specs", "this
VLAN exists on that VM" — never provisioner syntax. Engine-level config files
(velocitee.yml blocks) are parsed into engine-local Pydantic models that then
translate into the models defined here. Renderers consume these models and
emit whatever provisioner-specific artifacts they need.

Adding a new provisioner means writing a Renderer subclass against this schema.
The schema does not change.
"""

from __future__ import annotations

import ipaddress
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Primitives
# ---------------------------------------------------------------------------

class _StrictModel(BaseModel):
    """Forbid extra keys everywhere — typos in config files become errors."""
    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=False)


# ---------------------------------------------------------------------------
# Hardware / VM
# ---------------------------------------------------------------------------

class VMSpec(_StrictModel):
    """Compute resource definition for a VM the engine will create."""
    name: str
    vmid: int = Field(ge=100, le=999_999_999, description="Proxmox VMID; unique per cluster.")
    cores: int = Field(ge=1, le=256)
    memory_mb: int = Field(ge=256, le=4_194_304)
    disk_gb: int = Field(ge=1, le=65_536)
    storage_pool: str = Field(min_length=1)
    iso_url: str | None = None
    iso_checksum: str | None = Field(
        default=None,
        description="Form: 'sha256:<hex>'. Required when iso_url is set.",
    )
    cpu_type: str = "host"
    bios: Literal["seabios", "ovmf"] = "ovmf"
    machine: str = "q35"

    @model_validator(mode="after")
    def _iso_checksum_required(self) -> VMSpec:
        if self.iso_url and not self.iso_checksum:
            raise ValueError("iso_checksum is required when iso_url is set")
        if self.iso_checksum and not self.iso_checksum.startswith(("sha256:", "sha512:")):
            raise ValueError("iso_checksum must start with 'sha256:' or 'sha512:'")
        return self


class Volume(_StrictModel):
    """Persistent storage volume.

    VME doesn't manage volumes today; VSE will. This schema is here now so
    backends written against `shared/schema.py` (especially future provider
    plugins like an OpenNebula or OpenStack renderer) can model storage as
    a first-class object instead of bolting it onto VMSpec.
    """
    name: str = Field(min_length=1)
    size_gb: int = Field(ge=1, le=65_536)
    storage_pool: str = Field(min_length=1)
    bootable: bool = False
    image_id: str | None = Field(
        default=None,
        description="Backend-specific image identifier when this volume should "
                    "be cloned from a base image (e.g. an OS template). When set, "
                    "`bootable` should also be true.",
    )
    fs: Literal["raw", "qcow2", "ext4", "xfs", "btrfs"] | None = None

    @model_validator(mode="after")
    def _bootable_image(self) -> Volume:
        if self.image_id and not self.bootable:
            raise ValueError("Volume.image_id is set but bootable=False — "
                             "imaged volumes must be bootable to be useful")
        return self


# ---------------------------------------------------------------------------
# Networking
# ---------------------------------------------------------------------------

class VLAN(_StrictModel):
    """A single Layer-2/3 VLAN with optional DHCP scope."""
    id: int = Field(ge=1, le=4094)
    name: str = Field(min_length=1, max_length=32)
    cidr: str
    dhcp_start: str | None = None
    dhcp_end: str | None = None
    dhcp_lease_time: int = Field(default=86400, ge=60, le=2_592_000)

    @field_validator("cidr")
    @classmethod
    def _valid_cidr(cls, v: str) -> str:
        ipaddress.ip_network(v, strict=False)
        return v

    @model_validator(mode="after")
    def _dhcp_inside_cidr(self) -> VLAN:
        if (self.dhcp_start is None) != (self.dhcp_end is None):
            raise ValueError("dhcp_start and dhcp_end must both be set or both omitted")
        if self.dhcp_start and self.dhcp_end:
            net = ipaddress.ip_network(self.cidr, strict=False)
            start = ipaddress.ip_address(self.dhcp_start)
            end = ipaddress.ip_address(self.dhcp_end)
            if start not in net or end not in net:
                raise ValueError(f"DHCP range {start}-{end} is not inside {self.cidr}")
            if int(start) > int(end):
                raise ValueError("dhcp_start must be <= dhcp_end")
        return self

    @property
    def gateway(self) -> str:
        """Convention: VNE assigns the first usable address as the gateway."""
        net = ipaddress.ip_network(self.cidr, strict=False)
        return str(next(iter(net.hosts())))


class DNS(_StrictModel):
    upstream: list[str] = Field(min_length=1)
    domain: str = "lab.local"

    @field_validator("upstream")
    @classmethod
    def _valid_ips(cls, v: list[str]) -> list[str]:
        for ip in v:
            ipaddress.ip_address(ip)
        return v


class FirewallRule(_StrictModel):
    description: str = Field(min_length=1, max_length=120)
    src_vlan: int | Literal["any"] = "any"
    dst_vlan: int | Literal["any"] = "any"
    dst: str = "any"
    proto: Literal["any", "tcp", "udp", "icmp"] = "any"
    action: Literal["allow", "block", "reject"] = "allow"
    port: int | None = Field(default=None, ge=1, le=65535)


class Firewall(_StrictModel):
    default_policy: Literal["allow", "block"] = "block"
    allow_rules: list[FirewallRule] = Field(default_factory=list)


class NetworkConfig(_StrictModel):
    """The complete declarative network state VNE manages."""
    wan_interface: str
    lan_interface: str
    vlans: list[VLAN] = Field(default_factory=list)
    dns: DNS
    firewall: Firewall = Field(default_factory=Firewall)

    @model_validator(mode="after")
    def _unique_vlan_ids(self) -> NetworkConfig:
        ids = [v.id for v in self.vlans]
        if len(ids) != len(set(ids)):
            raise ValueError("VLAN ids must be unique")
        names = [v.name for v in self.vlans]
        if len(names) != len(set(names)):
            raise ValueError("VLAN names must be unique")
        return self


# ---------------------------------------------------------------------------
# OPNsense — the network appliance VNE deploys
# ---------------------------------------------------------------------------

class OPNsense(_StrictModel):
    vm: VMSpec
    version: str = "24.7"
    api_endpoint: str | None = None  # populated by renderer after VM boots


# ---------------------------------------------------------------------------
# Top-level intent for VNE
# ---------------------------------------------------------------------------

class VNEIntent(_StrictModel):
    """Everything VNE needs to deliver a configured network.

    Renderers receive an instance of this model. From here they emit whatever
    provisioner artifacts are needed. The Pydantic guarantee: if a VNEIntent
    instance exists, it's internally consistent and renderable.
    """
    proxmox_host: str = Field(min_length=1, description="IP or hostname of the Proxmox node from VME.")
    opnsense: OPNsense
    network: NetworkConfig

    def vlan_by_id(self, vlan_id: int) -> VLAN | None:
        return next((v for v in self.network.vlans if v.id == vlan_id), None)


# ---------------------------------------------------------------------------
# Provisioning result — what every renderer returns
# ---------------------------------------------------------------------------

class ProvisioningResult(_StrictModel):
    """Renderer output. Pipeline collects these and threads them between phases."""
    success: bool
    renderer: str = Field(description="Provisioner name, e.g. 'velocitee-native'.")
    phase: str = Field(description="'infra' or 'config'.")
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    artifacts: dict[str, str] = Field(
        default_factory=dict,
        description="Optional: filesystem paths to artifacts the renderer produced "
                    "(e.g. {'infra_manifest': '/abs/path/infra_manifest.json'}).",
    )
