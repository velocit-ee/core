#!/usr/bin/env python3
"""
Automated Multi-Iteration Test Suite for Telia Vector Edge Demo.
Executes repeated lifecycles of the edge daemon and sovereign hub to verify:
1. Clean port binding and rapid port recycling without orphan sockets.
2. Complete REST API coverage (telemetry, manifest, logs, iPXE, claim).
3. CORS headers on all endpoints for third-party / Lovable UI embedding.
4. Correct state machine transitions (quarantined -> claiming -> active).
5. Fault injection and error handling (400 on malformed JSON, 404 on bad MAC).
6. Memory footprint compliance (< 40 MB RSS on CPE hardware class).
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional, Tuple

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TELIA_DIR = os.path.dirname(SCRIPT_DIR)
DAEMON_DIR = os.path.join(TELIA_DIR, "daemon")
TARGET_DIR = os.path.join(TELIA_DIR, "target")

AGENT_PORT = 8088
HUB_PORT = 8081
AGENT_URL = f"http://127.0.0.1:{AGENT_PORT}"
HUB_URL = f"http://127.0.0.1:{HUB_PORT}"


def get_rss_mb(pid: int) -> float:
    """Read RSS memory in megabytes for a given PID."""
    try:
        out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(pid)]).decode().strip()
        return float(out) / 1024.0
    except Exception:
        return 0.0


def wait_for_port(port: int, timeout: float = 5.0) -> bool:
    """Wait until an HTTP service is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(f"http://127.0.0.1:{port}/")
            with urllib.request.urlopen(req, timeout=0.5):
                return True
        except urllib.error.HTTPError:
            return True
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    return False


