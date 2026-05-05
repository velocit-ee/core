# shared.discovery — network scanning, documentation & diagnostics

Common tool used by **VNE** (Path B — join existing network), **VSE**
(service inventory baseline), and **VLE** (drift detection baseline).

One scan, one report. Downstream code reads the report — it does not
re-scan. The output is a strictly-validated `DiscoveryReport` (Pydantic +
JSON Schema-flavoured) plus a paired Markdown rendering for humans.

## Design constraints

- **Stdlib-only baseline.** The default scan path uses no third-party
  Python libraries (`requests` is optional, only for the OPNsense
  adapter). Works in unprivileged containers, dev machines, and CI.
- **nmap is an optional enrichment layer**, not a hard dependency. If the
  `nmap` binary is on PATH we use it for service-version + OS detection;
  if not, the stdlib scan still produces a useful report. See
  *nmap integration* below for the license boundary.
- **Failure-tolerant.** Multicast blocked? mDNS empty. No SNMP community?
  No SNMP. Every layer degrades gracefully — the report just records what
  was unavailable in `warnings`.
- **Vendor-agnostic by default.** Identification is heuristic and yields
  a confidence score; no caller is forced to act on a low-confidence match.
  The `unmanaged` adapter handles every gateway we don't recognise.

## nmap integration

When nmap is available, discovery shells out to the `nmap` binary (never
imports a Python wrapper) and parses `-oX -` XML output with stdlib
`xml.etree`. This adds:

- Real service-version detection (`-sV`) — far more accurate than our
  banner/title scraping
- OS family fingerprinting (`-O`) when running as root
- CPE strings on each service for inventory tools downstream

Control via `--use-nmap auto|on|off` (default `auto`):

- `auto` — use nmap if installed; otherwise silent fallback to stdlib
- `on`   — require nmap; warn loudly if missing
- `off`  — never invoke nmap even if installed

### License boundary

Nmap ships under the **Nmap Public Source License** — a modified GPL v2
with extra commercial-redistribution restrictions. Our integration:

1. Calls the `nmap` binary as a subprocess. Never imports a Python wrapper
   (`python-nmap` and `python-libnmap` are GPL v2; importing either would
   conflict with this package's Apache 2.0 license).
2. Self-hosted users install `nmap` themselves. velocitee-shared does not
   ship it.
3. The velocit.ee SaaS does **not** run nmap server-side. Doing so would
   require an Nmap OEM license and is intentionally out of scope today.

## What a scan produces

```
DiscoveryReport
├── network        local interfaces, default gateway, DNS, DHCP lease
├── router         identified vendor + confidence + evidence
├── hosts[]        observed hosts with services, banners, role hints
├── vlans[]        observed VLANs (currently from local subinterfaces)
├── capabilities[] flat yes/no flags VSE/VLE consume
└── warnings[]     anything that didn't work as expected
```

## CLI

```bash
# Default: scan the local subnet, write JSON + Markdown
velocitee-discover scan

# Explicit scope, explicit ports
velocitee-discover scan \
    --cidr 10.0.0.0/24 --cidr 10.10.0.0/24 \
    --ports 22,80,443,8443,8006 \
    --snmp-community public \
    --json out.json --md out.md

# Re-render an existing report
velocitee-discover show out.json
```

## Library

```python
from shared.discovery import run_discovery, render_markdown

report = run_discovery(cidrs=["192.168.1.0/24"])
print(report.to_json())
print(render_markdown(report))
```

## Router adapters

Adapters take a `DiscoveryReport` and produce the manifest fragment a join
operation writes for VSE/VLE.

| Adapter      | Status        | Source file                         |
|--------------|---------------|-------------------------------------|
| `unmanaged`  | Implemented   | `adapters/unmanaged.py`             |
| `opnsense`   | Implemented   | `adapters/opnsense.py`              |
| Others       | Stubs         | `adapters/_stubs.py` (slugs reserved) |

Add a new adapter: subclass `RouterAdapter`, register the slug. Same
pattern as `shared.renderer.Renderer`.
