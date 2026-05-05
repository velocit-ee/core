"""OPNsense adapter — enriched join when the gateway is an OPNsense box.

If credentials are supplied (OPNSENSE_API_KEY/OPNSENSE_API_SECRET in the
environment), we pull the live VLAN list, DHCP scopes, and DNS settings
straight from the API. Operators get a manifest that fully describes the
joined network, and downstream engines can interact with the firewall.

If credentials are *not* supplied, we degrade to the same observation-only
manifest the unmanaged adapter produces — but with `vendor: opnsense`
populated, so VSE/VLE can still tell what they're talking to.

The adapter never *modifies* the gateway during a join. Joining is read-only;
mutation only happens later when the operator runs `vne deploy` against
this manifest with explicit intent.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from .base import AdapterResult, RouterAdapter, register
from ..report import Capability

log = logging.getLogger("velocitee.discovery.adapter.opnsense")


class OPNsenseAdapter(RouterAdapter):
    slug = "opnsense"
    description = (
        "Enriched join for OPNsense gateways. Uses OPNSENSE_API_KEY/SECRET if set "
        "to pull VLAN, DHCP, and DNS settings; otherwise falls back to observed state."
    )

    @classmethod
    def matches(cls, report) -> bool:  # type: ignore[no-untyped-def]
        return report.router.api_kind == "opnsense"

    def capabilities(self) -> list[Capability]:
        have_creds = bool(os.environ.get("OPNSENSE_API_KEY") and os.environ.get("OPNSENSE_API_SECRET"))
        return [
            Capability(
                name="firewall_managed",
                available=have_creds,
                reason=(
                    "OPNsense API credentials present — VNE can manage firewall rules"
                    if have_creds
                    else "OPNSENSE_API_KEY/SECRET not set — falling back to observation only"
                ),
            ),
            Capability(
                name="vlan_managed",
                available=have_creds,
                reason=(
                    "OPNsense API credentials present — VNE can read/write VLANs"
                    if have_creds
                    else "no OPNsense credentials — VLANs recorded only as observed"
                ),
            ),
            Capability(
                name="dhcp_managed",
                available=have_creds,
                reason=(
                    "OPNsense API credentials present — VNE can read/write DHCP scopes"
                    if have_creds
                    else "no OPNsense credentials — DHCP scopes recorded only as observed"
                ),
            ),
            Capability(
                name="documentation_only",
                available=True,
                reason="manifest captures observed state regardless of credentials",
            ),
        ]

    def execute(self) -> AdapterResult:
        report = self.report
        router = report.router

        api_key = os.environ.get("OPNSENSE_API_KEY", "")
        api_secret = os.environ.get("OPNSENSE_API_SECRET", "")

        live: dict[str, Any] = {}
        creds_present = bool(api_key and api_secret)
        if creds_present and router.api_endpoint:
            try:
                live = self._pull_live_state(
                    endpoint=router.api_endpoint.replace("/api", "").rstrip("/"),
                    api_key=api_key,
                    api_secret=api_secret,
                )
            except Exception as exc:  # noqa: BLE001 — network/API errors are non-fatal
                log.warning("OPNsense live pull failed: %s — degrading to observed-only", exc)
                live = {"error": str(exc)}

        fragment = {
            "mode": "join",
            "adapter": self.slug,
            "gateway": {
                "ip": router.ip,
                "vendor": router.vendor,
                "product": router.product,
                "version": router.version,
                "confidence": round(router.confidence, 2),
                "managed": creds_present and not live.get("error"),
                "api_endpoint": router.api_endpoint,
            },
            "dns": {
                "resolvers": list(report.network.dns_resolvers),
                "search_domains": list(report.network.search_domains),
            },
            "observed_cidrs": list(report.scan_scope.cidrs),
            "observed_vlans": [
                {"id": v.id, "cidr": v.cidr, "gateway": v.gateway, "source": v.source}
                for v in report.vlans
            ],
            "discovery_report": "discovery-report.json",
            "live": live,
        }

        return AdapterResult(
            success=True,
            adapter=self.slug,
            capabilities=self.capabilities(),
            manifest_fragment=fragment,
            state={"adapter": self.slug, "router_ip": router.ip},
        )

    # -------------------------------------------------------------------
    # Live pull
    # -------------------------------------------------------------------

    def _pull_live_state(self, *, endpoint: str, api_key: str, api_secret: str) -> dict[str, Any]:
        """Use the existing OPNsenseClient to read VLAN / interface / DHCP state.

        Imported here rather than at module top to keep `requests` out of the
        import path of unrelated callers (e.g. `velocitee-discover scan` on a
        host without `requests` should still work — it has the optional
        runtime dep but only uses it for this one adapter).
        """
        from ...renderers._opnsense_client import OPNsenseAPIError, OPNsenseClient

        client = OPNsenseClient(
            endpoint=endpoint,
            api_key=api_key,
            api_secret=api_secret,
            verify_ssl=os.environ.get("OPNSENSE_INSECURE") != "1",
        )
        try:
            client.ping()
        except OPNsenseAPIError as exc:
            raise RuntimeError(f"OPNsense ping failed: {exc}") from exc

        out: dict[str, Any] = {}
        try:
            out["vlans"] = client.list_vlans()
        except OPNsenseAPIError as exc:
            out["vlans_error"] = str(exc)
        try:
            out["interface_assignments"] = client.get_interface_assignments()
        except OPNsenseAPIError as exc:
            out["interface_assignments_error"] = str(exc)
        try:
            out["dhcpv4"] = client.get_dhcpv4()
        except OPNsenseAPIError as exc:
            out["dhcpv4_error"] = str(exc)
        try:
            out["filter_rules_count"] = len(client.list_filter_rules())
        except OPNsenseAPIError as exc:
            out["filter_rules_error"] = str(exc)

        return out


register("opnsense", OPNsenseAdapter)
