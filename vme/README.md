# VME — velocitee metal provisioning engine

[![CI](https://github.com/velocit-ee/core/actions/workflows/ci.yml/badge.svg)](https://github.com/velocit-ee/core/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](../LICENSE)

**Phase 1 of the [velocit.ee](https://velocit.ee) engine stack. Turns bare, unconfigured hardware into a provisioned machine running Proxmox VE or Ubuntu Server — fully unattended, driven from a single seed machine on the provisioning network.**

---

## What it does

1. Runs a seed stack (dnsmasq + nginx + iPXE) on a laptop or Raspberry Pi connected to the provisioning network
2. PXE boots target machines — no USB drives, no manual OS install
3. Drives a fully unattended install of Proxmox VE or Ubuntu Server
4. Writes a structured **handoff manifest** on success for downstream engines (VNE, VSE)

VME is stateless by design. Re-running it against already-provisioned hardware is safe.

---

## Quick start

```bash
curl -fsSL https://raw.githubusercontent.com/velocit-ee/core/main/vme/install.sh | bash
cd ~/vme/vme
vme setup       # guided config wizard — detects interfaces, writes vme-config.yml
vme preflight   # verify Docker, interface, DHCP, disk space
vme deploy      # power on the target — everything else is automatic
```

See <https://docs.velocit.ee/vme/getting-started/> for the full walkthrough.

---

## Technical approach

| Component | Role |
|-----------|------|
| **dnsmasq** | DHCP + TFTP — assigns IPs and serves the iPXE bootloader to PXE clients |
| **nginx** | HTTP — serves iPXE scripts, boot files, and OS install assets |
| **iPXE** | Chainloaded bootloader — selects the install chain per target OS |
| **cloud-init autoinstall** | Drives the unattended Ubuntu Server install |
| **Proxmox installer** | Unattended Proxmox VE install via patched initrd + answer file |

The seed machine needs only a NIC on the provisioning network and enough disk to hold the install assets. No internet access required on the provisioning network after initial asset download.

---

## Handoff manifest

On success, VME writes a JSON manifest to `manifests/output/<hostname>-<timestamp>.json`:

```json
{
  "schema_version": "1.0",
  "target": {
    "hostname": "node-01",
    "ip": "192.168.100.10",
    "prefix": 24,
    "gateway": "192.168.100.1",
    "os": "proxmox-ve",
    "mac": "aa:bb:cc:dd:ee:ff"
  },
  "access": {
    "username": "root",
    "ssh_public_key": "ssh-ed25519 ...",
    "ssh_port": 22
  },
  "engines": {
    "vme": {
      "status": "success",
      "version": "0.1.0",
      "started_at": "2026-04-24T10:00:00+00:00",
      "completed_at": "2026-04-24T10:14:22+00:00",
      "duration_seconds": 862.0
    }
  }
}
```

VNE (phase 2) reads this manifest to continue the pipeline without re-asking for config.

---

## Requirements

**Seed machine**
- Ubuntu 22.04+ or Debian 12+
- Docker Engine
- One NIC connected to the provisioning switch
- ~10 GB free disk for OS image cache

**Target hardware**
- x86\_64
- PXE-capable NIC
- BIOS/UEFI configured to network-boot on the provisioning NIC
- ≥ 4 GB RAM, ≥ 64 GB storage (Proxmox VE minimum)

---

## Status

**Working end-to-end.** VME successfully PXE boots a target, streams the OS image over the network, runs a fully unattended install of Proxmox VE or Ubuntu Server, and shuts down the target when done. Both OS targets confirmed working against real hardware and Proxmox VMs.

---

## License

GNU Affero General Public License v3.0 — see [LICENSE](../LICENSE).

Part of the velocit.ee open-core engine stack. See [velocit-ee](https://github.com/velocit-ee) for the full architecture.
