# velocitee core

[![CI](https://github.com/velocit-ee/core/actions/workflows/ci.yml/badge.svg)](https://github.com/velocit-ee/core/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/velocit-ee/core?label=release)](https://github.com/velocit-ee/core/releases)
[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

**Open-source engine stack powering [velocit.ee](https://velocit.ee). Takes hardware from bare metal to a fully documented, running infrastructure stack — engine by engine.**

---

## What it does

velocitee core is a pipeline of four independent engines. Each one does one thing, writes a structured handoff manifest, and passes it to the next. Enter the pipeline wherever your hardware already is.

```
bare metal ──► VME ──► VNE ──► VSE ──► VLE ──► documented, running stack
               │        │       │        │
            provision  network  services  lifecycle
```

| Engine | Phase | Status | Description |
|--------|-------|--------|-------------|
| **VME** | 1 | Active | Bare-metal provisioning — PXE boot + unattended OS install (Proxmox VE, Ubuntu Server) |
| **VNE** | 2 | Active (initial) | Network configuration — OPNsense VM, VLANs, DHCP, DNS, firewall. Provisioner-agnostic via the renderer registry. |
| VSE | 3 | Planned | Services — containerised stack deployment, idempotent configuration |
| VLE | 4 | Planned | Lifecycle — monitoring, drift detection, auto-docs, single-command repair |

Each engine is independently useful. You don't need the full pipeline to get value from VME.

---

## Quick start — VME

```bash
curl -fsSL https://raw.githubusercontent.com/velocit-ee/core/main/vme/install.sh | bash
cd ~/vme/vme
vme setup       # guided config wizard
vme preflight   # verify the seed machine is ready
vme deploy      # power on the target — everything else is automatic
```

See the [VME getting-started guide](https://docs.velocit.ee/vme/getting-started/) for full documentation.

---

## Requirements

**Seed machine** (runs VME)
- Ubuntu 22.04+ or Debian 12+
- Docker Engine
- One NIC connected to the provisioning switch
- ~10 GB free disk for OS image cache

**Target hardware**
- x86\_64
- PXE-capable NIC

---

## Architecture

VME runs a containerised seed stack on the provisioning machine:

| Component | Role |
|-----------|------|
| **dnsmasq** | DHCP + TFTP — assigns IPs and serves the iPXE bootloader |
| **nginx** | HTTP — serves boot scripts and OS install assets |
| **iPXE** | Chainloaded bootloader — selects the correct install chain per target OS |
| **cloud-init / autoinstall** | Drives the unattended OS install |

On success, VME writes a **handoff manifest** — a JSON document describing the provisioned machine (hostname, IP, OS, SSH key, timing). Downstream engines read this manifest to continue the pipeline without re-asking for config.

---

## Config sources

All engines accept config from:

- Local file (`vme-config.yml`)
- Git repository
- velocit.ee authenticated registry *(SaaS tier — AI-assisted config generation)*

---

## Development

```bash
git clone https://github.com/velocit-ee/core
cd core/vme
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest
```

---

## Contributing

All contributors must sign the [Contributor License Agreement](CLA.md) before a pull request can be merged.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines and code standards.

---

## License

The velocitee core engines are open source under the [GNU Affero General Public License v3.0](LICENSE).

- **Self-hosted use** — free, always
- **Network service deployment** — modifications must be released under AGPL v3
- **Proprietary embedding** — [commercial license available](https://docs.velocit.ee/commercial-license/)

The velocit.ee AI configuration generator (SaaS layer) is proprietary and not included in this repository.
