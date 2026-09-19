#!/usr/bin/env python3
"""
Telia Sovereign Edge Hub — Target Node Service (Port 8081)
Simulates the live, operational business server after zero-touch provisioning.
"""

import http.server
import socketserver
import os

PORT = 8081
socketserver.TCPServer.allow_reuse_address = True

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sovereign Legal Workspace #01 · Kati & Partners Advisory</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<!-- Web fonts are optional (G-04): loaded with media=print so a blocked or
     throttled network never blocks first paint. The CSS font stacks below
     fall back to system fonts when the request fails or times out. -->
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
  
  /* Header */
  .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 32px; padding-bottom: 24px; border-bottom: 1px solid var(--border); }
  .brand-title h1 { font-size: 22px; font-weight: 800; display: flex; align-items: center; gap: 10px; margin-bottom: 4px; }
  .brand-title p { color: var(--muted); font-size: 13px; }
  .sovereign-tag { background: rgba(16, 185, 129, 0.12); color: var(--green); border: 1px solid var(--green); padding: 6px 14px; border-radius: 20px; font-weight: 700; font-size: 12px; display: flex; align-items: center; gap: 6px; }

  /* Grid */
  .grid { display: grid; grid-template-columns: 2fr 1fr; gap: 28px; }
  .card { background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 24px; margin-bottom: 24px; }
  .card-title { font-size: 13px; text-transform: uppercase; letter-spacing: 0.8px; color: var(--muted); margin-bottom: 20px; font-weight: 700; }

  /* App Items */
  .app-item { display: flex; align-items: center; justify-content: space-between; padding: 16px; background: rgba(255,255,255,0.02); border: 1px solid rgba(255,255,255,0.06); border-radius: 10px; margin-bottom: 14px; }
  .app-left { display: flex; align-items: center; gap: 16px; }
  .app-icon { font-size: 26px; background: rgba(153, 10, 227, 0.15); border-radius: 10px; width: 48px; height: 48px; display: flex; align-items: center; justify-content: center; }
  .app-name { font-weight: 700; font-size: 15px; margin-bottom: 2px; }
  .app-desc { font-size: 12px; color: var(--muted); }
  .app-badge { font-size: 12px; font-weight: 700; color: var(--green); background: rgba(16, 185, 129, 0.1); padding: 4px 10px; border-radius: 6px; }

  /* Telemetry Rows */
  .row { display: flex; justify-content: space-between; padding: 10px 0; border-bottom: 1px solid rgba(255,255,255,0.04); font-size: 13px; }
  .row-label { color: var(--muted); }
  .row-val { font-weight: 600; font-family: var(--font-mono); }

  /* Guarantee Box */
  .guarantee { background: linear-gradient(135deg, rgba(16, 185, 129, 0.08) 0%, rgba(56, 189, 248, 0.04) 100%); border: 1px solid var(--green); border-radius: 12px; padding: 20px; }
  .guarantee h3 { font-size: 14px; color: var(--green); margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }
  .guarantee p { font-size: 13px; color: #cbd5e1; line-height: 1.5; }
</style>
</head>
<body>
<div class="container">
  
  <header class="header">
    <div class="brand-title">
      <h1>Sovereign Office Workspace #01 <span style="font-size:12px; color:#A855F7; font-weight:600; background:rgba(168,85,247,0.1); padding:2px 8px; border-radius:4px;">TALLINN HQ</span></h1>
      <p>Kati & Partners Legal & Financial Advisory · Provisioned via Telia X2 Business Gateway (Port 4)</p>
    </div>
    <div class="sovereign-tag">
      <span>● SOVEREIGN & ENCRYPTED</span>
    </div>
  </header>

  <div class="grid">
    <div>
      <div class="card">
        <div class="card-title">Active Sovereign Business Services</div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">📁</div>
            <div>
              <div class="app-name">Nextcloud Legal Vault & Client Portal</div>
              <div class="app-desc">Local sovereign document sync · Zero US hyperscaler storage · End-to-end encrypted</div>
            </div>
          </div>
          <div class="app-badge">ONLINE (LAN)</div>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">⚖️</div>
            <div>
              <div class="app-name">Estonian Accounting & Tax Database</div>
              <div class="app-desc">Encrypted local PostgreSQL · Directo / Merit Aktiva real-time sync connector</div>
            </div>
          </div>
          <div class="app-badge">ONLINE (LAN)</div>
        </div>

        <div class="app-item">
          <div class="app-left">
            <div class="app-icon">🛡️</div>
            <div>
              <div class="app-name">Telia Tier-3 Sovereign Backup Agent</div>
              <div class="app-desc">Encrypted deduplicated snapshots to Telia Tallinn Tier-3 Data Center (Estonia)</div>
            </div>
          </div>
          <div class="app-badge" style="color:var(--blue); background:rgba(56,189,248,0.1);">SYNCED (1m ago)</div>
        </div>
      </div>

      <div class="guarantee">
        <h3>🔒 Estonian Data Sovereignty Guarantee</h3>
        <p>This server was provisioned via <strong>Velocitee Metal Provisioning Engine (VME)</strong> without internal IT staff. Storage is full-disk encrypted with LUKS2 and sealed with TPM 2.0. Primary confidential client data never traverses unencrypted public transit and remains legally anchored inside the Republic of Estonia.</p>
      </div>
    </div>

    <div>
      <div class="card">
        <div class="card-title">Node Telemetry</div>
        <div class="row"><span class="row-label">Host Node</span><span class="row-val">sovereign-01.lan</span></div>
        <div class="row"><span class="row-label">Router Uplink</span><span class="row-val">Telia X2 (Port 4)</span></div>
        <div class="row"><span class="row-label">Assigned IP</span><span class="row-val">192.168.100.50</span></div>
        <div class="row"><span class="row-label">Storage Encryption</span><span class="row-val" style="color:var(--green);">LUKS2 / AES-XTS</span></div>
        <div class="row"><span class="row-label">Security Attestation</span><span class="row-val">TPM 2.0 PCR-7</span></div>
        <div class="row"><span class="row-label">Engine Backend</span><span class="row-val"><a href="https://velocit.ee" target="_blank" style="color:#A855F7; text-decoration:none;">velocit.ee VME</a></span></div>
      </div>

      <div class="card">
        <div class="card-title">Telia Business Value</div>
        <div style="font-size:13px; color:var(--muted); line-height:1.6;">
          • <strong>ARPU Expansion:</strong> +€89/month managed private edge tier.<br>
          • <strong>Cloud Attach:</strong> Automated offsite sync to Telia Tallinn DC.<br>
          • <strong>Moat:</strong> Core business databases run on this node — 0% churn risk.
        </div>
      </div>
    </div>
  </div>

</div>
</body>
</html>
"""

class HubHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(HTML.encode())

if __name__ == "__main__":
    print(f"Telia Sovereign Hub running on port {PORT}...")
    with socketserver.TCPServer(("", PORT), HubHandler) as httpd:
        httpd.serve_forever()
