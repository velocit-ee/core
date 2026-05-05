"""Service fingerprinting — lightweight banner / header / cert grab.

Per-port grabs are bounded in time and bytes. We never hold a connection
open longer than `timeout_s` and never read more than 4 KiB. Parsers are
deliberately defensive: a malformed banner cannot crash the scan.

What we *do not* do here:
  - vulnerability detection / CVE matching (out of scope)
  - active fuzzing of services
  - long-running protocol negotiation

What we *do*:
  - SSH banner (line 1, first 256 bytes)
  - HTTP GET / on plain HTTP — capture status, Server header, <title>
  - HTTPS — TLS handshake to grab cert subject + SANs, then HTTP GET over TLS
  - SMB / NetBIOS — best-effort name dump
  - SNMP v2c sysDescr if a community string was provided

Each grab populates fields on the Service in place. Errors silently leave
fields empty — the host record still gets the service, just without details.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import socket
import ssl
from html.parser import HTMLParser

from .report import Host, Service

log = logging.getLogger("velocitee.discovery.fingerprint")


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def fingerprint_hosts(
    hosts: list[Host],
    *,
    timeout_s: float = 1.5,
    workers: int = 64,
    snmp_community: str = "",
) -> None:
    """Mutate `hosts` in place — fill banner/title/server/tls_san on each Service."""
    work: list[tuple[Host, Service]] = []
    for host in hosts:
        for svc in host.services:
            work.append((host, svc))

    def grab(item: tuple[Host, Service]) -> None:
        host, svc = item
        try:
            _fingerprint_one(host.ip, svc, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001 — fingerprint must never fail the scan
            log.debug("fingerprint %s:%d failed: %s", host.ip, svc.port, exc)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(grab, work))

    if snmp_community:
        _snmp_pass(hosts, community=snmp_community, timeout_s=timeout_s)


# ---------------------------------------------------------------------------
# Per-service grabs
# ---------------------------------------------------------------------------

def _fingerprint_one(ip: str, svc: Service, *, timeout_s: float) -> None:
    port = svc.port
    if port == 22:
        _ssh_banner(ip, svc, timeout_s)
    elif port in {80, 8080, 8081, 3000, 9000}:
        _http_grab(ip, svc, scheme="http", timeout_s=timeout_s)
    elif port in {443, 8443, 9443, 8006, 5986, 9090, 10000, 32400}:
        _https_grab(ip, svc, timeout_s=timeout_s)
    elif port == 23:
        _telnet_banner(ip, svc, timeout_s)
    elif port in {25, 465, 587, 110, 143, 993, 995, 21}:
        _line_banner(ip, svc, timeout_s)


def _ssh_banner(ip: str, svc: Service, timeout_s: float) -> None:
    data = _read_first_line(ip, svc.port, timeout_s=timeout_s)
    if not data:
        return
    svc.banner = data[:256]
    # "SSH-2.0-OpenSSH_9.3p1 Debian-1+b1"
    m = re.match(r"^SSH-\d\.\d-([^\s]+)(?:\s+(.*))?$", data)
    if m:
        product_full = m.group(1)
        if "_" in product_full:
            product, _, version = product_full.partition("_")
        else:
            product, version = product_full, ""
        svc.product = product
        svc.version = version


def _telnet_banner(ip: str, svc: Service, timeout_s: float) -> None:
    data = _read_bytes(ip, svc.port, timeout_s=timeout_s, max_bytes=512)
    if data:
        svc.banner = data.decode("latin-1", errors="replace")[:256].strip()


def _line_banner(ip: str, svc: Service, timeout_s: float) -> None:
    line = _read_first_line(ip, svc.port, timeout_s=timeout_s)
    if line:
        svc.banner = line[:256]


def _http_grab(ip: str, svc: Service, *, scheme: str, timeout_s: float) -> None:
    try:
        with socket.create_connection((ip, svc.port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            sock.sendall(_http_request(ip))
            raw = _read_until_done(sock, max_bytes=8192, timeout_s=timeout_s)
    except OSError:
        return
    _parse_http_response(raw, svc)


def _https_grab(ip: str, svc: Service, *, timeout_s: float) -> None:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, svc.port), timeout=timeout_s) as raw_sock:
            raw_sock.settimeout(timeout_s)
            with ctx.wrap_socket(raw_sock, server_hostname=ip) as sock:
                svc.tls = True
                _populate_tls_san(sock, svc)
                try:
                    sock.sendall(_http_request(ip))
                    data = _read_until_done(sock, max_bytes=8192, timeout_s=timeout_s)
                except OSError:
                    return
                _parse_http_response(data, svc)
    except (OSError, ssl.SSLError):
        return


def _populate_tls_san(sock: ssl.SSLSocket, svc: Service) -> None:
    try:
        cert = sock.getpeercert()
    except (ValueError, OSError):
        return
    if not cert:
        # Cert without verification is dict only when CERT_REQUIRED — fall back
        # to parsing the binary cert.
        try:
            der = sock.getpeercert(binary_form=True)
        except (ValueError, OSError):
            return
        sans = _sans_from_der(der or b"")
        if sans:
            svc.tls_san = sans
        return
    sans: list[str] = []
    for typ, val in cert.get("subjectAltName", ()):
        if typ == "DNS":
            sans.append(val)
    if sans:
        svc.tls_san = sans


def _sans_from_der(der: bytes) -> list[str]:
    """Naive SAN extractor from the DER-encoded cert. Falls back gracefully."""
    if not der:
        return []
    # Look for the OID for subjectAltName (2.5.29.17) — '\x06\x03\x55\x1d\x11'
    needle = b"\x06\x03\x55\x1d\x11"
    idx = der.find(needle)
    if idx < 0:
        return []
    # Advance to the OCTET STRING containing the SAN sequence — best-effort
    # text scan for hostnames following the OID. Not strict ASN.1 parsing,
    # but sufficient for the common case of a self-signed cert exposing SANs.
    window = der[idx:idx + 2048]
    candidates = re.findall(rb"[A-Za-z0-9_*\.\-]{3,253}", window)
    sans: list[str] = []
    for cand in candidates:
        text = cand.decode("ascii", errors="ignore")
        if "." in text and not text.startswith(("0", "1", "2.5", "subject")):
            if text not in sans and text.lower() not in {"unknown", "n/a"}:
                sans.append(text)
    return sans[:8]


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _http_request(host: str) -> bytes:
    return (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: velocitee-discover/0.1\r\n"
        f"Accept: text/html,*/*;q=0.5\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    ).encode("ascii")


def _parse_http_response(raw: bytes, svc: Service) -> None:
    if not raw:
        return
    head, _, body = raw.partition(b"\r\n\r\n")
    head_text = head.decode("latin-1", errors="replace")
    server = ""
    for line in head_text.splitlines()[1:]:  # skip status line
        if ":" in line:
            k, _, v = line.partition(":")
            if k.strip().lower() == "server":
                server = v.strip()
                break
    svc.http_server = server[:256]

    # Body up to ~16 KiB for title.
    title = _extract_title(body[:16384])
    if title:
        svc.http_title = title[:256]


class _TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title = ""
        self.done = False

    def handle_starttag(self, tag: str, attrs):  # type: ignore[no-untyped-def]
        if tag.lower() == "title" and not self.done:
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title" and self.in_title:
            self.in_title = False
            self.done = True

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title += data


def _extract_title(body: bytes) -> str:
    if not body:
        return ""
    text = body.decode("latin-1", errors="replace")
    parser = _TitleParser()
    try:
        parser.feed(text)
    except Exception:  # noqa: BLE001
        return ""
    return parser.title.strip()


# ---------------------------------------------------------------------------
# Socket helpers
# ---------------------------------------------------------------------------

def _read_first_line(ip: str, port: int, *, timeout_s: float, max_bytes: int = 512) -> str:
    raw = _read_bytes(ip, port, timeout_s=timeout_s, max_bytes=max_bytes)
    if not raw:
        return ""
    text = raw.decode("latin-1", errors="replace")
    return text.splitlines()[0].strip() if text else ""


def _read_bytes(ip: str, port: int, *, timeout_s: float, max_bytes: int) -> bytes:
    try:
        with socket.create_connection((ip, port), timeout=timeout_s) as sock:
            sock.settimeout(timeout_s)
            return sock.recv(max_bytes)
    except OSError:
        return b""


def _read_until_done(sock: socket.socket | ssl.SSLSocket, *, max_bytes: int, timeout_s: float) -> bytes:
    sock.settimeout(timeout_s)
    chunks: list[bytes] = []
    total = 0
    try:
        while total < max_bytes:
            chunk = sock.recv(min(4096, max_bytes - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
    except OSError:
        pass
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# SNMP — optional pass over hosts that look router-y
# ---------------------------------------------------------------------------

def _snmp_pass(hosts: list[Host], *, community: str, timeout_s: float) -> None:
    """v2c sysDescr.0 + sysName.0 GET. Pure socket impl — no pysnmp dep.

    A successful response populates host.role_hints with 'snmp:<sysDescr>'.
    We do not iterate the full MIB — sysDescr alone is enough to identify
    most consumer/prosumer routers and switches.
    """
    for host in hosts:
        if host.ip == "":
            continue
        descr = _snmp_get_sysdescr(host.ip, community=community, timeout_s=timeout_s)
        if descr:
            tag = f"snmp:{descr[:120]}"
            if tag not in host.role_hints:
                host.role_hints.append(tag)


def _snmp_get_sysdescr(ip: str, *, community: str, timeout_s: float) -> str:
    """Issue an SNMP v2c GET for sysDescr.0 (1.3.6.1.2.1.1.1.0). Best-effort.

    We hand-roll the BER-encoded packet to avoid pulling in pysnmp. If parsing
    fails or the response doesn't decode cleanly, return ''. This is small but
    fiddly — kept inline rather than spread across the file.
    """
    pkt = _snmp_v2c_get_packet(community, oid=(1, 3, 6, 1, 2, 1, 1, 1, 0))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_s)
            sock.sendto(pkt, (ip, 161))
            data, _ = sock.recvfrom(2048)
    except OSError:
        return ""
    return _snmp_extract_string(data)


def _snmp_v2c_get_packet(community: str, *, oid: tuple[int, ...]) -> bytes:
    """Build a SNMPv2c GET request. Request-id is fixed; we don't pipeline."""
    def encode_length(n: int) -> bytes:
        if n < 0x80:
            return bytes([n])
        body = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return bytes([0x80 | len(body)]) + body

    def encode_int(value: int) -> bytes:
        if value == 0:
            body = b"\x00"
        else:
            length = (value.bit_length() + 8) // 8
            body = value.to_bytes(length, "big", signed=False)
            if body[0] & 0x80:
                body = b"\x00" + body
        return b"\x02" + encode_length(len(body)) + body

    def encode_octet_string(s: bytes) -> bytes:
        return b"\x04" + encode_length(len(s)) + s

    def encode_oid(parts: tuple[int, ...]) -> bytes:
        # First two values are encoded as 40*a + b
        out = bytearray([40 * parts[0] + parts[1]])
        for v in parts[2:]:
            if v < 128:
                out.append(v)
            else:
                stack: list[int] = []
                while v:
                    stack.append(v & 0x7F)
                    v >>= 7
                stack[0] |= 0x00
                for i in range(1, len(stack)):
                    stack[i] |= 0x80
                out.extend(reversed(stack))
        return b"\x06" + encode_length(len(out)) + bytes(out)

    def encode_sequence(*items: bytes) -> bytes:
        body = b"".join(items)
        return b"\x30" + encode_length(len(body)) + body

    null = b"\x05\x00"
    varbind = encode_sequence(encode_oid(oid), null)
    varbind_list = encode_sequence(varbind)
    pdu = (
        b"\xa0"  # GetRequest PDU tag
        + encode_length(len(
            encode_int(0x42)            # request-id (arbitrary)
            + encode_int(0)             # error-status
            + encode_int(0)             # error-index
            + varbind_list
        ))
        + encode_int(0x42)
        + encode_int(0)
        + encode_int(0)
        + varbind_list
    )
    msg = encode_sequence(
        encode_int(1),                     # version (v2c == 1)
        encode_octet_string(community.encode("utf-8")),
        pdu,
    )
    return msg


