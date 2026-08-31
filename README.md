# Velocitee Vector

[![CI](https://github.com/velocit-ee/core/actions/workflows/ci.yml/badge.svg)](https://github.com/velocit-ee/core/actions/workflows/ci.yml)
[![Latest Release](https://img.shields.io/github/v/release/velocit-ee/core?label=release)](https://github.com/velocit-ee/core/releases)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/)

**The open-source engine stack behind [velocit.ee](https://velocit.ee). Takes hardware from bare metal to a documented, running infrastructure stack — engine by engine.**

---

## What it does

Velocitee Vector is a pipeline of four independent engines. Each one does one thing, writes a structured handoff manifest, and passes it to the next. Enter the pipeline wherever your hardware already is.

```
bare metal ──► VME ──► VNE ──► VSE ──► VLE ──► documented, running stack
               │        │       │        │
            provision  network  services  lifecycle
```

<!-- ENGINE-STATUS:BEGIN region=engine-table -->
| Engine  | Phase | Status  | Description |
|---------|:-----:|---------|-------------|
| **VME** | 1     | Alpha   | Bare-metal provisioning — PXE boot + unattended OS install (Proxmox VE, Ubuntu Server). Two backends: `builtin` seed stack or `maas` (optional). |
| **VNE** | 2     | Alpha   | Network configuration — OPNsense VM, VLANs, DHCP, DNS, firewall. Two working backends via the renderer registry. Discovery + Path B (`vne join`) for existing networks. |
| VSE     | 3     | Planned | Services — containerised stack deployment, idempotent configuration |
| VLE     | 4     | Planned | Lifecycle — monitoring, drift detection, auto-docs, single-command repair |
<!-- ENGINE-STATUS:END region=engine-table -->

Each engine is independently useful. You don't need the full pipeline to get value from VME.

---

## Project status — read this first

Velocitee Vector is at `0.1.0` and both implemented engines are **alpha**. That
label is chosen deliberately, and it means something specific here:

| | |
|---|---|
| **Tests** | 356 passing |
| **Line coverage** | 63% overall |
| **Covered** | config parsing and validation, JSON-Schema manifest handling, the OS image registry, preflight checks, backend selection, OPNsense XML generation, the OpenTofu renderer, the pipeline state machine, discovery reporting, the OPNsense and Proxmox REST clients, the verification gate, `vne deploy`'s refusal paths, `vne join` end-to-end, the provisioner registry, and log configuration |
| **Not covered** | most of the VME command surface (`vme deploy` drives Docker, PXE and real hardware), the `vme setup` wizard, `velocitee-native`'s execution path, and the raw-socket half of discovery — passive listening, active scanning, and OS fingerprinting |

What is left uncovered is the code that cannot run without hardware, a
hypervisor, or a live network. That is a real limit, not an excuse: those paths
have been exercised by hand, which is evidence, not a guarantee. Everything
that could be tested without a lab now is.

Run `python -m pytest --cov=shared --cov=vme --cov=vne` to reproduce those
numbers yourself rather than taking this table's word for it.

Two further things worth knowing before you rely on this:

- **The renderer registry lists more backends than exist.** `velocitee-native`
  and `opentofu+ansible` are implemented. Pulumi, Salt, Chef, Puppet,
  CloudFormation, Bicep, Nix, Helm, Packer and ansible-only are registered
  names that pass config validation and then refuse to run. They are reserved
  slots, not options.
- **VSE and VLE do not exist yet.** There is no code for them in this
  repository. The pipeline diagram shows the intended shape, not shipped
  software.

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

- a local file (`velocitee.yml`, or a per-engine one like `vme-config.yml`)
- a git repository the engine pulls from

A hosted config registry is intended as part of a future paid tier. It does not
exist yet.

---

## Development

```bash
git clone https://github.com/velocit-ee/core
cd core
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'   # installs shared + vme + vne as one distribution
python -m pytest          # runs every engine's suite from the repo root
```

The whole stack is a single installable package (`pip install .` /
`pipx install velocitee`). The `vme`, `vne`, and `velocitee-discover`
commands come from `[project.scripts]` in the root `pyproject.toml`.

---

## Contributing

All contributors must sign the [Contributor License Agreement](CLA.md) before a pull request can be merged.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines and code standards.

---

## License

The Velocitee Vector engines are open source under the [Apache License, Version 2.0](LICENSE).

- **Self-hosted use** — free, always.
- **Modifying, forking, redistributing, embedding** — go ahead.
- **Building a commercial product on top** — go ahead. Just keep the
  attribution and don't claim our patent rights stop you from competing
  with us.

The long-term intention is open core: the engines stay Apache 2.0 forever, and
a hosted layer on top — config generation, a hosted registry, drift alerts — is
what gets charged for. **None of that exists yet.** There is no hosted service,
no account system, and nothing to buy.