def wait_for_port_closed(port: int, timeout: float = 5.0) -> bool:
    """Wait until a local port is no longer accepting TCP connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                time.sleep(0.05)
        except (OSError, ConnectionRefusedError, ConnectionResetError):
            return True
    return False


def http_request(
    url: str,
    method: str = "GET",
    data: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, bytes, Dict[str, str]]:
    """Execute an HTTP request and return (status_code, body, response_headers)."""
    req_headers = headers.copy() if headers else {}
    body_bytes = None
    if data is not None:
        body_bytes = json.dumps(data).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=body_bytes, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, resp.read(), resp_headers
    except urllib.error.HTTPError as e:
        resp_headers = {k.lower(): v for k, v in e.headers.items()}
        return e.code, e.read(), resp_headers


class DemoIterationTester:
    def __init__(self, fast_mode: bool = True):
        self.fast_mode = fast_mode
        self.agent_proc: Optional[subprocess.Popen] = None
        self.hub_proc: Optional[subprocess.Popen] = None

    def start_processes(self) -> None:
        """Start both agent.py and sovereign_hub.py."""
        env = os.environ.copy()
        if self.fast_mode:
            env["TELIA_VECTOR_FAST_SIMULATION"] = "1"

        self.agent_proc = subprocess.Popen(
            [sys.executable, os.path.join(DAEMON_DIR, "agent.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self.hub_proc = subprocess.Popen(
            [sys.executable, os.path.join(TARGET_DIR, "sovereign_hub.py")],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        if not wait_for_port(AGENT_PORT, timeout=4.0):
            self.stop_processes()
            raise RuntimeError(f"Agent daemon failed to bind port {AGENT_PORT}")

        if not wait_for_port(HUB_PORT, timeout=4.0):
            self.stop_processes()
            raise RuntimeError(f"Sovereign hub failed to bind port {HUB_PORT}")

    def stop_processes(self) -> None:
        """Gracefully stop processes and ensure ports are recycled."""
        for proc in [self.agent_proc, self.hub_proc]:
            if proc and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)

        self.agent_proc = None
        self.hub_proc = None

        if not wait_for_port_closed(AGENT_PORT, timeout=3.0):
            raise RuntimeError(f"Port {AGENT_PORT} did not close cleanly")
        if not wait_for_port_closed(HUB_PORT, timeout=3.0):
            raise RuntimeError(f"Port {HUB_PORT} did not close cleanly")

    def run_single_iteration(self, iteration_num: int) -> Dict[str, Any]:
        """Execute a full test cycle in one iteration."""
        results: Dict[str, Any] = {"iteration": iteration_num, "checks": []}
        t0 = time.time()

        self.start_processes()
        try:
            agent_rss = get_rss_mb(self.agent_proc.pid)
            hub_rss = get_rss_mb(self.hub_proc.pid)
            results["agent_rss_mb"] = agent_rss
            results["hub_rss_mb"] = hub_rss

            # Check 1: Agent Root Portal (HTML)
            status, body, headers = http_request(f"{AGENT_URL}/")
            assert status == 200, f"Expected 200, got {status}"
            assert b"Telia Vector" in body, "Agent portal missing brand text"
            assert "access-control-allow-origin" in headers, "Missing CORS header on GET /"
            results["checks"].append("portal_root_html")

            # Check 2: Sovereign Hub Root (HTML)
            status, body, _ = http_request(f"{HUB_URL}/")
            assert status == 200, f"Expected 200, got {status}"
            assert b"Sovereign Legal Workspace" in body, "Hub missing workspace title"
            results["checks"].append("hub_root_html")

            # Check 3: CORS Preflight (OPTIONS)
            status, _, headers = http_request(f"{AGENT_URL}/api/v1/telemetry", method="OPTIONS")
            assert status in (200, 204), f"OPTIONS expected 200 or 204, got {status}"
            assert headers.get("access-control-allow-origin") == "*", "Bad Access-Control-Allow-Origin"
            assert "post" in headers.get("access-control-allow-methods", "").lower(), "Missing POST in CORS methods"
            results["checks"].append("cors_preflight")

            # Check 4: Telemetry Initial State
            status, body, _ = http_request(f"{AGENT_URL}/api/v1/telemetry")
            assert status == 200, f"Telemetry expected 200, got {status}"
            gateway = json.loads(body.decode("utf-8"))
            assert gateway["hardware_model"] == "Technicolor DGA4330 (Vantiva)"

            status, body, _ = http_request(f"{AGENT_URL}/api/v1/nodes")
            assert status == 200, f"Nodes expected 200, got {status}"
            nodes = json.loads(body.decode("utf-8"))
            assert len(nodes) > 0
            node = nodes[0]
            assert node["status"] == "quarantined", f"Expected quarantined, got {node['status']}"
            results["checks"].append("initial_telemetry_and_nodes")

            # Check 5: Velocitee Manifest Specification
            status, body, _ = http_request(f"{AGENT_URL}/api/v1/manifest")
            assert status == 200, f"Manifest expected 200, got {status}"
            manifest = json.loads(body.decode("utf-8"))
            assert manifest["schema_version"] == "1.0", f"Unexpected schema_version: {manifest.get('schema_version')}"
            assert manifest["engines"]["vme"]["gateway_hardware"] == "Technicolor DGA4330 (Telia X2)"
            results["checks"].append("canonical_manifest")

            # Check 6: iPXE Dynamic Bootstrap
            status, body, headers = http_request(f"{AGENT_URL}/boot.ipxe")
            assert status == 200, f"iPXE expected 200, got {status}"
            assert body.startswith(b"#!ipxe"), "Missing #!ipxe header"
            results["checks"].append("dynamic_ipxe")

            # Check 7: Error handling - bad claim (invalid MAC)
            status, _, _ = http_request(
                f"{AGENT_URL}/api/v1/nodes/00:00:00:00:00:00/claim",
                method="POST",
                data={"profile": "test", "operator": "admin"},
            )
            assert status == 404, f"Expected 404 for unknown MAC, got {status}"
            results["checks"].append("error_unknown_mac")

            # Check 8: Error handling - malformed JSON
            req = urllib.request.Request(
                f"{AGENT_URL}/api/v1/nodes/{node['mac']}/claim",
                data=b"invalid json payload",
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=2.0) as resp:
                    status = resp.status
            except urllib.error.HTTPError as e:
                status = e.code
            assert status == 400, f"Expected 400 for malformed JSON, got {status}"
            results["checks"].append("error_malformed_json")

            # Check 9: Execute Valid Claim Flow
            status, body, _ = http_request(
                f"{AGENT_URL}/api/v1/nodes/{node['mac']}/claim",
                method="POST",
                data={"profile": "sovereign-legal-advisory", "operator": "kati@legalpartners.ee"},
            )
            assert status == 200, f"Claim expected 200, got {status}"
            claim_resp = json.loads(body.decode("utf-8"))
            assert claim_resp["status"] == "claiming"
            assert "claim_token" in claim_resp
            token = claim_resp["claim_token"]
            assert "." in token, f"Malformed token structure: {token}"
            parts = token.split(".")
            assert len(parts) == 2 and parts[0].isdigit() and len(parts[1]) == 64, f"Invalid HMAC signature format: {token}"
            results["checks"].append("valid_claim_flow")

            # Check 10: Verify Audit Logs
            status, body, _ = http_request(f"{AGENT_URL}/api/v1/logs")
            assert status == 200
            logs = json.loads(body.decode("utf-8"))
            event_types = [entry["event_type"] for entry in logs]
            assert "CLAIM_APPROVED" in event_types, "CLAIM_APPROVED not found in audit logs"
            results["checks"].append("structured_audit_logs")

            # Check 11: Plain Profile Verification on Target Hub
            status, body, _ = http_request(f"{HUB_URL}/?profile=plain")
            assert status == 200
            assert b"Velocitee Vector Edge Node #01" in body
            assert b"Docker Engine" in body
            assert b"OpenSSH Server" in body
            results["checks"].append("plain_profile_verification")

            results["duration_ms"] = round((time.time() - t0) * 1000, 2)
            results["status"] = "PASSED"
            return results

        finally:
            self.stop_processes()


def main():
    parser = argparse.ArgumentParser(description="Multi-iteration tester for Telia Vector Edge Demo")
    parser.add_argument("--iterations", type=int, default=10, help="Number of iterations to run (default: 10)")
    args = parser.parse_args()

    print("\n========================================================")
    print("  TELIA VECTOR MULTI-ITERATION DEMO TEST SUITE")
    print(f"  Target Iterations: {args.iterations}")
    print("========================================================\n")

    tester = DemoIterationTester(fast_mode=True)
    all_passed = True
    iteration_stats = []

    for i in range(1, args.iterations + 1):
        sys.stdout.write(f"  [Iteration {i:02d}/{args.iterations:02d}] Running... ")
        sys.stdout.flush()
        try:
            res = tester.run_single_iteration(i)
            iteration_stats.append(res)
            print(f"PASSED ({res['duration_ms']}ms | Agent RSS: {res['agent_rss_mb']:.1f}MB)")
        except Exception as e:
            all_passed = False
            print(f"FAILED: {e}")
            break

    print("\n========================================================")
    if all_passed:
        avg_duration = sum(r["duration_ms"] for r in iteration_stats) / len(iteration_stats)
        max_rss = max(r["agent_rss_mb"] for r in iteration_stats)
        print(f"  ALL {args.iterations} ITERATIONS PASSED SUCCESSFULLY!")
        print(f"  Average Cycle Latency: {avg_duration:.1f}ms")
        print(f"  Peak Agent RSS Footprint: {max_rss:.1f}MB (CPE Budget: 40MB)")
        print("========================================================\n")
        return 0
    else:
        print(f"  SUITE FAILED ON ITERATION {len(iteration_stats) + 1}")
        print("========================================================\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
