#!/usr/bin/env python3
"""
Telia Sovereign Edge Hub — Target Node Service (Port 8081)
Simulates the live, operational business server after zero-touch provisioning.
Supports dual profiles:
  - Scenario-based: Sovereign Legal Advisory (Kati & Partners)
  - Universal / Plain: Clean Ubuntu 24.04 LTS Server with Docker + OpenSSH
"""

import http.server
import socketserver
from urllib.parse import parse_qs, urlparse

PORT = 8081
socketserver.TCPServer.allow_reuse_address = True

HTML_LEGAL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sovereign Legal Workspace #01 · Kati & Partners Advisory</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
<noscript><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"></noscript>
<style>
  :root {
    --bg: #07090E;
    --card: #0F141F;
    --card-accent: #141C2B;
    --border: #1E293B;
    --text: #F8FAFC;
    --muted: #94A3B8;
    --telia: #990AE3;
    --green: #10B981;
    --blue: #38BDF8;
    --font-sans: 'Plus Jakarta Sans', sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); padding: 36px; min-height: 100vh; }
  .container { max-width: 1140px; margin: 0 auto; }

  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }
  .brand-title h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .brand-title p { color: var(--muted); font-size: 13px; }
  .sovereign-tag { background: rgba(16, 185, 129, 0.12); color: var(--green); border: 1px solid var(--green); padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 12px; display: flex; align-items: center; gap: 6px; }

  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 28px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 20px; font-weight: 700; }

  .app-item { display: flex; align-items: center; justify-content: space-between; padding: 16px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; margin-bottom: 14px; }
  .app-left { display: flex; align-items: center; gap: 16px; }
  .app-icon { font-size: 26px; background: rgba(153, 10, 227, 0.15); border-radius: 10px; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; }
  .app-name { font-weight: 700; font-size: 15px; margin-bottom: 2px; }
  .app-desc { font-size: 12px; color: var(--muted); }
  .status-pill { font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: 700; }
  .status-online { background: rgba(16, 185, 129, 0.15); color: var(--green); }

  .row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px; }
  .row:last-child { border-bottom: none; }
  .row-label { color: var(--muted); }
  .row-val { font-family: var(--font-mono); font-weight: 600; }
</style>
</head>
<body>
<div class="container">
  <header class="header">
    <div class="brand-title">
      <h1>🏛️ Kati & Partners Advisory · Sovereign Node #01</h1>
      <p>Telia Managed Private Edge · Zero-Touch Provisioned Hardware</p>
    </div>
    <div class="sovereign-tag">
      <span>●</span> Data Resident: Estonia (Tallinn On-Prem)
    </div>
  </header>

  <div class="grid">
    <div>
      <div class="card">
        <div class="card-title">Active Sovereign Workspaces</div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🔒</div>
            <div>
              <div class="app-name">Nextcloud Legal Workspace</div>
              <div class="app-desc">Client contract repository & encrypted audit trail</div>
            </div>
          </div>
          <span class="status-pill status-online">Running</span>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🐘</div>
            <div>
              <div class="app-name">Encrypted PostgreSQL Database</div>
              <div class="app-desc">Direct hardware encryption on local NVMe disk</div>
            </div>
          </div>
          <span class="status-pill status-online">Protected</span>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">☁️</div>
            <div>
              <div class="app-name">Telia Cloud DC Backup Agent</div>
              <div class="app-desc">Encrypted incremental sync to Telia Tallinn DC</div>
            </div>
          </div>
          <span class="status-pill status-online">Synced</span>
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-title">Cryptographic Health & Attestation</div>
        <div class="row"><span class="row-label">Disk Encryption</span><span class="row-val" style="color:var(--green);">LUKS2 (AES-XTS 512)</span></div>
        <div class="row"><span class="row-label">Hardware Attestation</span><span class="row-val" style="color:var(--green);">TPM 2.0 PCR[7] Valid</span></div>
        <div class="row"><span class="row-label">GDPR Article 32</span><span class="row-val" style="color:var(--green);">Compliant</span></div>
        <div class="row"><span class="row-label">Local Edge IP</span><span class="row-val">192.168.100.50</span></div>
        <div class="row"><span class="row-label">Provisioned By</span><span class="row-val">Telia X2 Router (VLAN 10)</span></div>
        <div class="row"><span class="row-label">Engine Backend</span><span class="row-val"><a href="https://velocit.ee" target="_blank" style="color:#A855F7; text-decoration:none;">velocit.ee VME</a></span></div>
      </div>

      <div class="card">
        <div class="card-title">Universal Appliance Mode</div>
        <div style="font-size:13px; color:var(--muted); line-height:1.6; margin-bottom:12px;">
          View this edge node without the scenario bundle as a clean, general-purpose server:
        </div>
        <a href="/?profile=plain" style="display:block; text-align:center; background:#141C2B; border:1px solid #1E293B; color:#38BDF8; padding:10px; border-radius:8px; text-decoration:none; font-weight:700; font-size:12px;">
          Switch to Plain Ubuntu Server View →
        </a>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""

