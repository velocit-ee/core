#!/usr/bin/env python3
"""
Telia Vector Edge Gateway Daemon — Powered by Velocitee
Part of the Velocitee Vector product line.
Carrier-grade edge orchestrator for Telia Business CPEs (Technicolor DGA4330 / Telia X2).
Exposes REST API for Lovable/custom frontends, handles iPXE boot chains,
and implements zero-trust hardware claim workflows.
"""

import http.server
import json
import os
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from security import generate_claim_token, verify_claim_token, record_audit_event, audit_log
from manifest_generator import build_velocitee_manifest

PORT = 8088
DAEMON_DIR = os.path.dirname(os.path.abspath(__file__))
TELIA_DIR = os.path.dirname(DAEMON_DIR)
ASSETS_DIR = os.path.join(TELIA_DIR, "assets")
TARGET_DIR = os.path.join(TELIA_DIR, "target")

# Initial state modeling Telia Eesti Business Router (Telia X2 / Technicolor DGA4330)
edge_state = {
    "gateway": {
        "brand": "Telia Eesti AS",
        "product_name": "Telia Vector (Telia X2 Edition)",
        "hardware_model": "Technicolor DGA4330 (Vantiva)",
        "serial_number": "CP2138RA49B",
        "subscription_id": "IN-884920-EE",
        "cpu_arch": "Broadcom BCM63138 (Dual-core ARM Cortex-A9 @ 1.0 GHz)",
        "ram_used_mb": 38,
        "ram_total_mb": 512,
        "flash_used_mb": 62,
        "flash_total_mb": 256,
        "firmware_version": "18.3.c.0454-Telia-Vector-v1.0",
        "uplink": {
            "type": "Telia Ärikiud (Dedicated Business Fiber)",
            "speed": "1000 / 1000 Mbps",
            "wan_ip": "194.126.101.42",
            "gateway": "194.126.101.1",
            "sla": "99.95% Gold with 4G LTE Failover"
        },
        "ports": {
            "LAN 1": {"status": "up", "speed": "1 Gbps", "role": "Office Network (VLAN 10)"},
            "LAN 2": {"status": "up", "speed": "1 Gbps", "role": "VoIP SIP Trunk (VLAN 20)"},
            "LAN 3": {"status": "down", "speed": "Auto", "role": "Guest Wi-Fi (VLAN 30)"},
            "LAN 4": {"status": "up", "speed": "1 Gbps", "role": "Dedicated Server Port (VLAN 999 Quarantine)"}
        },
        "compliance": {
            "gdpr_compliant": True,
            "nis2_certified": True,
            "data_boundary": "Estonia (Tallinn Edge)",
            "tpm_attestation": "TPM 2.0 Active",
            "luks2_encryption": "Enforced"
        },
        "product": {
            "name": "Vector",
            "full_name": "Velocitee Vector (Telia Carrier Edition)",
            "edition": "telia-vector-cpe",
            "engine": "Velocitee Metal Provisioning Engine (VME)",
            "version": "1.0.0-telia",
            "domain": "https://velocit.ee",
            "docs": "https://docs.velocit.ee/vme/"
        }
    },
    "nodes": [
        {
            "mac": "52:54:00:12:34:56",
            "current_ip": "192.168.100.50",
            "port": "LAN 4",
            "vlan": 999,
            "vlan_name": "Quarantine / Zero-Trust Bootstrap",
            "status": "quarantined",
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "hardware_spec": {
                "chassis": "Edge MicroServer 1U",
                "cpu": "Intel Xeon E-2314 / ARM64 Ampere",
                "memory_gb": 16,
                "storage": "512 GB NVMe (Encrypted)",
                "vendor": "Telia Certified Hardware"
            },
            "claim": {
                "claimed": False,
                "claimed_at": None,
                "claimed_by": None,
                "claim_token": None,
                "profile": "sovereign-legal-advisory"
            },
            "provisioning": {
                "progress": 0,
                "stage": "Waiting for hardware claim approval in Telia Iseteenindus",
                "started_at": None,
                "completed_at": None,
                "active_apps": []
            }
        }
    ]
}