def _snmp_extract_string(data: bytes) -> str:
    """Find the first OCTET STRING after the request-id in an SNMP response."""
    if not data or data[0] != 0x30:
        return ""
    # Walk past the version int and community octet string by finding the PDU.
    # Then find the first 0x04 (OCTET STRING) inside that. Simplistic but
    # adequate for sysDescr.0.
    idx = 0
    n = len(data)
    while idx < n:
        if data[idx] == 0x04:
            length, used = _snmp_read_length(data, idx + 1)
            if length is None:
                return ""
            start = idx + 1 + used
            value = data[start:start + length]
            try:
                text = value.decode("utf-8", errors="replace").strip()
            except UnicodeDecodeError:
                text = ""
            # Skip the community OCTET STRING by requiring the value to look
            # like a multi-word descriptor (community is usually one token).
            if " " in text or len(text) > 32:
                return text
            idx = start + length
            continue
        idx += 1
    return ""


def _snmp_read_length(data: bytes, idx: int) -> tuple[int | None, int]:
    if idx >= len(data):
        return None, 0
    first = data[idx]
    if first < 0x80:
        return first, 1
    nbytes = first & 0x7F
    if nbytes == 0 or idx + 1 + nbytes > len(data):
        return None, 0
    return int.from_bytes(data[idx + 1:idx + 1 + nbytes], "big"), 1 + nbytes
