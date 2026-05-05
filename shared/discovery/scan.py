"""Top-level discovery orchestrator.

run_discovery() ties together the four sources and returns a DiscoveryReport.

Order matters:
  1. Local introspection — free, never fails.
  2. Passive — listens for what's already advertising itself; primes the
     active sweep with hosts we don't have to probe.
  3. Active sweep — finds responsive hosts in the target CIDRs.
  4. Connect-scan + fingerprint — populates services on each alive host.
  5. Router identification — picks the best vendor match for the gateway.
  6. Capability synthesis — writes a flat list of yes/no flags VSE/VLE read
     to gate features ('snmp_writeable', 'opnsense_api_creds', ...).

Every layer is failure-tolerant: a network with no multicast still produces
a useful report; a target with no SNMP just lacks SNMP signals.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable

from . import active, fingerprint, network, passive, routers
from .report import (
    Capability,
    DiscoveryReport,
    Host,
    RouterInfo,
    ScanScope,
    VLANObservation,
)

log = logging.getLogger("velocitee.discovery.scan")


def run_discovery(
    *,
    cidrs: list[str] | None = None,
    iface: str = "",
    ports: Iterable[int] | None = None,
    passive_seconds: int = 6,
    do_active: bool = True,
    do_fingerprint: bool = True,
    snmp_community: str = "",
    timeout_s: float = 0.6,
    workers: int = 256,
) -> DiscoveryReport:
    """Run a discovery scan and return a DiscoveryReport.

    `cidrs`: explicit target CIDRs. If empty, derived from the local default
             gateway's interface. Passing [] disables active scanning unless
             a CIDR can be inferred — in which case we use the inferred one.

    `iface`: bind multicast listeners to this interface IP if set. Discovery
             still works without it on hosts with a single up interface.
    """
    started = time.monotonic()
    warnings: list[str] = []

    # 1. Local
    net_info, w = network.collect_local()
    warnings.extend(w)

    if not cidrs:
        cidrs = network.derive_default_cidrs(net_info)
        if cidrs:
            log.info("discovery: defaulting to local CIDR(s) %s", ", ".join(cidrs))

    iface_ip = ""
    if iface:
        for li in net_info.interfaces:
            if li.name == iface and li.ipv4:
                iface_ip = li.ipv4[0]
                break
    elif net_info.default_gateway_iface:
        for li in net_info.interfaces:
            if li.name == net_info.default_gateway_iface and li.ipv4:
                iface_ip = li.ipv4[0]
                break

    # 2. Passive
    passive_hosts, w = passive.collect(
        listen_seconds=passive_seconds,
        iface_ip=iface_ip,
    )
    warnings.extend(w)
    by_ip: dict[str, Host] = {h.ip: h for h in passive_hosts}

    # 3. Active sweep over requested CIDRs
    alive: list[str] = []
    if do_active and cidrs:
        alive = active.sweep(cidrs, timeout_s=timeout_s, workers=workers)
        log.info("discovery: sweep found %d alive hosts in %s", len(alive), ", ".join(cidrs))
    # Always promote passive findings to active probing too — they're known live.
    extra = [ip for ip in by_ip if ip not in set(alive)]
    alive_set = sorted(set(alive) | set(extra),
                       key=lambda ip: tuple(int(o) for o in ip.split(".")))

    # 4. Connect-scan + fingerprint
    services_by_ip: dict[str, list] = {}
    if do_active and alive_set:
        ports_tuple = tuple(ports) if ports else active.DEFAULT_PORTS
        services_by_ip = active.connect_scan(
            alive_set, ports=ports_tuple, timeout_s=timeout_s, workers=workers,
        )

    hosts = active.annotate_hosts(
        ips=alive_set,
        services_by_ip=services_by_ip,
        seed=by_ip,
    )

    if do_fingerprint and hosts:
        fingerprint.fingerprint_hosts(
            hosts, timeout_s=max(timeout_s * 2.5, 1.5),
            snmp_community=snmp_community,
        )

    # 5. Router identification — find the gateway in the host list
    gateway_host = next((h for h in hosts if h.ip == net_info.default_gateway), None)
    router = routers.identify(gateway_host, hosts)
    routers.annotate_role_hints(
        hosts,
        gateway_ip=net_info.default_gateway,
        dns_ips=set(net_info.dns_resolvers),
    )
    routers.merge_router_into_host_hints(router, hosts)

    # 6. VLAN observations from local interfaces
    vlans = _vlans_from_interfaces(net_info)

    # 7. Capabilities
    capabilities = _synthesize_capabilities(router, snmp_community=snmp_community)

    duration = time.monotonic() - started
    return DiscoveryReport(
        scan_scope=ScanScope(
            cidrs=cidrs or [],
            iface=iface,
            ports=list(ports or active.DEFAULT_PORTS),
            passive_seconds=passive_seconds,
            active=do_active,
            fingerprint=do_fingerprint,
            snmp_community="<set>" if snmp_community else "",
        ),
        network=net_info,
        router=router,
        hosts=hosts,
        vlans=vlans,
        capabilities=capabilities,
        warnings=warnings,
        duration_seconds=round(duration, 2),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _vlans_from_interfaces(net_info) -> list[VLANObservation]:  # type: ignore[no-untyped-def]
    out: list[VLANObservation] = []
    for li in net_info.interfaces:
        if not li.is_vlan or li.vlan_id is None:
            continue
        cidr = li.cidr[0] if li.cidr else ""
        out.append(VLANObservation(
            id=li.vlan_id,
            cidr=cidr,
            source=f"iface:{li.name}",
        ))
    return out


def _synthesize_capabilities(router: RouterInfo, *, snmp_community: str) -> list[Capability]:
    """Translate the router/router-API findings into VSE/VLE-readable flags."""
    caps: list[Capability] = []

    is_managed = bool(router.api_kind) and router.confidence >= 0.6
    caps.append(Capability(
        name="managed_router",
        available=is_managed,
        reason=(
            f"detected {router.vendor} with API '{router.api_kind}' "
            f"at confidence {router.confidence:.2f}"
            if is_managed
            else "no recognised management API on the gateway — VNE will treat it as unmanaged"
        ),
    ))

    caps.append(Capability(
        name="opnsense_api_creds",
        available=router.api_kind == "opnsense",
        reason=(
            "OPNsense API endpoint detected — supply OPNSENSE_API_KEY/SECRET to enable rich integration"
            if router.api_kind == "opnsense"
            else "OPNsense not detected on the gateway"
        ),
    ))

    caps.append(Capability(
        name="snmp_polling",
        available=bool(snmp_community),
        reason=(
            "SNMP community supplied — VLE drift checks may use SNMP polling"
            if snmp_community
            else "no SNMP community supplied to discovery; VLE will not poll SNMP"
        ),
    ))

    caps.append(Capability(
        name="vlan_aware",
        available=False,  # only true if we explicitly observed VLAN trunking; refine later
        reason=(
            "VLAN trunking on the seed host's link is not auto-detected; "
            "set this manually in velocitee.yml if you've put us on a trunk port"
        ),
    ))

    return caps
