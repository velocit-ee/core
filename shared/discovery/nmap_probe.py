"""Optional nmap enrichment for the discovery toolkit.

Nmap is the gold standard for service-version + OS fingerprinting. When it's
installed on PATH we use it to enrich the stdlib scan with real version
detection (`-sV`) and best-effort OS guesses (`-O`, root only). When it's
missing the rest of discovery still works — nmap is strictly an additive
layer, never a hard dependency.

## License boundary (read this before changing the module)

Nmap ships under the **Nmap Public Source License** — a modified GPL v2 with
extra restrictions on commercial redistribution. Two consequences shape
this module:

  - We **never** import a Python nmap wrapper. `python-nmap` and
    `python-libnmap` are GPL v2; importing either would virally pull
    velocitee-shared into GPL territory and conflict with our Apache 2.0
    license.
  - We invoke the `nmap` binary as a subprocess and parse its `-oX -` XML
    output ourselves with stdlib `xml.etree`. Subprocess invocation is
    legally analogous to calling `git` from a Python script and is well
    within Apache 2.0 + NPSL coexistence.

Self-hosted users install `nmap` themselves. We do not ship it. The
velocit.ee SaaS does not run nmap server-side and would need an Nmap OEM
license before it ever did.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import xml.etree.ElementTree as ET
from typing import Iterable

from .report import Host, OSGuess, Service

log = logging.getLogger("velocitee.discovery.nmap")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Cheap check: is the `nmap` binary on PATH?"""
    return shutil.which("nmap") is not None


def has_root() -> bool:
    """Whether we can run privileged probes (-sS SYN scan, -O OS detection)."""
    try:
        import os as _os
        return _os.geteuid() == 0
    except AttributeError:
        return False


def enrich_hosts(
    hosts: list[Host],
    *,
    ports: Iterable[int],
    timeout_s: float = 120.0,
    do_os_detect: bool = True,
) -> tuple[bool, str]:
    """Run nmap against the given hosts and merge results in place.

    Returns (used, message). `used` is True only if nmap actually ran and
    produced parseable output; `message` is a short status string suitable
    for the report's warnings list when something is off.

    Hosts without an IP are skipped. Ports that are not open in the input
    `hosts` are not re-scanned by nmap — we restrict to the union of open
    ports we already know about, plus the caller's `ports` hint, to keep
    nmap fast.
    """
    if not hosts:
        return False, "no hosts to enrich"
    if not is_available():
        return False, "nmap not installed; skipping enrichment"

    targets = [h.ip for h in hosts if h.ip]
    if not targets:
        return False, "no host IPs to enrich"

    open_ports: set[int] = set(ports)
    for host in hosts:
        for svc in host.services:
            open_ports.add(svc.port)
    if not open_ports:
        return False, "no ports to scan with nmap"

    args = _build_nmap_args(
        targets=targets,
        ports=sorted(open_ports),
        do_os_detect=do_os_detect and has_root(),
    )
    log.debug("nmap: %s", " ".join(args))

    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"nmap exceeded {timeout_s:.0f}s timeout — enrichment skipped"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"nmap failed to start: {exc}"

    if proc.returncode != 0 and not proc.stdout:
        # nmap prints to stderr; surface a short summary to the warnings list.
        msg = (proc.stderr or "").strip().splitlines()
        tail = msg[-1] if msg else f"exit code {proc.returncode}"
        return False, f"nmap exited {proc.returncode}: {tail[:200]}"

    try:
        parsed = parse_nmap_xml(proc.stdout)
    except ET.ParseError as exc:
        return False, f"nmap XML parse error: {exc}"

    _merge(parsed, hosts)
    return True, f"nmap enriched {len(parsed)} host(s)"


# ---------------------------------------------------------------------------
# Building the nmap command
# ---------------------------------------------------------------------------

def _build_nmap_args(
    *,
    targets: list[str],
    ports: list[int],
    do_os_detect: bool,
) -> list[str]:
    """Construct the nmap argv. Privilege mode chosen by `do_os_detect`.

    Why these flags:
      -oX -                emit XML to stdout (we parse it; never the human form)
      -Pn                  skip ping; the stdlib sweep already proved liveness
      -n                   skip DNS; we do reverse-DNS ourselves elsewhere
      -sV                  service/version detection — the entire point of nmap here
      --version-intensity 5  default-ish; trades speed against accuracy
      -T4                  aggressive timing template; fine on a LAN
      -p <list>            limit ports to what we already think are interesting
      -sS or -sT           privileged half-open vs unprivileged TCP-connect
      -O                   OS detection — needs root, omitted otherwise
      --max-retries 1      we'd rather fail fast than thrash on a flaky host
      --host-timeout 60s   bound the worst case per host
    """
    args = [
        "nmap",
        "-oX", "-",
        "-Pn", "-n",
        "-sV",
        "--version-intensity", "5",
        "-T4",
        "--max-retries", "1",
        "--host-timeout", "60s",
        "-p", ",".join(str(p) for p in ports),
    ]
    if do_os_detect:
        args.extend(["-sS", "-O"])
    else:
        args.append("-sT")
    args.extend(targets)
    return args


