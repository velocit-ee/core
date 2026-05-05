"""Active scan — sweep + TCP connect-scan.

We deliberately do *not* require nmap, scapy, or raw sockets. Two reasons:
  - Portability: works in unprivileged containers, dev machines, and CI.
  - Compatibility: every OS we target ships Python with a usable socket lib.

Cost: TCP-connect scanning is noisier (full handshakes) and slightly slower
than SYN scanning. For the network sizes VNE/VSE/VLE care about (a /24 to
maybe a /16 with limited port set) the wall-clock difference is negligible
once we parallelize.

The default port set is small and biased toward management/infra ports —
this is a "what's running on this network?" tool, not a CVE scanner. Operators
who want a wider scan can pass --ports.
"""

from __future__ import annotations

import concurrent.futures
import ipaddress
import logging
import socket
import time
from typing import Iterable

from .report import Host, Service

log = logging.getLogger("velocitee.discovery.active")


DEFAULT_PORTS: tuple[int, ...] = (
    22,    # SSH
    23,    # Telnet (legacy switches/routers)
    53,    # DNS
    80,    # HTTP
    123,   # NTP (TCP variant rarely; included for completeness)
    161,   # SNMP (UDP — connect-scan won't see it; left in for future SNMP probe)
    443,   # HTTPS
    445,   # SMB
    515,   # LPD
    554,   # RTSP
    631,   # CUPS
    830,   # NETCONF
    902,   # VMware
    993,   # IMAPS
    995,   # POP3S
    1883,  # MQTT
    2049,  # NFS
    3000,  # Common web app
    3306,  # MySQL
    3389,  # RDP
    5060,  # SIP
    5222,  # XMPP
    5432,  # PostgreSQL
    5601,  # Kibana
    5900,  # VNC
    5985,  # WinRM HTTP
    5986,  # WinRM HTTPS
    6379,  # Redis
    7547,  # TR-069 (CPE management — common on consumer routers)
    8006,  # Proxmox
    8080,  # HTTP-alt
    8081,  # HTTP-alt
    8443,  # HTTPS-alt (OPNsense, pfSense, UniFi)
    8728,  # MikroTik API
    8729,  # MikroTik API-SSL
    9000,  # Various
    9090,  # Cockpit
    9100,  # Printer / Prometheus node-exporter
    9443,  # UniFi
    10000, # Webmin / VTun
    27017, # MongoDB
    32400, # Plex
)


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def sweep(
    cidrs: Iterable[str],
    *,
    timeout_s: float = 0.6,
    workers: int = 256,
    sweep_ports: tuple[int, ...] = (22, 80, 443, 53, 8443, 8006),
) -> list[str]:
    """Find live IPv4 hosts in `cidrs` via concurrent TCP-connect on a tiny port set.

    Any host that completes a TCP handshake on *any* port in `sweep_ports` is
    counted as alive. We do not need to confirm every probed host responds on
    the same port; the goal here is "is something there?".

    Returns a deduped, sorted list of IPv4 strings.
    """
    targets: list[str] = []
    for cidr in cidrs:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            log.warning("sweep: invalid CIDR %s — skipping", cidr)
            continue
        if net.num_addresses > 65_536:
            log.warning("sweep: %s has %d addresses — limit is /16, skipping",
                        cidr, net.num_addresses)
            continue
        for ip in net.hosts():
            targets.append(str(ip))

    alive: set[str] = set()

    def probe(ip: str) -> str | None:
        for port in sweep_ports:
            if _tcp_connect(ip, port, timeout_s):
                return ip
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for result in pool.map(probe, targets):
            if result:
                alive.add(result)

    return sorted(alive, key=lambda ip: tuple(int(o) for o in ip.split(".")))


def connect_scan(
    targets: Iterable[str],
    ports: Iterable[int] = DEFAULT_PORTS,
    *,
    timeout_s: float = 0.6,
    workers: int = 256,
) -> dict[str, list[Service]]:
    """Per-host list of open TCP ports across `ports`. Concurrent.

    Returns {ip: [Service(port=...), ...]}. Empty list means we didn't find
    open ports — the host might still be alive (e.g. firewalled).
    """
    ports = tuple(ports)
    targets = list(targets)
    out: dict[str, list[Service]] = {ip: [] for ip in targets}

    work = [(ip, port) for ip in targets for port in ports]

    def probe(item: tuple[str, int]) -> tuple[str, int] | None:
        ip, port = item
        if _tcp_connect(ip, port, timeout_s):
            return (ip, port)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for hit in pool.map(probe, work):
            if hit:
                ip, port = hit
                out[ip].append(Service(port=port, name=_service_name(port)))

    return out


# ---------------------------------------------------------------------------
# Annotation pass — turn passive hosts + connect-scan results into final Hosts
# ---------------------------------------------------------------------------

def annotate_hosts(
    *,
    ips: Iterable[str],
    services_by_ip: dict[str, list[Service]],
    seed: dict[str, Host] | None = None,
) -> list[Host]:
    """Build the final Host list. `seed` carries passive findings to merge."""
    seed = dict(seed or {})
    for ip in ips:
        host = seed.get(ip) or Host(ip=ip)
        if "tcp-connect" not in host.discovered_via:
            host.discovered_via.append("tcp-connect")
        host.services = services_by_ip.get(ip, [])
        seed[ip] = host
    return sorted(seed.values(), key=lambda h: tuple(int(o) for o in h.ip.split(".")))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tcp_connect(ip: str, port: int, timeout_s: float) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout_s)
            return sock.connect_ex((ip, port)) == 0
    except OSError:
        return False


_SERVICE_NAMES: dict[int, str] = {
    22: "ssh", 23: "telnet", 53: "dns", 80: "http", 123: "ntp",
    161: "snmp", 443: "https", 445: "smb", 515: "lpd", 554: "rtsp",
    631: "ipp", 830: "netconf", 902: "vmware", 993: "imaps", 995: "pop3s",
    1883: "mqtt", 2049: "nfs", 3000: "http", 3306: "mysql", 3389: "rdp",
    5060: "sip", 5222: "xmpp", 5432: "postgres", 5601: "kibana",
    5900: "vnc", 5985: "winrm", 5986: "winrm-tls",
    6379: "redis", 7547: "tr069",
    8006: "proxmox", 8080: "http", 8081: "http", 8443: "https",
    8728: "mikrotik-api", 8729: "mikrotik-api-tls",
    9000: "http", 9090: "cockpit", 9100: "jetdirect", 9443: "https",
    10000: "webmin", 27017: "mongodb", 32400: "plex",
}


def _service_name(port: int) -> str:
    return _SERVICE_NAMES.get(port, "")


def time_budget_estimate(num_hosts: int, num_ports: int, timeout_s: float = 0.6) -> float:
    """Rough wall-clock estimate in seconds — used to warn users on huge sweeps."""
    work = num_hosts * num_ports
    workers = 256
    return max(1.0, (work / workers) * timeout_s)


def now() -> float:
    return time.monotonic()
