"""Passive discovery — observe without probing.

Three independent sources, each runs to a configurable time bound:

  - kernel ARP / neighbor table (instant — what we've already talked to)
  - mDNS  (UDP 5353 multicast listener)
  - SSDP  (UDP 1900 multicast listener)

Passive discovery is run before active scanning. Hosts found here:
  - shorten the active sweep (we already know they exist)
  - contribute hostnames and service hints the active scan cannot infer
  - detect devices that don't answer ICMP/TCP on the common port set

We send *one* SSDP M-SEARCH at the start of the listen window — that's a
unicast-friendly request, not a broadcast probe of the whole network, so it
counts as passive enough for our purposes. mDNS we just listen.

No third-party deps. Failures (no multicast route, blocked socket, ...) are
non-fatal — we log a warning and return what we have.
"""

from __future__ import annotations

import logging
import re
import socket
import struct
import subprocess
import time
from pathlib import Path

from .report import Host

log = logging.getLogger("velocitee.discovery.passive")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def collect(
    *,
    listen_seconds: int = 8,
    iface_ip: str = "",
    do_mdns: bool = True,
    do_ssdp: bool = True,
) -> tuple[list[Host], list[str]]:
    """Return (hosts, warnings). Hosts are deduped by IP and merged."""
    warnings: list[str] = []

    by_ip: dict[str, Host] = {}

    for host in arp_table(warnings):
        _merge(by_ip, host)

    if listen_seconds > 0:
        if do_mdns:
            for host in _mdns_listen(listen_seconds, iface_ip, warnings):
                _merge(by_ip, host)
        if do_ssdp:
            for host in _ssdp_listen(listen_seconds, iface_ip, warnings):
                _merge(by_ip, host)

    return list(by_ip.values()), warnings


def _merge(target: dict[str, Host], new: Host) -> None:
    existing = target.get(new.ip)
    if not existing:
        target[new.ip] = new
        return
    # Merge: union of discovery channels, prefer non-empty fields.
    for src in new.discovered_via:
        if src not in existing.discovered_via:
            existing.discovered_via.append(src)
    if not existing.mac and new.mac:
        existing.mac = new.mac
    if not existing.hostname and new.hostname:
        existing.hostname = new.hostname
    if not existing.vendor and new.vendor:
        existing.vendor = new.vendor
    for hint in new.role_hints:
        if hint not in existing.role_hints:
            existing.role_hints.append(hint)


# ---------------------------------------------------------------------------
# ARP / neighbor table
# ---------------------------------------------------------------------------

