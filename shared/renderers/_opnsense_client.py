"""Minimal OPNsense REST API client.

OPNsense's API is HTTPS Basic-auth using an API key/secret pair. Endpoints we
care about:

  Interfaces (VLANs):
    GET  /api/interfaces/vlan_settings/searchItem
    POST /api/interfaces/vlan_settings/addItem
    POST /api/interfaces/vlan_settings/setItem/<uuid>
    POST /api/interfaces/vlan_settings/delItem/<uuid>
    POST /api/interfaces/vlan_settings/reconfigure

  DHCP (ISC kea / classic):
    GET  /api/dhcpv4/leases/searchLease
    GET  /api/kea/dhcpv4/get
    POST /api/kea/dhcpv4/set
    POST /api/kea/service/reconfigure

  DNS (Unbound):
    GET  /api/unbound/settings/get
    POST /api/unbound/settings/set
    POST /api/unbound/service/reconfigure

  Firewall:
    GET  /api/firewall/filter/searchRule
    POST /api/firewall/filter/addRule
    POST /api/firewall/filter/setRule/<uuid>
    POST /api/firewall/filter/delRule/<uuid>
    POST /api/firewall/filter/apply

The client below only implements the verbs we need. Each method documents
which OPNsense endpoint it talks to. We treat 4xx as fatal, 5xx as retryable
with a small backoff (the API can hiccup briefly during 'reconfigure' calls).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger("velocitee.opnsense")


class OPNsenseAPIError(RuntimeError):
    pass


class OPNsenseClient:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        api_secret: str,
        *,
        verify_ssl: bool | None = None,
        timeout: float = 30.0,
    ):
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}"
        self.base = endpoint.rstrip("/") + "/api"

        if verify_ssl is None:
            verify_ssl = os.environ.get("OPNSENSE_INSECURE", "0") != "1"

        self._session = requests.Session()
        self._session.auth = (api_key, api_secret)
        self._session.headers.update({"Accept": "application/json"})
        self._session.verify = verify_ssl
        self._timeout = timeout

        if not verify_ssl:
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            except ImportError:
                pass

    # -----------------------------------------------------------------
    # Low level
    # -----------------------------------------------------------------

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = self.base + path
        kwargs.setdefault("timeout", self._timeout)
        attempts = 0
        last_exc: Exception | None = None
        while attempts < 3:
            attempts += 1
            try:
                resp = self._session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(min(2 ** attempts, 8))
                continue
            if 500 <= resp.status_code < 600:
                last_exc = OPNsenseAPIError(
                    f"{method} {path} → HTTP {resp.status_code}"
                )
                time.sleep(min(2 ** attempts, 8))
                continue
            if resp.status_code >= 400:
                raise OPNsenseAPIError(
                    f"{method} {path} → HTTP {resp.status_code}: "
                    f"{resp.text.strip()[:500]}"
                )
            try:
                return resp.json()
            except ValueError as exc:
                raise OPNsenseAPIError(
                    f"{method} {path} returned non-JSON"
                ) from exc

        raise OPNsenseAPIError(f"{method} {path} failed after retries: {last_exc}")

    def _get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def _post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json or {})

    # -----------------------------------------------------------------
    # Reachability probe
    # -----------------------------------------------------------------

    def ping(self) -> bool:
        try:
            self._get("/core/system/status")
            return True
        except OPNsenseAPIError:
            return False

    def wait_until_ready(self, *, timeout: float = 900.0, interval: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.ping():
                return
            time.sleep(interval)
        raise OPNsenseAPIError(
            f"OPNsense API at {self.base} did not become ready within {timeout}s"
        )

    # -----------------------------------------------------------------
    # VLANs
    # -----------------------------------------------------------------

    def list_vlans(self) -> list[dict[str, Any]]:
        data = self._get("/interfaces/vlan_settings/searchItem")
        return (data or {}).get("rows", []) or []

    def find_vlan(self, tag: int, parent: str) -> dict[str, Any] | None:
        for v in self.list_vlans():
            if str(v.get("tag")) == str(tag) and v.get("if") == parent:
                return v
        return None

    def upsert_vlan(self, *, tag: int, parent: str, descr: str) -> str:
        """Create-or-update a VLAN. Returns the UUID."""
        existing = self.find_vlan(tag, parent)
        body = {"vlan": {"if": parent, "tag": str(tag), "descr": descr, "pcp": "0"}}
        if existing:
            uuid = existing["uuid"]
            log.info("opnsense: updating VLAN %s on %s (%s)", tag, parent, uuid)
            self._post(f"/interfaces/vlan_settings/setItem/{uuid}", json=body)
            return uuid
        log.info("opnsense: creating VLAN %s on %s", tag, parent)
        result = self._post("/interfaces/vlan_settings/addItem", json=body)
        if not isinstance(result, dict) or "uuid" not in result:
            raise OPNsenseAPIError(f"unexpected addItem response: {result}")
        return result["uuid"]

    def reconfigure_vlans(self) -> None:
        self._post("/interfaces/vlan_settings/reconfigure")

    # -----------------------------------------------------------------
    # Interface assignment (associate the VLAN device with an opt interface)
    # -----------------------------------------------------------------

    def get_interface_assignments(self) -> dict[str, Any]:
        return self._get("/interfaces/overview/interfacesInfo") or {}

    # -----------------------------------------------------------------
    # DHCP — Kea
    # -----------------------------------------------------------------

    def get_dhcpv4(self) -> dict[str, Any]:
        return self._get("/kea/dhcpv4/get") or {}

    def set_dhcpv4(self, payload: dict[str, Any]) -> None:
        self._post("/kea/dhcpv4/set", json=payload)

    def reconfigure_dhcp(self) -> None:
        self._post("/kea/service/reconfigure")

    def upsert_dhcp_subnet(
        self,
        *,
        cidr: str,
        gateway: str,
        pool_start: str,
        pool_end: str,
        dns_servers: list[str],
        domain: str,
        lease_time: int,
    ) -> None:
        """Idempotently add or update a Kea DHCP subnet for a given CIDR."""
        current = self.get_dhcpv4()
        # Schema is nested: {dhcpv4: {subnets: {subnet4: {<uuid>: {…}}}}}
        config = current.get("dhcpv4", {})
        subnets_root = config.setdefault("subnets", {})
        subnet4 = subnets_root.setdefault("subnet4", {})

        target_uuid = None
        for uuid, subnet in subnet4.items():
            if subnet.get("subnet") == cidr:
                target_uuid = uuid
                break

        subnet_body = {
            "subnet": cidr,
            "next_server": gateway,
            "pools": f"{pool_start}-{pool_end}",
            "valid_lifetime": str(lease_time),
            "option_data_autocollect": "1",
            "option_data": {
                "router": gateway,
                "domain_name_servers": ",".join(dns_servers),
                "domain_name": domain,
            },
        }

        if target_uuid:
            log.info("opnsense: updating DHCP subnet %s (%s)", cidr, target_uuid)
            subnet4[target_uuid] = subnet_body
        else:
            new_uuid = f"{cidr.replace('/', '_').replace('.', '-')}"
            log.info("opnsense: creating DHCP subnet %s", cidr)
            subnet4[new_uuid] = subnet_body

        self.set_dhcpv4({"dhcpv4": {"subnets": {"subnet4": subnet4}}})

    # -----------------------------------------------------------------
    # DNS — Unbound
    # -----------------------------------------------------------------

    def configure_unbound(self, *, upstream: list[str], domain: str) -> None:
        """Set Unbound to forward to upstream resolvers, with the given local domain."""
        log.info("opnsense: configuring Unbound (domain=%s, upstream=%s)", domain, upstream)
        # Unbound general settings — schema differs slightly between OPNsense versions;
        # we use the post-22.x layout.
        self._post("/unbound/settings/set", json={
            "unbound": {
                "general": {
                    "enabled": "1",
                    "active_interface": "lan",
                    "outgoing_interface": "wan",
                    "domain": domain,
                    "forwarding": "1",
                },
                "dnsbl": {"enabled": "0"},
                "forwarding": {
                    "queryServerSelection": "0",
                },
            }
        })
        # Forwarders aren't part of the same set call in newer OPNsense.
        # We patch them via the dedicated endpoint:
        self._post("/unbound/settings/set", json={
            "unbound": {"forwards": [
                {"enabled": "1", "domain": ".", "server": s} for s in upstream
            ]}
        })
        self._post("/unbound/service/reconfigure")

    # -----------------------------------------------------------------
    # Firewall
    # -----------------------------------------------------------------

    def list_filter_rules(self) -> list[dict[str, Any]]:
        data = self._get("/firewall/filter/searchRule")
        return (data or {}).get("rows", []) or []

    def upsert_filter_rule(self, body: dict[str, Any]) -> str:
        """Create-or-update a filter rule. Match key is the description string —
        the renderer is responsible for using stable descriptions."""
        descr = body.get("rule", {}).get("description") or body.get("description")
        if not descr:
            raise OPNsenseAPIError("upsert_filter_rule requires a description")

        for rule in self.list_filter_rules():
            if rule.get("description") == descr:
                uuid = rule["uuid"]
                log.info("opnsense: updating firewall rule '%s' (%s)", descr, uuid)
                self._post(f"/firewall/filter/setRule/{uuid}", json=body)
                return uuid

        log.info("opnsense: creating firewall rule '%s'", descr)
        result = self._post("/firewall/filter/addRule", json=body)
        if not isinstance(result, dict) or "uuid" not in result:
            raise OPNsenseAPIError(f"unexpected addRule response: {result}")
        return result["uuid"]

    def apply_firewall(self) -> None:
        self._post("/firewall/filter/apply")

    def set_default_policy(self, *, block: bool) -> None:
        """OPNsense default policy is set per-interface via 'block all' rule.

        We add a low-priority block rule on each interface when block=True; we
        remove it when block=False. The renderer typically calls this after
        adding allow rules so the implicit default doesn't strand the user.
        """
        descr = "velocitee: default-block (managed)"
        body = {
            "rule": {
                "type": "block",
                "interface": "any",
                "ipprotocol": "inet",
                "direction": "in",
                "description": descr,
                "source_net": "any",
                "destination_net": "any",
                "enabled": "1" if block else "0",
            }
        }
        self.upsert_filter_rule(body)
        self.apply_firewall()
