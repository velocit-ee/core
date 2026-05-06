# velocitee core

[![CI](https://github.com/velocit-ee/core/actions/workflows/ci.yml/badge.svg)](https://github.com/velocit-ee/core/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/velocit-ee/core?label=release)](https://github.com/velocit-ee/core/releases)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
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

<!-- ENGINE-STATUS:BEGIN region=engine-table -->
| Engine  | Phase | Status  | Description |
|---------|:-----:|---------|-------------|
| **VME** | 1     | Stable  | Bare-metal provisioning — PXE boot + unattended OS install (Proxmox VE, Ubuntu Server). Two backends: `builtin` seed stack or `maas` (optional). |
| **VNE** | 2     | Stable  | Network configuration — OPNsense VM, VLANs, DHCP, DNS, firewall. Provisioner-agnostic via the renderer registry. Discovery + Path B (`vne join`) for existing networks. |
| VSE     | 3     | Planned | Services — containerised stack deployment, idempotent configuration |
| VLE     | 4     | Planned | Lifecycle — monitoring, drift detection, auto-docs, single-command repair |
<!-- ENGINE-STATUS:END region=engine-table -->

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

VME ships two backends. Pick one in `vme-config.yml` (`backend: builtin` is the default):

| Backend   | What it does                                                                  | When to choose it |
|-----------|-------------------------------------------------------------------------------|-------------------|
| `builtin` | Runs a containerised seed stack on the provisioning machine — dnsmasq (DHCP + TFTP), nginx (HTTP), iPXE (chainloaded bootloader), cloud-init / autoinstall. | Default. No external services needed; runs on a laptop or a Pi. |
| `maas`    | Hands provisioning off to an existing Canonical [MAAS](https://maas.io) region+rack controller via its REST API.                            | Operators who already run MAAS for inventory / IPMI / commissioning. |

On success, VME writes a **handoff manifest** — a JSON document describing the provisioned machine (hostname, IP, OS, SSH key, timing, the backend that ran it). Downstream engines read this manifest to continue the pipeline without re-asking for config.

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

The velocitee core engines are open source under the [Apache License, Version 2.0](LICENSE).

- **Self-hosted use** — free, always.
- **Modifying, forking, redistributing, embedding** — go ahead.
- **Building a commercial product on top** — go ahead. Just keep the
  attribution and don't claim our patent rights stop you from competing
  with us.

The velocit.ee SaaS layer (AI config generator, hosted registry, drift
alerts) is a separate proprietary service built on top of these engines.
That code lives elsewhere. The engines stay open.