def advance_provisioning_pipeline(mac: str):
    """Background simulator executing realistic deployment stages with strict state transitions."""
    node = next((n for n in edge_state["nodes"] if n["mac"].lower() == mac.lower()), None)
    if not node:
        return

    stages = [
        (15, "Claim token verified. Unlocking iPXE bootloader...", "INFO"),
        (30, "Target received DHCP lease on VLAN 999. Chainloading iPXE...", "INFO"),
        (50, "Streaming signed Linux kernel & initrd over TLS...", "INFO"),
        (70, "Partitioning /dev/nvme0n1 and initializing LUKS2 encryption...", "INFO"),
        (85, "Executing cloud-init: Deploying Nextcloud vault and PostgreSQL...", "INFO"),
        (95, "Configuring Telia Tallinn DC backup agent & sealing TPM2 keys...", "INFO"),
        (100, "Node active and sovereign. Migrating to Trusted Office LAN.", "INFO")
    ]

    for progress, stage_msg, severity in stages:
        time.sleep(3.0)
        node["provisioning"]["progress"] = progress
        node["provisioning"]["stage"] = stage_msg
        record_audit_event(
            event_type="PROVISIONING_PROGRESS",
            actor="velocitee_vector",
            target_mac=mac,
            details=f"[{progress}%] {stage_msg}",
            severity=severity
        )
        if progress == 100:
            node["status"] = "active"
            node["vlan"] = 10
            node["vlan_name"] = "Trusted Office Network"
            node["provisioning"]["completed_at"] = datetime.now(timezone.utc).isoformat()
            node["provisioning"]["active_apps"] = [
                {"name": "Nextcloud Legal Vault", "port": 8081, "status": "online"},
                {"name": "Encrypted Audit Database", "port": 5432, "status": "online"},
                {"name": "Telia DC Backup Agent", "port": 9100, "status": "synced"}
            ]

