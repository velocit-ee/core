"""Shared fixtures for the VNE test suite."""
from __future__ import annotations

import pytest

from shared.schema import (
    DNS,
    Firewall,
    FirewallRule,
    NetworkConfig,
    OPNsense,
    VLAN,
    VMSpec,
    VNEIntent,
)


@pytest.fixture
def intent() -> VNEIntent:
    """A representative VNEIntent used across renderer tests."""
    vm = VMSpec(
        name="opnsense-100",
        vmid=100,
        cores=2,
        memory_mb=2048,
        disk_gb=20,
        storage_pool="local-lvm",
        iso_url="https://mirror.example.org/OPNsense-24.7-dvd-amd64.iso",
        iso_checksum="sha256:" + "ab" * 32,
    )
    network = NetworkConfig(
        wan_interface="ens18",
        lan_interface="ens19",
        vlans=[
            VLAN(id=10, name="mgmt", cidr="10.10.10.0/24",
                 dhcp_start="10.10.10.100", dhcp_end="10.10.10.200"),
            VLAN(id=20, name="users", cidr="10.10.20.0/24"),
        ],
        dns=DNS(upstream=["1.1.1.1", "9.9.9.9"], domain="lab.local"),
        firewall=Firewall(
            default_policy="block",
            allow_rules=[FirewallRule(description="mgmt to any", src_vlan=10, action="allow")],
        ),
    )
    return VNEIntent(
        proxmox_host="192.168.1.10",
        opnsense=OPNsense(vm=vm, version="24.7"),
        network=network,
    )
