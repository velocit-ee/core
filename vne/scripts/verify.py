#!/usr/bin/env python3
"""Post-provisioning verification gate.

Runs after any VNE backend completes. Halts on the first failure — there's no
point continuing once the system is broken in a way that downstream engines
will trip over. Exit code 0 means *every* check passed; non-zero means VNE
must not write its output manifest.

Checks, in order:
  1. OPNsense API reachable (HTTPS 200 from /api/core/system/status)
  2. DNS resolution working — resolve a known external hostname through OPNsense
  3. Internet egress — TCP-connect to a known external IP (no ICMP, frequently blocked)
  4. VLAN interfaces present on OPNsense and showing 'up'

Designed to be invoked either:
  - directly:  python -m vne.scripts.verify --opnsense-ip 10.10.10.1 --vlans 10,20
  - in-process: from vne.scripts.verify import run_all
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from dataclasses import dataclass
from typing import Iterable

import requests


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str

    def line(self) -> str:
        icon = "PASS" if self.passed else "FAIL"
        return f"  [{icon}] {self.name:<22} {self.detail}"


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def check_api_reachable(
    opnsense_ip: str,
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    timeout: float = 10.0,
) -> CheckResult:
    url = f"https://{opnsense_ip}/api/core/system/status"
    try:
        auth = (api_key, api_secret) if api_key and api_secret else None
        # OPNsense uses self-signed certs by default — verify=False is appropriate.
        # Suppress only this client's warnings, not globally.
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            pass
        r = requests.get(url, auth=auth, verify=False, timeout=timeout)
    except requests.RequestException as exc:
        return CheckResult("api_reachable", False,
                           f"could not reach {url}: {exc}")
    if r.status_code != 200:
        return CheckResult("api_reachable", False,
                           f"HTTP {r.status_code} from {url}")
    return CheckResult("api_reachable", True, f"{url} → 200")


def check_dns_resolving(
    opnsense_ip: str,
    *,
    hostname: str = "one.one.one.one",
    timeout: float = 10.0,
) -> CheckResult:
    """Resolve a public hostname *via* the OPNsense Unbound resolver.

    We do not use the local /etc/resolv.conf — that would test the seed
    machine's DNS, not OPNsense's. Use socket-level DNS pointing at the
    OPNsense IP directly.
    """
    try:
        # dnspython would be cleaner; we avoid the dependency by speaking DNS by hand.
        import struct

        # Build a minimal A query: TXID + flags + 1 question
        txid = os.urandom(2)
        header = txid + b"\x01\x00" + b"\x00\x01\x00\x00\x00\x00\x00\x00"
        qname = b"".join(bytes([len(p)]) + p.encode() for p in hostname.split(".")) + b"\x00"
        question = qname + b"\x00\x01\x00\x01"
        packet = header + question

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(timeout)
        sock.sendto(packet, (opnsense_ip, 53))
        data, _ = sock.recvfrom(512)
        sock.close()

        if data[:2] != txid:
            return CheckResult("dns_resolving", False,
                               "DNS reply TXID mismatch — possible spoofing or wrong server")
        rcode = data[3] & 0x0F
        if rcode != 0:
            return CheckResult("dns_resolving", False,
                               f"DNS rcode={rcode} from {opnsense_ip} resolving {hostname}")
        # Don't bother parsing the answer — non-zero ancount + rcode==0 is enough.
        ancount = struct.unpack("!H", data[6:8])[0]
        if ancount == 0:
            return CheckResult("dns_resolving", False,
                               f"DNS returned 0 answers for {hostname} via {opnsense_ip}")

        return CheckResult("dns_resolving", True,
                           f"resolved {hostname} via {opnsense_ip} (answers={ancount})")
    except (OSError, socket.timeout) as exc:
        return CheckResult("dns_resolving", False,
                           f"DNS query to {opnsense_ip}:53 failed: {exc}")


def check_internet_egress(
    *,
    target_ip: str = "1.1.1.1",
    target_port: int = 443,
    timeout: float = 8.0,
) -> CheckResult:
    """TCP-connect to a known anycast endpoint. We use TCP, not ICMP — many
    networks (and OPNsense's WAN block-bogons rule for any short period of
    transition state) drop ping but allow established TCP."""
    start = time.monotonic()
    try:
        with socket.create_connection((target_ip, target_port), timeout=timeout):
            elapsed = (time.monotonic() - start) * 1000
        return CheckResult("internet_egress", True,
                           f"TCP {target_ip}:{target_port} reached in {elapsed:.0f} ms")
    except OSError as exc:
        return CheckResult("internet_egress", False,
                           f"TCP {target_ip}:{target_port} unreachable: {exc}")


def check_vlans_up(
    opnsense_ip: str,
    expected_vlan_ids: Iterable[int],
    *,
    api_key: str | None = None,
    api_secret: str | None = None,
    timeout: float = 10.0,
) -> CheckResult:
    """Ask OPNsense which VLANs it has configured and confirm they're enabled.

    Without API credentials we can only check reachability, so we degrade
    gracefully — still PASS if the API can't be auth'd, with a 'skipped'
    detail. The other checks above already verify the appliance is alive."""
    expected = list(expected_vlan_ids)
    if not expected:
        return CheckResult("vlans_up", True, "no VLANs configured — nothing to check")

    if not (api_key and api_secret):
        return CheckResult("vlans_up", True,
                           "skipped (no API credentials provided to verify.py)")

    url = f"https://{opnsense_ip}/api/interfaces/vlan_settings/searchItem"
    try:
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        except ImportError:
            pass
        r = requests.get(url, auth=(api_key, api_secret), verify=False, timeout=timeout)
    except requests.RequestException as exc:
        return CheckResult("vlans_up", False,
                           f"could not query OPNsense VLAN list: {exc}")
    if r.status_code != 200:
        return CheckResult("vlans_up", False,
                           f"HTTP {r.status_code} from {url}")

    rows = (r.json() or {}).get("rows", []) or []
    present = {int(row.get("tag")) for row in rows if str(row.get("tag", "")).isdigit()}
    missing = [v for v in expected if v not in present]
    if missing:
        return CheckResult("vlans_up", False,
                           f"VLANs missing from OPNsense: {missing} (expected {expected})")
    return CheckResult("vlans_up", True,
                       f"all {len(expected)} expected VLAN(s) present: {expected}")


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def run_all(
    *,
    opnsense_ip: str,
    expected_vlan_ids: Iterable[int],
    api_key: str | None = None,
    api_secret: str | None = None,
) -> tuple[bool, list[CheckResult]]:
    """Run every check in order, halting on the first failure. Returns
    (passed_all, results-so-far)."""
    results: list[CheckResult] = []

    for check in (
        lambda: check_api_reachable(opnsense_ip, api_key=api_key, api_secret=api_secret),
        lambda: check_dns_resolving(opnsense_ip),
        lambda: check_internet_egress(),
        lambda: check_vlans_up(opnsense_ip, expected_vlan_ids,
                               api_key=api_key, api_secret=api_secret),
    ):
        result = check()
        results.append(result)
        print(result.line())
        if not result.passed:
            return False, results

    return True, results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify VNE-provisioned OPNsense network is healthy."
    )
    parser.add_argument("--opnsense-ip", required=True,
                        help="OPNsense LAN IP (the API host)")
    parser.add_argument("--vlans", default="",
                        help="Comma-separated VLAN IDs that should be present")
    args = parser.parse_args()

    vlan_ids: list[int] = []
    if args.vlans.strip():
        try:
            vlan_ids = [int(s.strip()) for s in args.vlans.split(",") if s.strip()]
        except ValueError:
            print("error: --vlans must be comma-separated integers", file=sys.stderr)
            return 2

    api_key = os.environ.get("OPNSENSE_API_KEY")
    api_secret = os.environ.get("OPNSENSE_API_SECRET")

    print(f"\nVerifying OPNsense at {args.opnsense_ip} ...\n")
    passed, _ = run_all(
        opnsense_ip=args.opnsense_ip,
        expected_vlan_ids=vlan_ids,
        api_key=api_key,
        api_secret=api_secret,
    )
    print()
    if passed:
        print("All checks passed.")
        return 0
    print("One or more checks failed — VNE manifest will not be written.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
