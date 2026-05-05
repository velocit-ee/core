"""Unmanaged adapter — works on any network, requires no API access.

This is the catch-all and the floor of the system: VNE Path B can always
proceed via this adapter, even when the gateway is something exotic we
haven't written a vendor-specific adapter for. The trade-off is that
downstream engines (VSE/VLE) only get the *observed* state, not a managed
one — drift detection compares against the snapshot, no enforcement.

The manifest fragment we produce contains exactly what discovery saw —
gateway, DNS, observed CIDRs, observed VLANs, services. Nothing
synthesised, nothing assumed.
"""

from __future__ import annotations

from .base import AdapterResult, RouterAdapter, register
from ..report import Capability


class UnmanagedAdapter(RouterAdapter):
    slug = "unmanaged"
    description = (
        "Vendor-agnostic join. Records what discovery saw; no router API "
        "integration. Always available."
    )

    @classmethod
    def matches(cls, report) -> bool:  # type: ignore[no-untyped-def]
        return True  # always

    def capabilities(self) -> list[Capability]:
        return [
            Capability(
                name="firewall_managed",
                available=False,
                reason="unmanaged adapter — VNE will not write firewall rules into the router",
            ),
            Capability(
                name="vlan_managed",
                available=False,
                reason="unmanaged adapter — VLANs are recorded only as observed",
            ),
            Capability(
                name="dhcp_managed",
                available=False,
                reason="unmanaged adapter — DHCP scopes are recorded only as observed",
            ),
            Capability(
                name="documentation_only",
                available=True,
                reason="manifest captures the observed network state for VSE/VLE consumption",
            ),
        ]

    def execute(self) -> AdapterResult:
        report = self.report
        net = report.network
        router = report.router

        observed_vlans = [
            {
                "id": v.id,
                "cidr": v.cidr,
                "gateway": v.gateway,
                "source": v.source,
            }
            for v in report.vlans
        ]

        observed_services = [
            {
                "ip": h.ip,
                "hostname": h.hostname,
                "ports": [s.port for s in h.services],
                "roles": list(h.role_hints),
            }
            for h in report.hosts
            if h.services or h.role_hints
        ]

        fragment = {
            "mode": "join",
            "adapter": self.slug,
            "gateway": {
                "ip": router.ip or net.default_gateway,
                "vendor": router.vendor or "unknown",
                "product": router.product,
                "version": router.version,
                "confidence": round(router.confidence, 2),
                "managed": False,
            },
            "dns": {
                "resolvers": list(net.dns_resolvers),
                "search_domains": list(net.search_domains),
            },
            "observed_cidrs": list(report.scan_scope.cidrs),
            "observed_vlans": observed_vlans,
            "observed_services": observed_services,
            "discovery_report": "discovery-report.json",
        }

        return AdapterResult(
            success=True,
            adapter=self.slug,
            capabilities=self.capabilities(),
            manifest_fragment=fragment,
            state={"adapter": self.slug, "router_ip": router.ip or net.default_gateway},
        )


register("unmanaged", UnmanagedAdapter)
