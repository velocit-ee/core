"""Local network introspection — what does the host *already know*?

We deliberately avoid probing the network here. This module reads kernel
state and system files: interfaces, routing table, DNS resolvers, DHCP leases.
It is the cheapest source of information and runs unprivileged.

The output feeds into NetworkInfo on the DiscoveryReport. Active scanning
takes this output as its starting point — e.g. if the kernel says the default
gateway is 192.168.1.1 on iface eth0, the active sweep defaults to scanning
the connected /24 unless the operator specified otherwise.

All system access is through subprocess + /proc + /etc reads. No third-party
dependencies. We tolerate missing tools (a minimal container without `ip(8)`,
say) by returning empty fields and adding a warning, never by crashing.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from .report import LocalInterface, NetworkInfo

log = logging.getLogger("velocitee.discovery.network")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def collect_local() -> tuple[NetworkInfo, list[str]]:
    """Return (NetworkInfo, warnings).

    Warnings describe missing system tools or unparseable files. They surface
    on the DiscoveryReport so the operator knows when something was skipped.
    """
    warnings: list[str] = []

    interfaces = _enumerate_interfaces(warnings)
    gateway, gw_iface = _default_gateway(warnings)
    resolvers, search = _dns_resolvers(warnings)
    lease = _dhcp_lease(gw_iface or "", warnings)

    info = NetworkInfo(
        interfaces=interfaces,
        default_gateway=gateway,
        default_gateway_iface=gw_iface,
        dns_resolvers=resolvers,
        search_domains=search,
        dhcp_lease=lease,
    )
    return info, warnings


def derive_default_cidrs(info: NetworkInfo) -> list[str]:
    """Pick the CIDR(s) most likely to be 'this network' for a default scan.

    Prefers the CIDR carrying the default gateway. Falls back to all
    non-loopback IPv4 CIDRs on UP interfaces. Caller can always override with
    --cidr explicitly.
    """
    if info.default_gateway and info.default_gateway_iface:
        for iface in info.interfaces:
            if iface.name == info.default_gateway_iface:
                return list(iface.cidr)
    out: list[str] = []
    for iface in info.interfaces:
        if not iface.is_up or iface.name == "lo":
            continue
        out.extend(iface.cidr)
    return out


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------

def _enumerate_interfaces(warnings: list[str]) -> list[LocalInterface]:
    """Use `ip -j addr` if available; otherwise fall back to /sys/class/net."""
    if shutil.which("ip"):
        return _interfaces_via_ip(warnings)
    return _interfaces_via_sysfs(warnings)


def _interfaces_via_ip(warnings: list[str]) -> list[LocalInterface]:
    try:
        proc = subprocess.run(
            ["ip", "-j", "addr"], capture_output=True, text=True, timeout=5, check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        warnings.append(f"could not run 'ip -j addr': {exc}")
        return _interfaces_via_sysfs(warnings)

    try:
        raw = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        warnings.append(f"could not parse 'ip -j addr' output: {exc}")
        return []

    out: list[LocalInterface] = []
    for entry in raw:
        name = entry.get("ifname", "")
        flags = entry.get("flags", []) or []
        is_up = "UP" in flags or entry.get("operstate", "").upper() == "UP"
        mtu = int(entry.get("mtu", 0) or 0)
        mac = entry.get("address", "") or ""

        ipv4: list[str] = []
        cidrs: list[str] = []
        for ainfo in entry.get("addr_info", []) or []:
            if ainfo.get("family") != "inet":
                continue
            local = ainfo.get("local", "")
            prefix = ainfo.get("prefixlen", 24)
            if local:
                ipv4.append(local)
                try:
                    net = ipaddress.ip_network(f"{local}/{prefix}", strict=False)
                    cidrs.append(str(net))
                except ValueError:
                    pass

        is_vlan, vlan_id, parent = _parse_vlan(entry, name)

        out.append(LocalInterface(
            name=name,
            mac=mac,
            ipv4=ipv4,
            cidr=cidrs,
            is_up=is_up,
            mtu=mtu,
            is_vlan=is_vlan,
            vlan_id=vlan_id,
            parent=parent,
        ))
    return out


def _parse_vlan(entry: dict, name: str) -> tuple[bool, int | None, str]:
    """Detect VLAN subinterfaces.

    `ip -j` reports linkinfo.info_kind == 'vlan' with info_data.id and the
    parent through 'link'. We also accept the legacy 'eth0.10' naming as a
    fallback for hosts that don't expose linkinfo.
    """
    linkinfo = entry.get("linkinfo") or {}
    if linkinfo.get("info_kind") == "vlan":
        info_data = linkinfo.get("info_data") or {}
        vid = info_data.get("id")
        try:
            vid = int(vid) if vid is not None else None
        except (TypeError, ValueError):
            vid = None
        return True, vid, entry.get("link", "") or ""
    m = re.match(r"^(?P<parent>[a-z0-9]+)\.(?P<vid>\d{1,4})$", name)
    if m and 1 <= int(m["vid"]) <= 4094:
        return True, int(m["vid"]), m["parent"]
    return False, None, ""


def _interfaces_via_sysfs(warnings: list[str]) -> list[LocalInterface]:
    """Pure /sys/class/net fallback. No CIDRs (would need extra parsing)."""
    base = Path("/sys/class/net")
    if not base.is_dir():
        warnings.append("no 'ip' command and /sys/class/net unavailable; interface list empty")
        return []
    out: list[LocalInterface] = []
    for entry in sorted(base.iterdir()):
        try:
            mac = (entry / "address").read_text().strip()
        except OSError:
            mac = ""
        try:
            mtu = int((entry / "mtu").read_text().strip() or 0)
        except (OSError, ValueError):
            mtu = 0
        try:
            operstate = (entry / "operstate").read_text().strip()
        except OSError:
            operstate = ""
        out.append(LocalInterface(
            name=entry.name,
            mac=mac,
            mtu=mtu,
            is_up=operstate.lower() == "up",
        ))
    return out


def _default_gateway(warnings: list[str]) -> tuple[str, str]:
    """Read the IPv4 default route from `ip -j route` or /proc/net/route."""
    if shutil.which("ip"):
        try:
            proc = subprocess.run(
                ["ip", "-j", "route", "show", "default"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            for entry in json.loads(proc.stdout or "[]"):
                gw = entry.get("gateway", "")
                dev = entry.get("dev", "")
                if gw:
                    return gw, dev
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"could not parse default route from ip(8): {exc}")

    return _default_gateway_proc(warnings)


def _default_gateway_proc(warnings: list[str]) -> tuple[str, str]:
    path = Path("/proc/net/route")
    if not path.exists():
        return "", ""
    try:
        lines = path.read_text().splitlines()
    except OSError as exc:
        warnings.append(f"could not read /proc/net/route: {exc}")
        return "", ""
    if len(lines) < 2:
        return "", ""

    # Header: Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 8:
            continue
        iface, dest, gw_hex, _flags, _ref, _use, _metric, mask = parts[:8]
        if dest == "00000000" and mask == "00000000":
            try:
                gw_int = int(gw_hex, 16)
                gw = ".".join(str((gw_int >> (8 * i)) & 0xFF) for i in range(4))
                return gw, iface
            except ValueError:
                continue
    return "", ""


def _dns_resolvers(warnings: list[str]) -> tuple[list[str], list[str]]:
    """Parse /etc/resolv.conf — both classic and systemd-resolved variants.

    On systemd-resolved hosts, /etc/resolv.conf is often a symlink to the stub
    listing 127.0.0.53. That's a valid resolver, but operationally less useful
    for a network audit, so we *also* try `resolvectl status --no-pager` to
    pull the real upstream resolvers behind the stub.
    """
    resolvers: list[str] = []
    search: list[str] = []
    resolv = Path("/etc/resolv.conf")
    if resolv.exists():
        try:
            for line in resolv.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or not line:
                    continue
                if line.startswith("nameserver"):
                    parts = line.split()
                    if len(parts) >= 2:
                        resolvers.append(parts[1])
                elif line.startswith("search") or line.startswith("domain"):
                    parts = line.split()
                    if len(parts) >= 2:
                        search.extend(parts[1:])
        except OSError as exc:
            warnings.append(f"could not read /etc/resolv.conf: {exc}")

    # Stub resolver — try to dig out the real upstreams.
    if any(r in {"127.0.0.53", "127.0.0.54"} for r in resolvers) and shutil.which("resolvectl"):
        try:
            proc = subprocess.run(
                ["resolvectl", "status", "--no-pager"],
                capture_output=True, text=True, timeout=5, check=False,
            )
            real: list[str] = []
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                # Lines look like "DNS Servers: 1.1.1.1 9.9.9.9" or "Current DNS Server: 1.1.1.1"
                if line.startswith(("DNS Servers:", "Current DNS Server:")):
                    _, _, rhs = line.partition(":")
                    for tok in rhs.split():
                        try:
                            ipaddress.ip_address(tok)
                            if tok not in real:
                                real.append(tok)
                        except ValueError:
                            pass
            if real:
                resolvers = real + [r for r in resolvers if r not in real]
        except (subprocess.SubprocessError, OSError) as exc:
            warnings.append(f"resolvectl call failed: {exc}")

    # De-dupe preserving order.
    seen: set[str] = set()
    resolvers = [r for r in resolvers if not (r in seen or seen.add(r))]
    seen.clear()
    search = [s for s in search if not (s in seen or seen.add(s))]
    return resolvers, search


def _dhcp_lease(iface: str, warnings: list[str]) -> dict[str, str]:
    """Extract DHCP lease info for the default-gateway interface, if present."""
    candidates: list[Path] = []
    if iface:
        candidates += [
            Path(f"/var/lib/dhcp/dhclient.{iface}.leases"),
            Path(f"/var/lib/dhclient/dhclient.{iface}.leases"),
            Path(f"/var/lib/NetworkManager/internal-{iface}.lease"),
        ]
    candidates += [
        Path("/var/lib/dhcp/dhclient.leases"),
        Path("/var/lib/dhclient/dhclient.leases"),
    ]

    for path in candidates:
        try:
            if not path.is_file():
                continue
            return _parse_dhclient_lease(path.read_text())
        except PermissionError:
            warnings.append(f"no permission to read {path} (root-owned lease — try sudo)")
        except OSError as exc:
            warnings.append(f"could not read {path}: {exc}")

    # systemd-networkd JSON lease (newer systemds)
    if iface and shutil.which("networkctl"):
        try:
            proc = subprocess.run(
                ["networkctl", "status", "--no-pager", iface],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return _parse_networkctl(proc.stdout or "")
        except (subprocess.SubprocessError, OSError):
            pass

    return {}


_LEASE_KEYS = {
    "fixed-address": "ip",
    "subnet-mask": "subnet_mask",
    "routers": "routers",
    "domain-name-servers": "dns_servers",
    "domain-name": "domain",
    "dhcp-server-identifier": "dhcp_server",
    "dhcp-lease-time": "lease_time",
    "broadcast-address": "broadcast",
    "next-server": "next_server",
    "host-name": "hostname",
}


def _parse_dhclient_lease(text: str) -> dict[str, str]:
    """Return the *most recent* lease as a flat dict. Strips trailing semicolons."""
    leases: list[dict[str, str]] = []
    cur: dict[str, str] = {}
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("lease {"):
            in_block = True
            cur = {}
            continue
        if line == "}" and in_block:
            in_block = False
            leases.append(cur)
            continue
        if not in_block or not line.startswith("option ") and not any(
            line.startswith(f"{k} ") for k in _LEASE_KEYS
        ) and not line.startswith("fixed-address"):
            continue
        # 'option key value;' or 'key value;'
        rest = line[len("option "):] if line.startswith("option ") else line
        rest = rest.rstrip(";").strip()
        if " " not in rest:
            continue
        key, _, value = rest.partition(" ")
        key = key.strip()
        value = value.strip().strip('"')
        target = _LEASE_KEYS.get(key)
        if target:
            cur[target] = value

    return leases[-1] if leases else {}


def _parse_networkctl(text: str) -> dict[str, str]:
    """Coarse parser for `networkctl status` output. Best-effort."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        if key == "address":
            out.setdefault("ip", value.split()[0])
        elif key == "gateway":
            out.setdefault("routers", value.split()[0])
        elif key == "dns":
            out.setdefault("dns_servers", value)
        elif key in {"search domains", "domain"}:
            out.setdefault("domain", value)
    return out
