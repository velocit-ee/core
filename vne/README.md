# VNE — velocitee network configuration engine

[![License: AGPL v3](https://img.shields.io/badge/license-AGPL%20v3-blue.svg)](../LICENSE)

**Phase 2 of the [velocit.ee](https://velocit.ee) engine stack. Takes a
provisioned Proxmox host (the output of VME) and turns it into a fully
configured network — OPNsense VM, VLANs, DHCP, DNS, and a firewall baseline —
without the operator ever touching HCL, playbooks, or `config.xml`.**

---

## What it does

1. Reads the handoff manifest written by VME — picks up the Proxmox host's IP and SSH info.
2. Parses `velocitee.yml` for the user's declarative network intent.
3. Hands that intent to whichever provisioner the user picked
   (`velocitee-native`, `opentofu+ansible`, …).
4. Drives the provisioner end-to-end: VM creation on Proxmox, OPNsense
   first-boot config, VLANs, DHCP, DNS, firewall.
5. Runs a verification gate — only writes its handoff manifest if every check
   passes.

VNE is fully **idempotent** — re-running against an already-configured network
is safe. State persists in `vne/state/vne.state.json`; interrupted deployments
resume from the last successful step.

---

## Quick start

```bash
# Set the credentials VNE needs. NEVER put these in velocitee.yml.
export PROXMOX_VE_ENDPOINT="https://proxmox.lab:8006"
export PROXMOX_VE_API_TOKEN="root@pam!vne=...secret..."
export OPNSENSE_ROOT_PASSWORD="..."

vne deploy \
  --config velocitee.yml \
  --manifest /path/to/vme-manifest.json
```

---

## velocitee.yml — the VNE block

```yaml
velocitee:
  provisioner: "velocitee-native"   # or: opentofu+ansible

vne:
  wan_interface: "ens18"
  lan_interface: "ens19"
  opnsense_version: "24.7"
  opnsense_iso_url: "https://mirror.example/opnsense-24.7-dvd.iso"
  opnsense_iso_checksum: "sha256:abcdef..."
  vlans:
    - id: 10
      name: "servers"
      cidr: "10.10.10.0/24"
      dhcp_start: "10.10.10.100"
      dhcp_end:   "10.10.10.200"
    - id: 20
      name: "clients"
      cidr: "10.10.20.0/24"
      dhcp_start: "10.10.20.100"
      dhcp_end:   "10.10.20.200"
  dns:
    upstream:
      - "1.1.1.1"
      - "9.9.9.9"
    domain: "lab.local"
  firewall:
    default_policy: "block"
    allow_rules:
      - description: "LAN to internet"
        src_vlan: 20
        dst: "any"
        proto: "any"
        action: "allow"
  opnsense_vm:
    vmid: 100
    cores: 2
    memory_mb: 2048
    disk_gb: 20
    storage_pool: "local-lvm"
```

The schema is enforced by Pydantic — typos and out-of-range values fail with
a human-readable error before VNE talks to any API.

---

## Provisioner backends

| Backend            | Status        | Notes |
|--------------------|---------------|-------|
| `velocitee-native` | **Primary**   | Pure Python, talks Proxmox + OPNsense REST directly. Owns its own state file. |
| `opentofu+ansible` | Implemented   | OpenTofu does VM creation, Ansible does network config. Hard phase gate between the two. |
| `ansible-only`     | Stub          | Reserved name; not implemented. |
| `pulumi`           | Stub          | Reserved name; not implemented. |
| `salt`, `chef`, `puppet`, `cloudformation`, `bicep`, `nix`, `cloud-init`, `helm`, `packer` | Stubs | Reserved names. |

Adding a new backend = subclass `shared.renderer.Renderer`, register it in
`shared/renderer_registry.py`. No core changes.

---

## Required environment variables

| Backend            | Required env vars |
|--------------------|--------------------|
| `velocitee-native` | `PROXMOX_VE_ENDPOINT`, `PROXMOX_VE_API_TOKEN`, `OPNSENSE_ROOT_PASSWORD` |
| `opentofu`         | `PROXMOX_VE_ENDPOINT`, `PROXMOX_VE_API_TOKEN` |
| `ansible`          | `OPNSENSE_API_KEY`, `OPNSENSE_API_SECRET` |
| `opentofu+ansible` | All of the above |

Optional: `PROXMOX_VE_INSECURE=1` to skip TLS verification (homelabs with
self-signed certs); `OPNSENSE_INSECURE=1` likewise. Pre-flight checks every
required variable in one pass and prints all misses at once.

---

## Verification gate

After every backend, VNE runs four checks in order:

1. OPNsense API reachable (HTTPS 200).
2. DNS resolution working — resolves a known external hostname through OPNsense's Unbound.
3. Internet egress — TCP-connect to a known anycast IP. We don't ping (often blocked).
4. VLAN interfaces present and enabled.

If any check fails, **VNE does not write its output manifest**. The system is
left as-is (don't tear down on a check failure — re-running is idempotent and
the user usually wants to inspect). Resolve the failure, re-run, succeed.

---

## State and resume

`vne/state/vne.state.json` records every step's outcome. On resume:

- Completed steps are skipped, but the live API is *also* probed — if a
  resource went missing between runs (someone deleted the VM by hand), VNE
  reconciles and re-creates it.
- Corrupted state files (JSON parse failure, schema mismatch) are a fatal
  error with a clear message — never silently overwritten.
- The state schema is versioned: bumping VNE may invalidate old state files,
  in which case the operator deletes the file (idempotency makes the re-run
  safe).

---

## Failure recovery

| Failure point            | What VNE does                                           | What you do                              |
|--------------------------|---------------------------------------------------------|------------------------------------------|
| Phase 1 (VM creation)    | Records the failed step; subsequent phases skipped.     | Fix the cause, re-run. State resumes.    |
| Phase 2 (config)         | Records the failed step; phase 1 NOT re-run.           | Fix the cause, re-run.                   |
| Verification             | Output manifest NOT written. System untouched.          | Inspect what failed, re-run.             |
| State file corruption    | Hard fail with recovery instructions.                   | Inspect, then delete and re-run.         |

---

## Path B (join existing network)

Not implemented. `--join-existing` exits with code 1 and a clear message.

---

## Layout

```
vne/
  config.py                       # Pydantic models for velocitee.yml VNE block
  config_xml.py                   # OPNsense first-boot config.xml generator
  deploy.py                       # Entry point — `vne deploy …`
  schema/
    vme-manifest.schema.json      # What VNE expects from VME
    vne-manifest.schema.json      # What VNE writes for VSE
  state/                          # vne.state.json (gitignored)
  output/                         # Final handoff manifest (gitignored)
  terraform/                      # Generated at runtime by OpenTofu renderer
  ansible/
    requirements.yml              # ansibleguy.opnsense, pinned
    playbooks/configure-network.yml
    roles/opnsense-{base,vlans,dhcp,dns,firewall}/
  scripts/
    deploy.sh                     # Thin wrapper around deploy.py
    verify.py                     # The verification gate
```

Renderers themselves live in `shared/renderers/` and ship with the velocitee-shared
package — VNE imports them.

---

## License

AGPL v3 — see [LICENSE](LICENSE).
