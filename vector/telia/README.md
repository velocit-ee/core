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

## Boot Assets

The kernel, initramfs and iPXE binaries under `assets/` are release assets and
are not tracked in git. `assets/SHA256SUMS` and `assets/PROVENANCE.md` are.
Fetch and verify them with:

```bash
./scripts/fetch-assets.sh            # from the GitHub release
./scripts/fetch-assets.sh --from-dir /path/to/mirror
./scripts/fetch-assets.sh --verify   # check what is already on disk
```

Any file whose checksum does not match the manifest is deleted, never served.
The demo does not need these files; only a real PXE boot does.

## Runtime Storage & Logging on the CPE

The Technicolor DGA4330 boots from raw NAND behind a small jffs2/overlay. To avoid
flash wear and a full overlay, the daemon and its helpers must not write to
persistent storage on the request path:

* **Logs:** dnsmasq logs to syslog (`log-facility=DAEMON`), which is the RAM ring
  buffer on Homeware/OpenWrt-class firmware (`logread`). The daemon logs to
  stderr, which procd routes to the same buffer.
* **Volatile state:** the DHCP lease file and any scratch state live under
  `/tmp/telia-vector/` (tmpfs). The init script must create that directory
  (mode `0750`) before starting dnsmasq.
* **Persistent writes:** exactly one, once — the device-unique HMAC secret at
  first boot. Nothing else touches the overlay.
* **Audit trail:** held in a bounded in-memory ring and forwarded over syslog;
  it is never appended to a file on the router.

## Demo Network Dependencies

None. Both services are local Python processes and the provisioning pipeline
is simulated in-process. Web fonts are loaded non-blocking with a system-font
fallback, so a venue network that throttles or blocks `fonts.googleapis.com`
does not delay first paint. The committed boot assets are an Alpine Linux
netboot initramfs and are not booted by the demo; a real netboot would fetch
`modloop` and packages from an Alpine mirror unless an `apkovl` and local
repository are bundled.

## Running the Demo

```bash
./scripts/demo.sh
```

* **Telia Vector Portal:** `http://localhost:8088`
* **Provisioned Sovereign Hub:** `http://localhost:8081`
* **Velocitee Manifest Output:** `http://localhost:8088/api/v1/manifest`
