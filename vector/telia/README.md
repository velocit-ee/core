# Telia Vector (Carrier Edge Edition)

> **Managed on-premises sovereign server compute for Nordic & Baltic businesses.**  
> Delivered in commercial partnership between **Telia** and **Velocit.ee**.

---

## The Challenge & Opportunity

Nordic and Baltic SMEs face strict European data protection obligations (GDPR, EU NIS2 Directive). Law practices, financial advisors, and medical clinics cannot legally store unencrypted or offshore copies of sensitive client records. Yet, installing on-premises servers requires €150/hour sysadmins that SMEs lack.

**Telia Vector** transforms the existing **Telia X2 (Technicolor DGA4330)** and **Telia F1** broadband routers already installed in SME offices into zero-touch edge provisioning gateways.

### Value to Telia (Business Guru Track)
* **ARPU Uplift:** Elevates standard €40/month broadband lines into €120/month managed sovereign edge subscriptions.
* **0% Churn Moat:** Critical business files and local databases run on the provisioned hardware, making switching ISPs operationally unthinkable.
* **Zero R&D Overhead:** Velocitee provides the software engine under an ISV revenue-share model; Telia acts as the distribution channel.

---

## Technical Specifications

* **Target Hardware:** Technicolor DGA4330 / Telia X2 (Broadcom Dual-core ARM Cortex-A9 @ 1.0 GHz, 512 MB RAM, 256 MB Flash).
* **Memory Utilization:** Under **40 MB RAM** at steady state.
* **Storage Footprint:** Under **40 MB total** (zero multi-gigabyte ISO storage on router flash; streaming netboot over TLS).
* **Port Security:** Dedicated Server Port (LAN 4) mapped to untagged **VLAN 999 (Quarantine Network)**.
* **Authentication:** **HMAC-SHA256 Cryptographic Claim Tokens** (5-minute TTL). Rogue machines cannot boot or access office LANs.
* **Auditability:** Tamper-evident structured audit logging conforming to **RFC 5424** / CEF standards.

---

## Running the Demo

```bash
./scripts/demo.sh
```

* **Telia Vector Portal:** `http://localhost:8088`
* **Provisioned Sovereign Hub:** `http://localhost:8081`
* **Velocitee Manifest Output:** `http://localhost:8088/api/v1/manifest`