HTML_PLAIN = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Velocitee Vector Edge Node #01 · Plain Ubuntu 24.04 LTS</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
<noscript><link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;700&display=swap" rel="stylesheet"></noscript>
<style>
  :root {
    --bg: #07090E;
    --card: #0F141F;
    --card-accent: #141C2B;
    --border: #1E293B;
    --text: #F8FAFC;
    --muted: #94A3B8;
    --accent-blue: #38BDF8;
    --green: #10B981;
    --font-sans: 'Plus Jakarta Sans', sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: var(--bg); color: var(--text); font-family: var(--font-sans); padding: 36px; min-height: 100vh; }
  .container { max-width: 1140px; margin: 0 auto; }
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }
  .brand-title h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .brand-title p { color: var(--muted); font-size: 13px; }
  .node-tag { background: rgba(56, 189, 248, 0.12); color: var(--accent-blue); border: 1px solid var(--accent-blue); padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 12px; display: flex; align-items: center; gap: 6px; }
  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 28px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 20px; font-weight: 700; }
  .app-item { display: flex; align-items: center; justify-content: space-between; padding: 16px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; margin-bottom: 14px; }
  .app-left { display: flex; align-items: center; gap: 16px; }
  .app-icon { font-size: 22px; background: rgba(56, 189, 248, 0.15); border-radius: 10px; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; }
  .app-name { font-weight: 700; font-size: 15px; margin-bottom: 2px; }
  .app-desc { font-size: 12px; color: var(--muted); }
  .status-pill { font-size: 11px; padding: 4px 10px; border-radius: 12px; font-weight: 700; }
  .status-active { background: rgba(16, 185, 129, 0.15); color: var(--green); }
  .row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px; }
  .row:last-child { border-bottom: none; }
  .row-label { color: var(--muted); }
  .row-val { font-family: var(--font-mono); font-weight: 600; }
  .cmd-box { background: #030712; border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; font-family: var(--font-mono); font-size: 13px; color: #38BDF8; margin: 12px 0; user-select: all; }
</style>
</head>
<body>
<div class="container">
  <header class="header">
    <div class="brand-title">
      <h1>⚡ Velocitee Vector Edge Node #01</h1>
      <p>Plain Ubuntu Server 24.04 LTS (Noble Numbat) · Hardened Bare-Metal Instance</p>
    </div>
    <div class="node-tag">
      <span>●</span> Active · Trusted LAN (192.168.100.50)
    </div>
  </header>

  <div class="grid">
    <div>
      <div class="card">
        <div class="card-title">Provisioned Base Runtime Services</div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🐳</div>
            <div>
              <div class="app-name">Docker Engine 26.1 & containerd</div>
              <div class="app-desc">Container runtime active · Unix socket /var/run/docker.sock ready</div>
            </div>
          </div>
          <span class="status-pill status-active">Active</span>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🔑</div>
            <div>
              <div class="app-name">OpenSSH Server 9.6p1</div>
              <div class="app-desc">Port 22 · Ed25519 public key auth enforced · Password auth disabled</div>
            </div>
          </div>
          <span class="status-pill status-active">Listening</span>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">📊</div>
            <div>
              <div class="app-name">Prometheus Node Exporter</div>
              <div class="app-desc">Port 9100 · System metrics scraping endpoint active</div>
            </div>
          </div>
          <span class="status-pill status-active">Streaming</span>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🛡️</div>
            <div>
              <div class="app-name">UFW Hardened Firewall</div>
              <div class="app-desc">VLAN 10 routing enforced · Ports 22, 80, 443, 9100 allowed</div>
            </div>
          </div>
          <span class="status-pill status-active">Enforced</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Universal Custom Workload Slot</div>
        <div style="font-size:13px; color:#E2E8F0; line-height:1.6; margin-bottom:12px;">
          This edge node was provisioned with zero opinionated application payloads. Any developer or enterprise workload can be launched immediately over SSH or Docker Compose.
        </div>
        <div class="cmd-box">ssh -i ~/.ssh/id_ed25519 ubuntu@192.168.100.50</div>
        <div style="font-size:12px; color:var(--muted); margin-top:8px;">
          Example deployments: <code>docker run -d -p 11434:11434 ollama/ollama</code> (Local AI) or <code>curl -sfL https://get.k3s.io | sh -</code> (Lightweight Kubernetes).
        </div>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-title">Hardware & Cryptographic Health</div>
        <div class="row"><span class="row-label">OS Platform</span><span class="row-val">Ubuntu 24.04 LTS</span></div>
        <div class="row"><span class="row-label">Linux Kernel</span><span class="row-val">6.8.0-40-generic</span></div>
        <div class="row"><span class="row-label">Architecture</span><span class="row-val">x86_64 / aarch64</span></div>
        <div class="row"><span class="row-label">Disk Encryption</span><span class="row-val" style="color:var(--green);">LUKS2 (AES-XTS-512)</span></div>
        <div class="row"><span class="row-label">Hardware Root</span><span class="row-val" style="color:var(--green);">TPM 2.0 PCR[7] Sealed</span></div>
        <div class="row"><span class="row-label">Memory Allocation</span><span class="row-val">0.8 / 16.0 GB RAM</span></div>
        <div class="row"><span class="row-label">Orchestrator</span><span class="row-val"><a href="https://velocit.ee" target="_blank" style="color:#38BDF8; text-decoration:none;">Velocitee Vector</a></span></div>
      </div>

      <div class="card">
        <div class="card-title">Scenario Demonstration</div>
        <div style="font-size:13px; color:var(--muted); line-height:1.6; margin-bottom:12px;">
          View the Telia Challenge scenario-based demonstration:
        </div>
        <a href="/?profile=legal" style="display:block; text-align:center; background:#141C2B; border:1px solid #1E293B; color:#A855F7; padding:10px; border-radius:8px; text-decoration:none; font-weight:700; font-size:12px;">
          Switch to Legal Advisory Scenario →
        </a>
      </div>
    </div>
  </div>
</div>
</body>
</html>
"""

class HubHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        profile = query.get("profile", ["legal"])[0]

        print(f"  \033[1;32m[SERVER-NODE]\033[0m HTTP GET {self.path} (Profile: {profile}) from {self.client_address[0]} (200 OK)", flush=True)
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()

        if profile == "plain":
            self.wfile.write(HTML_PLAIN.encode())
        else:
            self.wfile.write(HTML_LEGAL.encode())

    def log_message(self, format, *args):
        pass

if __name__ == "__main__":
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    http.server.ThreadingHTTPServer.daemon_threads = True
    print("""
\033[1;32m╔══════════════════════════════════════════════════════════════════════╗\033[0m
\033[1;32m║   TELIA SOVEREIGN SERVER NODE -- LIVE TARGET CONSOLE (TTY1)          ║\033[0m
\033[1;32m║   Status: Operational & Sovereign · Port 8081                        ║\033[0m
\033[1;32m║   Modes: Scenario (Legal Advisory) & Universal (Plain Ubuntu Server) ║\033[0m
\033[1;32m╚══════════════════════════════════════════════════════════════════════╝\033[0m
  [✓] Chassis:         1U Edge MicroServer (Intel Xeon / ARM64 Ampere)
  [✓] Primary MAC:     52:54:00:12:34:56 (VLAN 10 Trusted Office)
  [✓] Disk Partition:  /dev/nvme0n1 LUKS2 Encrypted (AES-XTS-512)
  [✓] Hardware Key:    TPM 2.0 PCR[7] Attestation Sealed
  [✓] Plain Dashboard: http://localhost:8081?profile=plain
  [✓] Scenario Hub:    http://localhost:8081?profile=legal
""", flush=True)
    with http.server.ThreadingHTTPServer(("", PORT), HubHandler) as httpd:
        httpd.serve_forever()
