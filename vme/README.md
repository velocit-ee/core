# VME — velocitee metal provisioning engine

**phase 1 · step 0 · in active development**

Part of the [velocit.ee](https://velocit.ee) engine stack — lives in [velocit-ee/core](https://github.com/velocit-ee/core). VME turns bare, unconfigured hardware into a provisioned machine running Proxmox VE or Ubuntu Server — fully unattended, from a single seed machine on the provisioning network.

---

## what it does

1. Runs a seed stack (dnsmasq + nginx + iPXE) on a laptop or Raspberry Pi connected to the provisioning network
2. PXE boots target machines — no USB drives, no manual OS install
3. Drives a fully unattended install of Proxmox VE or Ubuntu Server
4. Writes a structured **handoff manifest** on success, describing the provisioned machine (hostname, IP, OS, role) for downstream engines (VNE, VSE)

VME is stateless by design. Re-running it against already-provisioned hardware is safe.

---

## technical approach

| component | role |
|-----------|------|
| **dnsmasq** | DHCP + TFTP — hands out IPs and serves the iPXE bootloader to PXE clients |
| **nginx** | HTTP — serves iPXE scripts and OS install assets |
| **iPXE** | chainloaded bootloader — selects the correct install chain per target role |
| **preseed / autoinstall** | drives the unattended OS install (Debian preseed for Proxmox, cloud-init autoinstall for Ubuntu) |

The seed machine needs only a NIC on the provisioning network and enough disk to hold the install assets. No internet access required on the provisioning network after initial asset download.

---

## handoff manifest

On successful provisioning, VME writes a YAML manifest:

```yaml
hostname: node-01
ip: 192.168.100.10
mac: aa:bb:cc:dd:ee:ff
os: proxmox-ve-8
role: hypervisor
provisioned_at: 2026-04-05T14:32:00Z
```

VNE (phase 2) consumes this manifest to configure networking on the provisioned host.

---

## prerequisites

**seed machine**
- any x86_64 or ARM64 machine (laptop, Raspberry Pi 4+, small server)
- one NIC connected to the provisioning network
- Ubuntu 22.04+ or Debian 12+ recommended
- ~10 GB free disk for install assets

**target hardware**
- x86_64
- PXE-capable NIC (most hardware from the last 15 years qualifies)
- BIOS/UEFI configured to network-boot on the provisioning NIC
- ≥ 4 GB RAM, ≥ 64 GB storage (Proxmox VE minimum)

---

## status

Phase 1 — **working, end-to-end tested.** VME successfully PXE boots a target, streams the OS image over the network, runs a fully unattended Ubuntu Server install, and shuts down the target when done. Proxmox VE install path is implemented; Ubuntu Server is confirmed working.

See [docs/getting-started.md](docs/getting-started.md) to get started.

Track progress in [issues](https://github.com/velocit-ee/core/issues).

---

## license

GNU Affero General Public License v3.0 — see [LICENSE](LICENSE).

Part of the velocit.ee open-core engine stack. See [velocit-ee](https://github.com/velocit-ee) for the full architecture.