class EnterpriseEdgeHandler(http.server.SimpleHTTPRequestHandler):
    def send_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # ── REST API v1 ────────────────────────────────────────────────────────
        if path == "/api/v1/telemetry":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(edge_state["gateway"], indent=2).encode())
            return

        if path == "/api/v1/nodes":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(edge_state["nodes"], indent=2).encode())
            return

        if path == "/api/v1/audit-log":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(audit_log, indent=2).encode())
            return

        if path == "/api/v1/manifest":
            manifest = build_velocitee_manifest()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps(manifest, indent=2).encode())
            return

        # ── iPXE Dynamic Chainloader ──────────────────────────────────────────
        if path == "/boot.ipxe":
            mac = query.get("mac", ["52:54:00:12:34:56"])[0]
            token = query.get("token", [""])[0]
            node = next((n for n in edge_state["nodes"] if n["mac"].lower() == mac.lower()), None)

            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()

            # Security verification: Only release installer if claimed with valid token
            if not node or not node["claim"]["claimed"] or not verify_claim_token(mac, node["claim"]["profile"], token):
                script = f"""#!ipxe
echo ==============================================================================
echo   TELIA VECTOR GATEWAY -- POWERED BY VELOCITEE
echo   Device: {edge_state['gateway']['product_name']} ({edge_state['gateway']['hardware_model']})
echo   Port: LAN 4 (VLAN 999 Quarantine) · Node MAC: {mac}
echo ==============================================================================
echo
echo   [SECURITY ALERT] Device is quarantined in isolated provisioning network.
echo   This hardware is not yet claimed in the Telia Business Self-Service portal.
echo
echo   To claim this node, visit: http://192.168.100.1:8088
echo   Retrying authentication in 5 seconds...
sleep 5
chain http://192.168.100.1:8088/boot.ipxe?mac={mac}&token={token}
"""
                self.wfile.write(script.encode())
                return

            script = f"""#!ipxe
echo ==============================================================================
echo   TELIA VECTOR -- AUTHORIZED ZERO-TOUCH PROVISIONING
echo   Node MAC: {mac} · Profile: {node['claim']['profile']}
echo   Compliance: GDPR Article 32 · NIS2 Supply Chain Certified
echo ==============================================================================
echo
echo   [1/3] Loading signed kernel over secure edge channel...
kernel http://192.168.100.1:8088/boot/vmlinuz ip=dhcp autoinstall ds=nocloud-net;s=http://192.168.100.1:8088/cloud-init/ quiet ---
echo   [2/3] Loading secure initial ramdisk...
initrd http://192.168.100.1:8088/boot/initrd
echo   [3/3] Commencing zero-touch hardware provisioning...
boot
"""
            self.wfile.write(script.encode())
            return

        # ── Boot Assets & Cloud-Init ──────────────────────────────────────────
        if path.startswith("/boot/"):
            bname = os.path.basename(path)
            bpath = os.path.join(ASSETS_DIR, "boot", bname)
            if os.path.exists(bpath):
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(os.path.getsize(bpath)))
                self.end_headers()
                with open(bpath, "rb") as f:
                    while chunk := f.read(65536):
                        self.wfile.write(chunk)
                return

        if path.startswith("/cloud-init/"):
            fname = os.path.basename(path)
            fpath = os.path.join(TARGET_DIR, "cloud-init", fname)
            if os.path.exists(fpath):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                with open(fpath, "rb") as f:
                    self.wfile.write(f.read())
                return

        # ── Default Professional Web UI ───────────────────────────────────────
        if path in ["/", "/index.html"]:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(self.render_enterprise_ui().encode())
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/v1/nodes/") and path.endswith("/claim"):
            mac = path.split("/")[4]
            node = next((n for n in edge_state["nodes"] if n["mac"].lower() == mac.lower()), None)
            
            if not node:
                self.send_response(404)
                self.send_cors_headers()
                self.end_headers()
                self.wfile.write(json.dumps({"error": "Node not found"}).encode())
                return

            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length)) if length > 0 else {}
            profile = body.get("profile", "sovereign-legal-advisory")
            actor = body.get("operator", "kati@legalpartners.ee")

            token, expires_at = generate_claim_token(mac, profile)
            node["claim"]["claimed"] = True
            node["claim"]["claimed_at"] = datetime.now(timezone.utc).isoformat()
            node["claim"]["claimed_by"] = actor
            node["claim"]["claim_token"] = token
            node["claim"]["profile"] = profile
            node["status"] = "provisioning"
            node["provisioning"]["started_at"] = datetime.now(timezone.utc).isoformat()
            node["provisioning"]["stage"] = "Claim verified. Unlocking bootloader..."

            threading.Thread(target=advance_provisioning_pipeline, args=(mac,), daemon=True).start()

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_cors_headers()
            self.end_headers()
            self.wfile.write(json.dumps({
                "success": True,
                "product": "Telia Vector",
                "mac": mac,
                "claim_token": token,
                "expires_at": expires_at,
                "provisioning_status": "in_progress"
            }).encode())
            return

        self.send_response(404)
        self.send_cors_headers()
        self.end_headers()

    def render_enterprise_ui(self):
        return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Telia Vector — Managed On-Premises Edge Compute</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<!-- Web fonts are optional (G-04): loaded with media=print so a blocked or
     throttled network never blocks first paint. The CSS font stacks below
     fall back to system fonts when the request fails or times out. -->
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
<noscript><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"></noscript>
<style>
  :root {
    --telia-purple: #990AE3;
    --telia-purple-hover: #8208C2;
    --telia-dark: #0A0414;
    --telia-card: #130724;
    --telia-border: #260D45;
    --telia-text: #FFFFFF;
    --telia-muted: #9E8DB8;
    --velocitee-teal: #00E5FF;
    --accent-green: #00E676;
    --accent-orange: #FF9100;
    --font-sans: 'Plus Jakarta Sans', -apple-system, sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--telia-dark); color: var(--telia-text); font-family: var(--font-sans); padding: 32px; min-height: 100vh; line-height: 1.5; }
  .container { max-width: 1280px; margin: 0 auto; }
  
  /* Header */
  .header { display: flex; justify-content: space-between; align-items: center; padding-bottom: 24px; border-bottom: 1px solid var(--telia-border); margin-bottom: 32px; }
  .brand-group { display: flex; align-items: center; gap: 16px; }
  .telia-logo-pill { background: var(--telia-purple); color: #fff; font-weight: 800; font-size: 15px; padding: 8px 18px; border-radius: 8px; letter-spacing: 1.5px; }
  .brand-text h1 { font-size: 20px; font-weight: 800; display: flex; align-items: center; gap: 10px; }
  .brand-text p { font-size: 13px; color: var(--telia-muted); }
  .vector-badge { font-size: 11px; background: rgba(0, 229, 255, 0.15); border: 1px solid var(--velocitee-teal); color: var(--velocitee-teal); padding: 2px 8px; border-radius: 4px; font-weight: 700; }

  .partner-tag { background: rgba(0, 229, 255, 0.08); border: 1px solid var(--velocitee-teal); color: var(--velocitee-teal); font-size: 12px; font-weight: 700; padding: 7px 16px; border-radius: 20px; text-decoration: none; display: flex; align-items: center; gap: 6px; transition: all 0.2s ease; }
  .partner-tag:hover { background: rgba(0, 229, 255, 0.18); transform: translateY(-1px); }

  /* Main Grid */
  .layout-grid { display: grid; grid-template-columns: 360px 1fr; gap: 28px; }
  .card { background: var(--telia-card); border: 1px solid var(--telia-border); border-radius: 16px; padding: 24px; margin-bottom: 24px; }
  .card-title { font-size: 13px; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: var(--telia-muted); margin-bottom: 20px; display: flex; justify-content: space-between; align-items: center; }

  /* Metrics */
  .metric-row { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px; }
  .metric-label { color: var(--telia-muted); }
  .metric-value { font-weight: 600; font-family: var(--font-mono); }
  .pill-green { color: var(--accent-green); background: rgba(0, 230, 118, 0.1); padding: 2px 8px; border-radius: 4px; font-size: 11px; }

  /* Hero Card */
  .hero-card { border: 1px solid var(--telia-purple); background: linear-gradient(145deg, rgba(153, 10, 227, 0.18) 0%, rgba(19, 7, 36, 0.95) 100%); border-radius: 16px; padding: 28px; position: relative; overflow: hidden; }
  .hero-badge { display: inline-flex; align-items: center; gap: 8px; font-size: 12px; font-weight: 700; padding: 6px 14px; border-radius: 20px; text-transform: uppercase; letter-spacing: 0.5px; }
  .badge-quarantined { background: rgba(255, 145, 0, 0.15); border: 1px solid var(--accent-orange); color: var(--accent-orange); }
  .badge-active { background: rgba(0, 230, 118, 0.15); border: 1px solid var(--accent-green); color: var(--accent-green); }

  .hero-title { font-size: 24px; font-weight: 800; margin: 16px 0 6px 0; }
  .hero-desc { color: var(--telia-muted); font-size: 14px; max-width: 620px; margin-bottom: 24px; }

  .btn-claim { background: var(--telia-purple); color: #fff; border: none; font-size: 15px; font-weight: 700; padding: 14px 28px; border-radius: 10px; cursor: pointer; display: inline-flex; align-items: center; gap: 10px; transition: all 0.2s ease; box-shadow: 0 4px 20px rgba(153, 10, 227, 0.35); }
  .btn-claim:hover { background: var(--telia-purple-hover); transform: translateY(-2px); box-shadow: 0 6px 24px rgba(153, 10, 227, 0.5); }
  .btn-claim:disabled { opacity: 0.5; cursor: not-allowed; transform: none; }

  .progress-container { margin: 24px 0 12px 0; }
  .progress-bar-bg { background: rgba(255,255,255,0.08); border-radius: 8px; height: 10px; overflow: hidden; }
  .progress-fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--telia-purple), var(--accent-green)); transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1); }
  .progress-caption { display: flex; justify-content: space-between; font-size: 13px; color: var(--telia-muted); margin-top: 8px; font-family: var(--font-mono); }

  .console-box { background: #05020A; border: 1px solid #1E0933; border-radius: 12px; padding: 16px; font-family: var(--font-mono); font-size: 12px; color: #D6C8EB; height: 240px; overflow-y: auto; line-height: 1.6; }
  .log-line { border-bottom: 1px solid rgba(255,255,255,0.03); padding: 4px 0; }
  .log-time { color: var(--telia-muted); margin-right: 8px; }
  .log-badge { font-weight: 700; margin-right: 8px; font-size: 11px; }

  .success-panel { display: none; margin-top: 24px; background: rgba(0, 230, 118, 0.08); border: 1px solid var(--accent-green); border-radius: 12px; padding: 20px; }
  .app-link { display: inline-flex; align-items: center; gap: 8px; background: var(--accent-green); color: #000; font-weight: 700; font-size: 14px; padding: 10px 20px; border-radius: 8px; text-decoration: none; margin-top: 14px; transition: transform 0.2s ease; }
  .app-link:hover { transform: translateY(-1px); }
</style>
</head>
<body>
<div class="container">

  <!-- Header -->
  <header class="header">
    <div class="brand-group">
      <div class="telia-logo-pill">TELIA</div>
      <div class="brand-text">
        <h1>Telia Vector <span class="vector-badge">EDGE COMPUTE</span></h1>
        <p>Enterprise On-Premises Server Appliance · Telia X2 (Technicolor DGA4330)</p>
      </div>
    </div>
    <div style="display:flex; gap:14px; align-items:center;">
      <a href="https://velocit.ee" target="_blank" class="partner-tag">
        <span>⚡ Powered by Velocit.ee</span>
      </a>
      <a href="/api/v1/manifest" target="_blank" style="font-size:12px; color:var(--telia-muted); text-decoration:none; border:1px solid var(--telia-border); padding:6px 12px; border-radius:6px;">
        📄 VME Manifest
      </a>
    </div>
  </header>

  <!-- Content Grid -->
  <div class="layout-grid">
    
    <!-- Left Column: CPE Hardware & Compliance -->
    <div>
      <div class="card">
        <div class="card-title">CPE Router Telemetry <span class="pill-green">● ONLINE</span></div>
        <div class="metric-row"><span class="metric-label">Device Model</span><span class="metric-value">Technicolor DGA4330</span></div>
        <div class="metric-row"><span class="metric-label">Product Edition</span><span class="metric-value">Telia Vector (X2 Edition)</span></div>
        <div class="metric-row"><span class="metric-label">RAM Allocation</span><span class="metric-value" style="color:var(--accent-green);">38 MB / 512 MB</span></div>
        <div class="metric-row"><span class="metric-label">NAND Flash</span><span class="metric-value">62 MB / 256 MB</span></div>
        <div class="metric-row"><span class="metric-label">Uplink</span><span class="metric-value">1000/1000 Ärikiud</span></div>
        <div class="metric-row"><span class="metric-label">Static Subnet</span><span class="metric-value">194.126.101.40/29</span></div>
      </div>

      <div class="card">
        <div class="card-title">Port Allocation & Zero Trust</div>
        <div class="metric-row"><span class="metric-label">LAN 1 (Office LAN)</span><span class="metric-value">VLAN 10 · 1 Gbps</span></div>
        <div class="metric-row"><span class="metric-label">LAN 2 (VoIP SIP)</span><span class="metric-value">VLAN 20 · 1 Gbps</span></div>
        <div class="metric-row"><span class="metric-label">LAN 3 (Guest Wi-Fi)</span><span class="metric-value" style="color:var(--telia-muted);">Inactive</span></div>
        <div class="metric-row"><span class="metric-label">LAN 4 (Vector Port)</span><span class="metric-value" style="color:var(--accent-orange);">VLAN 999 Quarantine</span></div>
      </div>

      <div class="card">
        <div class="card-title">Compliance & Sovereignty</div>
        <div class="metric-row"><span class="metric-label">Data Boundary</span><span class="metric-value" style="color:var(--accent-green);">EE-Tallinn On-Prem</span></div>
        <div class="metric-row"><span class="metric-label">EU NIS2 Directive</span><span class="metric-value">Supply Chain Verified</span></div>
        <div class="metric-row"><span class="metric-label">GDPR Art. 32</span><span class="metric-value">LUKS2 Encryption</span></div>
        <div class="metric-row"><span class="metric-label">Offsite Snapshot</span><span class="metric-value">Telia Tallinn Tier-3 DC</span></div>
      </div>
    </div>

    <!-- Right Column: Zero-Touch Claim Controller & Logs -->
    <div>
      <div class="hero-card">
        <div style="display:flex; justify-content:space-between; align-items:flex-start;">
          <div id="node-badge" class="hero-badge badge-quarantined">● Quarantined in VLAN 999</div>
          <div style="font-size:12px; font-family:var(--font-mono); color:var(--telia-muted);">Port 4 · 52:54:00:12:34:56</div>
        </div>

        <h2 class="hero-title">New Sovereign Server Detected</h2>
        <p class="hero-desc">An unprovisioned server node was detected on dedicated Vector Port 4. Isolated from the corporate network until authenticated by an authorized Telia business administrator.</p>

        <div style="display:flex; gap:16px; align-items:center;">
          <button id="btn-claim" class="btn-claim" onclick="triggerClaim()">
            <span>🛡️ Authenticate & Zero-Touch Deploy</span>
          </button>
        </div>

        <div class="progress-container">
          <div class="progress-bar-bg">
            <div id="progress-fill" class="progress-fill"></div>
          </div>
          <div class="progress-caption">
            <span id="stage-text">Awaiting administrative claim token...</span>
            <span id="progress-pct">0%</span>
          </div>
        </div>

        <div id="success-panel" class="success-panel">
          <div style="font-weight:700; color:var(--accent-green); font-size:16px; margin-bottom:4px;">✅ Sovereign Server Successfully Provisioned!</div>
          <div style="font-size:13px; color:#E2E8F0; line-height:1.5;">The node has been migrated from Quarantine VLAN 999 to the Trusted Office Network. Full-disk encryption initialized with TPM 2.0 attestation.</div>
          <a href="http://localhost:8081" target="_blank" class="app-link">
            <span>Open Sovereign Legal Workspace →</span>
          </a>
        </div>
      </div>

      <!-- Security Audit Trail -->
      <div class="card" style="margin-top:24px;">
        <div class="card-title">Security & Provisioning Audit Trail (RFC 5424) <span style="font-size:11px; color:var(--telia-muted);">Tamper-evident</span></div>
        <div id="console-box" class="console-box"></div>
      </div>
    </div>

  </div>
</div>

<script>
async function pollState() {
  try {
    const res = await fetch('/api/v1/nodes');
    const nodes = await res.json();
    const node = nodes[0];

    const auditRes = await fetch('/api/v1/audit-log');
    const logs = await auditRes.json();

    document.getElementById('progress-fill').style.width = node.provisioning.progress + '%';
    document.getElementById('progress-pct').innerText = node.provisioning.progress + '%';
    document.getElementById('stage-text').innerText = node.provisioning.stage;

    if (node.claim.claimed) {
      const btn = document.getElementById('btn-claim');
      btn.disabled = true;
      btn.innerHTML = '<span>⚡ Provisioning Active...</span>';
    }

    if (node.status === 'active') {
      const badge = document.getElementById('node-badge');
      badge.className = 'hero-badge badge-active';
      badge.innerText = '● Operational & Sovereign (VLAN 10)';
      document.getElementById('btn-claim').style.display = 'none';
      document.getElementById('success-panel').style.display = 'block';
    }

    const consoleBox = document.getElementById('console-box');
    consoleBox.innerHTML = logs.map(l => 
      `<div class="log-line"><span class="log-time">${l.timestamp.slice(11, 19)}</span><span class="log-badge" style="color:${l.severity === 'ALERT' ? '#FF5252' : l.severity === 'WARN' ? '#FFB300' : '#00E676'};">[${l.event_type}]</span>${l.details}</div>`
    ).join('');
    consoleBox.scrollTop = consoleBox.scrollHeight;
  } catch (e) {
    console.error(e);
  }
}

async function triggerClaim() {
  const btn = document.getElementById('btn-claim');
  btn.disabled = true;
  btn.innerText = 'Issuing Cryptographic Claim Token...';
  await fetch('/api/v1/nodes/52:54:00:12:34:56/claim', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ profile: 'sovereign-legal-advisory', operator: 'kati@legalpartners.ee' })
  });
  pollState();
}

setInterval(pollState, 1500);
pollState();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    record_audit_event("VECTOR_DAEMON_START", "system", "router_cpe", f"Telia Vector edge daemon v1.0.0 initialized on port {PORT}")
    print(f"Telia Vector Daemon starting on port {PORT}...")
    with socketserver.TCPServer(("", PORT), EnterpriseEdgeHandler) as httpd:
        httpd.serve_forever()
