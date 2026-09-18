# Velocitee Vector

> **Zero-touch bare-metal server provisioning at the network edge.**  
> Part of the [Velocitee](https://velocit.ee) infrastructure automation ecosystem.

---

## Overview

**Vector** is Velocitee's flagship commercial edge appliance product. It bridges the gap between raw bare-metal hardware and production-ready sovereign cloud infrastructure for small- and medium-sized enterprises (SMEs).

While public cloud hyperscalers offer ease-of-use at the expense of data sovereignty, latency, and unpredictable egress costs, traditional on-premises servers require specialized systems engineering that SMEs cannot afford.

**Vector solves this:**
1. **Zero-Touch Provisioning:** Unbox a certified node, connect it to the designated edge port, and the node self-configures over cryptographically authenticated iPXE.
2. **Sovereignty & Compliance:** 100% of primary client data remains legally anchored on local NVMe storage with automated LUKS2 disk encryption (GDPR Art. 32 & NIS2 compliant).
3. **Carrier & Partner Editions:** Deploys as an ultra-lightweight micro-service (< 40 MB RAM) directly on ISP customer-premises equipment (CPE) or managed branch gateways.

---

## Product Editions

| Edition | Target Environment | Footprint | Primary Role |
| :--- | :--- | :--- | :--- |
| **Telia Vector** (`vector/telia`) | Telia X2 (Technicolor DGA4330) & Telia F1 | < 45 MB RAM, < 40 MB flash | Carrier-grade edge gateway with quarantine VLAN & cryptographic claim tokens |
| **Vector Standalone** | Enterprise x86/ARM Bare-Metal & KVM | Standard Linux runtime | Direct on-premises cluster orchestration |

---

## Repository Structure

```
vector/
├── README.md               # Product overview & architecture
└── telia/                  # Telia Carrier Edition (Telia Vector)
    ├── daemon/             # Lightweight edge daemon, security, & REST API
    │   ├── agent.py        # Main orchestrator & HTTP daemon (Port 8088)
    │   ├── security.py     # HMAC-SHA256 claim token & RFC 5424 audit logger
    │   ├── manifest_generator.py # Canonical Velocitee handoff manifest
    │   └── dnsmasq.conf    # Hardened DHCP & TFTP configuration
    ├── target/             # Target node payloads & cloud-init
    │   ├── sovereign_hub.py# Live legal workspace web service (Port 8081)
    │   └── cloud-init/     # Automated OS partitioning & app provisioning
    ├── assets/             # Static bootloaders (iPXE) & streaming netboot kernel
    └── scripts/            # One-click demo & validation harnesses
        └── demo.sh         # Interactive demo runner for presentations
```

---

## Quick Start (Demo Mode)

```bash
# From the repository root:
./vector/telia/scripts/demo.sh
```

Opens the Telia Vector Edge Gateway portal at `http://localhost:8088`, ready to authenticate and provision a local server node.
