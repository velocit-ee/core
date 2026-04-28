# Changelog

All notable changes to velocitee core are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Changed — relicense

- **Engines relicensed from AGPL v3 → Apache License, Version 2.0.** All five
  LICENSE files (root, vme, vne, vse, vle) replaced with the canonical Apache
  2.0 text. Apache 2.0 is permissive — fork, modify, redistribute, embed in
  proprietary products. The CLA we already had keeps the door open for
  velocitee to offer alternative licensing arrangements in the future.
- CLA updated to v2.0 — license-agnostic; references Apache 2.0 as the
  current OSS release; explicit patent grant.
- READMEs, pyproject `license` fields, marketing site copy, docs site, org
  profile README all updated for consistency.
- The "commercial license" pricing tier feature is dropped — Apache 2.0
  already permits commercial use; the enterprise tier now sells on
  private deployments, named support, training, and SLA.

### Added

**VNE — Network Configuration Engine** (initial implementation)
- `velocitee.yml` VNE-block parser (`vne/config.py`) with Pydantic validation, translated into a provisioner-agnostic `VNEIntent` schema
- `velocitee-native` provisioner — first-class Python backend that drives Proxmox VE and OPNsense REST APIs directly, with idempotency probes at every step and a versioned, resumable state file (`vne/state/vne.state.json`)
- OPNsense first-boot `config.xml` generator (`vne/config_xml.py`) — sets root password (SHA-512 crypt via passlib), assigns WAN/LAN, enables the REST API
- `opentofu+ansible` parallel backend — `OpenTofuRenderer` (pinned `bpg/proxmox` 0.66.2) creates infra and writes `infra_manifest.json`; `AnsibleRenderer` reads it and runs idempotent roles via the pinned `ansibleguy.opnsense` 1.30.1 collection. Hard phase gate: Phase 2 never runs after Phase 1 fails
- 11 stub renderers registered for completeness: `ansible-only`, `pulumi`, `salt`, `chef`, `puppet`, `cloudformation`, `bicep`, `nix`, `cloud-init`, `helm`, `packer`
- Verification gate (`vne/scripts/verify.py`) — checks API reachability, DNS resolution via OPNsense, internet egress (TCP not ICMP), and VLAN presence. Output manifest is not written unless every check passes
- Entry point `vne/deploy.py` + thin `scripts/deploy.sh` wrapper. Pre-flight reports all missing env vars in one error
- JSON Schema files for VNE input (VME manifest contract) and VNE output (consumed by VSE)

**shared — Engine runtime library** (extended)
- `shared/schema.py` — Pydantic models for the provisioner-agnostic internal schema (VLAN, DHCP, DNS, firewall, VM specs, `VNEIntent`)
- `shared/renderer.py` — `Renderer` ABC; phase-tagged (`infra` / `config` / `both`)
- `shared/pipeline.py` — phase-ordered orchestrator with hard gating between phases
- `shared/renderer_registry.py` — provisioner-name → renderer-class registry; lazy-loaded
- `shared/renderers/` — concrete renderer implementations and shared API clients (Proxmox, OPNsense)

### Engines
| Engine | Status |
|--------|--------|
| VME    | Active |
| VNE    | Active (initial) |
| VSE    | Planned — phase 3 |
| VLE    | Planned — phase 4 |

---

## [0.1.0] — 2026-04-24

Initial public release of the velocitee engine stack.

### Added

**VME — Metal Provisioning Engine**
- PXE boot pipeline: dnsmasq (DHCP/TFTP) + nginx (HTTP) + iPXE, fully containerised via Docker Compose
- Unattended OS install for Proxmox VE (kernel+initrd boot, live ISO fetch, initrd patching for NIC drivers and answer file embedding) and Ubuntu Server (cloud-init autoinstall)
- Guided setup wizard (`vme setup`) — detects network interfaces, writes `vme-config.yml` interactively
- Pre-flight checks (`vme preflight`) — Docker, interface, DHCP conflicts, TFTP port, disk space, firewall
- Filtered deploy log streaming — one line per milestone, full logs written to `~/.velocitee/logs/`
- OS image management (`vme images list/pull/clean`) with SHA-256 verification and resumable downloads
- `vme reset` with granular flags (`--images`, `--config-only`, `--full`)
- Handoff manifest written on success — structured JSON describing the provisioned machine for downstream engines

**shared — Engine runtime library**
- `shared/cli.py` — `make_app()`, `fatal()`, `warn()`, `run_app()`: clean CLI error handling for all engines (no Python tracebacks, typo suggestions on unknown commands)
- `shared/manifest.py` — manifest build, validation, write, load, and `append_engine()` for pipeline continuity
- `shared/schema.json` — JSON Schema v7 contract for the inter-engine handoff manifest

### Engines
| Engine | Status |
|--------|--------|
| VME    | Active |
| VNE    | Planned — phase 2 |
| VSE    | Planned — phase 3 |
| VLE    | Planned — phase 4 |

---

*Releases before v0.1.0 were internal development iterations.*
