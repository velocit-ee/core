# shared.discovery — network scanning, documentation & diagnostics

Common tool used by **VNE** (Path B — join existing network), **VSE**
(service inventory baseline), and **VLE** (drift detection baseline).

One scan, one report. Downstream code reads the report — it does not
re-scan. The output is a strictly-validated `DiscoveryReport` (Pydantic +
JSON Schema-flavoured) plus a paired Markdown rendering for humans.

## Design constraints

- **No nmap, no scapy, no raw sockets.** Pure stdlib + `requests`
  (optional, only for the OPNsense adapter). Works in unprivileged
  containers, dev machines, and CI.
- **Failure-tolerant.** Multicast blocked? mDNS empty. No SNMP community?
  No SNMP. Every layer degrades gracefully — the report just records what
  was unavailable in `warnings`.
- **Vendor-agnostic by default.** Identification is heuristic and yields
  a confidence score; no caller is forced to act on a low-confidence match.
  The `unmanaged` adapter handles every gateway we don't recognise.

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