# ---------------------------------------------------------------------------
# XML parsing — schema reference: https://nmap.org/book/nmap-dtd.html
# ---------------------------------------------------------------------------

def parse_nmap_xml(xml_text: str) -> dict[str, dict]:
    """Parse nmap -oX output. Returns {ip: {services: [...], os: [...]}}.

    Only fields we care about are extracted. The full nmap schema has a lot
    of additional information (NSE script output, traceroute, etc.) that
    we deliberately ignore — adding them later is a one-line change here.
    """
    if not xml_text or not xml_text.strip():
        return {}

    root = ET.fromstring(xml_text)
    out: dict[str, dict] = {}

    for host in root.findall("host"):
        ip = _addr(host)
        if not ip:
            continue
        record: dict = {"services": [], "os": []}

        ports = host.find("ports")
        if ports is not None:
            for port in ports.findall("port"):
                if port.get("protocol", "tcp") != "tcp":
                    continue
                state = port.find("state")
                if state is None or state.get("state") != "open":
                    continue
                portid_str = port.get("portid")
                if not portid_str:
                    continue
                try:
                    portid = int(portid_str)
                except ValueError:
                    continue

                svc_el = port.find("service")
                if svc_el is None:
                    continue
                cpes = [c.text or "" for c in svc_el.findall("cpe") if c.text]
                record["services"].append({
                    "port": portid,
                    "name": svc_el.get("name", "") or "",
                    "product": svc_el.get("product", "") or "",
                    "version": svc_el.get("version", "") or "",
                    "extrainfo": svc_el.get("extrainfo", "") or "",
                    "cpe": cpes,
                })

        os_el = host.find("os")
        if os_el is not None:
            for match in os_el.findall("osmatch"):
                accuracy_str = match.get("accuracy", "0")
                try:
                    accuracy = int(accuracy_str)
                except ValueError:
                    accuracy = 0
                osclass = match.find("osclass")
                family = osclass.get("osfamily", "") if osclass is not None else ""
                vendor = osclass.get("vendor", "") if osclass is not None else ""
                record["os"].append({
                    "name": match.get("name", "") or "",
                    "accuracy": accuracy,
                    "family": family,
                    "vendor": vendor,
                })

        out[ip] = record

    return out


def _addr(host: ET.Element) -> str:
    for addr in host.findall("address"):
        if addr.get("addrtype") == "ipv4":
            return addr.get("addr", "") or ""
    return ""


# ---------------------------------------------------------------------------
# Merge parsed results into existing Host objects
# ---------------------------------------------------------------------------

def _merge(parsed: dict[str, dict], hosts: list[Host]) -> None:
    by_ip = {h.ip: h for h in hosts}
    for ip, record in parsed.items():
        host = by_ip.get(ip)
        if host is None:
            continue
        # Services — fill nmap-prefixed fields on the matching Service entries.
        # We do not invent new Service rows from nmap; the stdlib scan already
        # discovered every open port we care about (nmap was constrained to
        # those ports). If nmap ever reports a port we missed, we surface it
        # as a fresh Service so it's not silently dropped.
        existing_ports = {svc.port: svc for svc in host.services}
        for entry in record.get("services", []):
            port = entry["port"]
            svc = existing_ports.get(port)
            if svc is None:
                svc = Service(port=port, name=entry.get("name", ""))
                host.services.append(svc)
                existing_ports[port] = svc
            svc.nmap_product = entry.get("product", "")
            svc.nmap_version = entry.get("version", "")
            svc.nmap_extrainfo = entry.get("extrainfo", "")
            svc.nmap_cpe = list(entry.get("cpe", []))
            # Promote nmap product/version into the canonical fields when the
            # stdlib pass didn't fill them. We never overwrite a pre-existing
            # value — both fields stay visible for diagnostics.
            if not svc.product and svc.nmap_product:
                svc.product = svc.nmap_product
            if not svc.version and svc.nmap_version:
                svc.version = svc.nmap_version

        # OS guesses — keep up to 3 highest-accuracy matches.
        guesses = sorted(
            record.get("os", []),
            key=lambda g: g.get("accuracy", 0),
            reverse=True,
        )[:3]
        host.os_guesses = [
            OSGuess(
                name=g["name"],
                accuracy=int(g.get("accuracy", 0)),
                family=g.get("family", ""),
                vendor=g.get("vendor", ""),
            )
            for g in guesses
            if g.get("name")
        ]