def arp_table(warnings: list[str]) -> list[Host]:
    """Read the kernel neighbor table — `ip neigh` first, then /proc/net/arp."""
    hosts: list[Host] = []
    seen: set[str] = set()

    try:
        proc = subprocess.run(
            ["ip", "-4", "neigh", "show"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        for line in (proc.stdout or "").splitlines():
            # "192.168.1.1 dev eth0 lladdr aa:bb:cc:dd:ee:ff REACHABLE"
            parts = line.split()
            if len(parts) < 4:
                continue
            ip = parts[0]
            mac = ""
            for i, tok in enumerate(parts):
                if tok == "lladdr" and i + 1 < len(parts):
                    mac = parts[i + 1]
            state = parts[-1] if parts else ""
            if state.upper() in {"FAILED", "INCOMPLETE", "NONE"}:
                continue
            if ip in seen:
                continue
            seen.add(ip)
            hosts.append(Host(ip=ip, mac=mac, discovered_via=["arp"]))
        if hosts:
            return hosts
    except (subprocess.SubprocessError, OSError, FileNotFoundError) as exc:
        warnings.append(f"could not run 'ip neigh': {exc}")

    arp_file = Path("/proc/net/arp")
    if not arp_file.exists():
        return hosts
    try:
        for line in arp_file.read_text().splitlines()[1:]:
            cols = line.split()
            if len(cols) < 4:
                continue
            ip, _hwtype, flags, mac = cols[:4]
            if flags == "0x0":  # incomplete
                continue
            if ip in seen:
                continue
            seen.add(ip)
            hosts.append(Host(ip=ip, mac=mac, discovered_via=["arp"]))
    except OSError as exc:
        warnings.append(f"could not read /proc/net/arp: {exc}")
    return hosts


# ---------------------------------------------------------------------------
# mDNS — UDP 5353 multicast listener
# ---------------------------------------------------------------------------

_MDNS_GROUP = "224.0.0.251"
_MDNS_PORT = 5353


def _mdns_listen(seconds: int, iface_ip: str, warnings: list[str]) -> list[Host]:
    sock = _open_multicast_socket(_MDNS_GROUP, _MDNS_PORT, iface_ip, warnings)
    if not sock:
        return []
    hosts: dict[str, Host] = {}
    deadline = time.monotonic() + seconds
    sock.settimeout(0.5)
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            ip = addr[0]
            name = _parse_mdns_name(data)
            host = hosts.get(ip) or Host(ip=ip, discovered_via=["mdns"])
            if "mdns" not in host.discovered_via:
                host.discovered_via.append("mdns")
            if name and not host.hostname:
                host.hostname = name
            hosts[ip] = host
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return list(hosts.values())


def _parse_mdns_name(data: bytes) -> str:
    """Extract the first owner-name from an mDNS message. Best-effort, defensive."""
    try:
        if len(data) < 12:
            return ""
        # Skip the 12-byte header. Read one DNS name (label-length-prefixed).
        idx = 12
        labels: list[str] = []
        for _ in range(16):
            if idx >= len(data):
                break
            length = data[idx]
            if length == 0:
                break
            if length & 0xC0:  # pointer
                break
            idx += 1
            if idx + length > len(data):
                break
            label = data[idx:idx + length]
            try:
                labels.append(label.decode("utf-8"))
            except UnicodeDecodeError:
                return ""
            idx += length
        return ".".join(labels)
    except (IndexError, ValueError):
        return ""


# ---------------------------------------------------------------------------
# SSDP — UDP 1900 multicast listener with one M-SEARCH
# ---------------------------------------------------------------------------

_SSDP_GROUP = "239.255.255.250"
_SSDP_PORT = 1900

_SSDP_MSEARCH = (
    "M-SEARCH * HTTP/1.1\r\n"
    f"HOST: {_SSDP_GROUP}:{_SSDP_PORT}\r\n"
    'MAN: "ssdp:discover"\r\n'
    "MX: 2\r\n"
    "ST: ssdp:all\r\n"
    "\r\n"
).encode("ascii")


def _ssdp_listen(seconds: int, iface_ip: str, warnings: list[str]) -> list[Host]:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.settimeout(0.5)
        if iface_ip:
            try:
                sock.setsockopt(
                    socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(iface_ip),
                )
            except OSError:
                pass
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.sendto(_SSDP_MSEARCH, (_SSDP_GROUP, _SSDP_PORT))
    except OSError as exc:
        warnings.append(f"SSDP M-SEARCH failed: {exc}")
        return []

    hosts: dict[str, Host] = {}
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue
            except OSError:
                break
            ip = addr[0]
            text = data.decode("latin-1", errors="replace")
            server = _http_header(text, "SERVER")
            usn = _http_header(text, "USN")
            host = hosts.get(ip) or Host(ip=ip, discovered_via=["ssdp"])
            if "ssdp" not in host.discovered_via:
                host.discovered_via.append("ssdp")
            if server:
                # Use SSDP SERVER as a lightweight vendor hint.
                role = f"ssdp:{server}"
                if role not in host.role_hints:
                    host.role_hints.append(role)
            if usn and not host.hostname:
                m = re.search(r"uuid:([\w\-]+)", usn)
                if m:
                    host.hostname = m.group(1)
            hosts[ip] = host
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return list(hosts.values())


def _http_header(text: str, name: str) -> str:
    for line in text.splitlines():
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip().upper() == name.upper():
            return v.strip()
    return ""


# ---------------------------------------------------------------------------
# Multicast socket helper
# ---------------------------------------------------------------------------

def _open_multicast_socket(
    group: str, port: int, iface_ip: str, warnings: list[str],
) -> socket.socket | None:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        mreq = struct.pack(
            "4s4s",
            socket.inet_aton(group),
            socket.inet_aton(iface_ip or "0.0.0.0"),
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        return sock
    except OSError as exc:
        warnings.append(f"could not join multicast group {group}:{port}: {exc}")
        return None
