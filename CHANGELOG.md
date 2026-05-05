# Changelog

All notable changes to velocitee core are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Added — nmap enrichment, MAAS backend, retry/structured-logging foundation

**`shared/discovery/nmap_probe.py`** — optional nmap enrichment for the
discovery toolkit
- Subprocess invocation of `nmap -oX -`; XML parsed with stdlib `xml.etree`
- Service-version detection (`-sV`) + best-effort OS family fingerprinting
  (`-O`, root only); CPE strings on each enriched service
- `--use-nmap auto|on|off` flag on `velocitee-discover scan` and
  `vne join` (default `auto` — used if `nmap` is on PATH)
- Strict license boundary documented: subprocess only, never imports a
  Python wrapper (`python-nmap`/`python-libmaas` are GPL v2 and would
  conflict with Apache 2.0)
- `Service.nmap_product/nmap_version/nmap_extrainfo/nmap_cpe` and
  `Host.os_guesses` populated when enrichment runs; `nmap_enrichment`
  capability flag added to the report

**VME — MAAS backend** *(new)*
- `vme/backends/` package: `Backend` ABC + slug registry mirroring the
  renderer pattern; `BuiltinBackend` placeholder + `MAASBackend` REST
  client implementation
- `vme deploy` dispatches on the new `backend:` field in
  `vme-config.yml` (default `builtin` — zero behaviour change for
  existing users); `backend: maas` hands off to a Canonical MAAS
  region+rack controller via `MAAS_URL` + `MAAS_API_KEY`
- OAuth 1.0 PLAINTEXT signer hand-rolled (no `python-libmaas` import to
  avoid LGPL friction); MAAS REST client uses `requests` directly
- `_maas_distro_series` translates VME's `target.os` slugs into MAAS
  distro codenames (jammy/noble); falls back to passthrough for custom
  MAAS images
- 14 unit tests for registry + OAuth + URL canonicalisation +
  distro-series mapping

**`shared._retry` + tenacity-backed retry policy**
- New `TransientAPIError` marker class + `transient_retry()` decorator
  (3 attempts, exponential backoff capped at 8s, retries only on the
  marker class — never on 4xx)
- OPNsense + Proxmox + MAAS clients all share the policy: 5xx and
  network errors retry transparently, user errors fail fast
- Replaces hand-rolled retry loop in `_opnsense_client.py`

**`shared.logging` + structlog stdlib bridge**
- `configure()` helper wires structlog into stdlib `logging` so existing
  `logging.getLogger(...)` calls keep working unchanged but emit
  structured output
- TTY autodetect — friendly key=value on a console, JSON for log
  shippers; `VELOCITEE_LOG_FORMAT` and `VELOCITEE_LOG_LEVEL` env-var
  overrides
- VNE deploy/join + `velocitee-discover` CLIs now call `configure()`
  instead of `logging.basicConfig`

**Repository tooling**
- `.pre-commit-config.yaml` — trailing-whitespace, end-of-file-fixer,
  check-yaml/json/toml, ruff + ruff-format, plus a local hook that
  validates every `*.schema.json` against Draft 7
- CONTRIBUTING.md gains a *Pre-commit hooks* section

**`velocitee-shared` 0.4.0** — adds `tenacity>=8.2` and `structlog>=24.0`
to the dependency floor; `vme` and `vne` bump their shared dep accordingly.

### Added — discovery toolkit & VNE Path B (join existing network)

**`shared/discovery/` — common scanning, documentation & diagnostics tool**
- `DiscoveryReport` Pydantic model (JSON + Markdown rendering) — single
  artifact consumed by VNE join, VSE inventory, and VLE drift baselines
- Local introspection (`network.py`): interfaces via `ip(8)` / sysfs,
  default gateway via `ip route` / `/proc/net/route`, DNS resolvers from
  `/etc/resolv.conf` (with systemd-resolved stub unwrap via `resolvectl`),
  DHCP lease parsing from dhclient + networkd
- Passive discovery (`passive.py`): kernel ARP/neighbor table, mDNS UDP/5353
  multicast listener, SSDP UDP/1900 with single M-SEARCH probe
- Active discovery (`active.py`): concurrent TCP-connect host sweep + port
  scan over a 40-port management/infra port set; ThreadPoolExecutor, no
  raw sockets, no scapy/nmap dep — works in unprivileged containers
- Service fingerprinting (`fingerprint.py`): SSH banner, HTTP `<title>` +
  `Server` header, HTTPS with TLS SAN extraction, optional SNMP v2c
  `sysDescr.0` (hand-rolled BER, no pysnmp)
- Router identification (`routers.py`): heuristic vendor detection with
  confidence score for OPNsense, pfSense, MikroTik, UniFi, EdgeOS, OpenWrt,
  Cisco IOS, FortiGate, Proxmox + a `generic-router` fallback
- Standalone CLI (`velocitee-discover scan|show`) writes paired
  `discovery-report.{json,md}` artifacts

**`shared/discovery/adapters/` — vendor-specific join glue**
- `RouterAdapter` ABC + slug registry, mirroring `Renderer`
- `unmanaged` (always available, vendor-agnostic) and `opnsense` (live
  state pull via existing `OPNsenseClient` when API creds present)
  implementations; stubs for pfsense / mikrotik / unifi / edgeos / openwrt
  / cisco / fortigate reserve the slugs

**VNE — Path B implemented**
- `vne join` command — scan ▸ identify ▸ confirm ▸ adapter execute ▸
  manifest write. Read-only end-to-end (never mutates the router)
- `vne/join.py` orchestrator; supports both `--manifest path/to/vme.json`
  (extending a VME run) and standalone (synthesises a minimal manifest
  from local hostname/IP)
- VNE manifest schema gains a `mode: "deploy" | "join"` discriminator;
  `joined_network` and `capabilities` blocks required when mode=join,
  `opnsense`/`vlans`/`dns`/`verification` required when mode=deploy
- Existing `vne deploy --join-existing` flag now forwards to `vne join`
  with a deprecation warning instead of exiting 1

**`velocitee-shared` 0.3.0** — packages discovery + adapters; bumps
`vne` dependency floor; adds `velocitee-discover` console script.

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
| Engine | Phase | Status  |
|--------|:-----:|---------|
| VME    | 1     | Stable  |
| VNE    | 2     | Stable  |
| VSE    | 3     | Planned |
| VLE    | 4     | Planned |

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
