# Changelog

All notable changes to velocitee core are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
