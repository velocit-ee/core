"""Minimal Proxmox VE REST API client used by velocitee-native.

We only need a handful of operations — VM exists?, create VM, attach ISO,
start/stop, query agent network info. The full proxmoxer surface is overkill
and pulls in dependencies we don't otherwise need.

Auth uses an API token (PROXMOX_VE_API_TOKEN env var). Form:

    user@realm!tokenid=secret

The 'PVEAPIToken=' header carries that whole string. SSL verification is on
by default and only disabled if PROXMOX_VE_INSECURE=1 — homelabs commonly run
self-signed Proxmox certs and we don't want to make the workaround painful,
but we want it to be deliberate.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any
from urllib.parse import urlparse

import requests

log = logging.getLogger("velocitee.proxmox")


class ProxmoxAPIError(RuntimeError):
    """API call returned a non-2xx response or unexpected body."""


class ProxmoxClient:
    def __init__(
        self,
        endpoint: str,
        token: str,
        *,
        verify_ssl: bool | None = None,
        timeout: float = 30.0,
    ):
        # endpoint e.g. "https://192.168.1.10:8006"
        if "://" not in endpoint:
            endpoint = f"https://{endpoint}:8006"
        parsed = urlparse(endpoint)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"PROXMOX_VE_ENDPOINT must be http(s): got {endpoint!r}")
        self.base = endpoint.rstrip("/") + "/api2/json"

        if "=" not in token or "!" not in token:
            raise ValueError(
                "PROXMOX_VE_API_TOKEN must look like 'user@realm!tokenid=secret'"
            )

        if verify_ssl is None:
            verify_ssl = os.environ.get("PROXMOX_VE_INSECURE", "0") != "1"

        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"PVEAPIToken={token}",
            "Accept": "application/json",
        })
        self._session.verify = verify_ssl
        self._timeout = timeout

        if not verify_ssl:
            # Suppress the ocean of warnings without globally disabling them.
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
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise ProxmoxAPIError(f"{method} {path} failed: {exc}") from exc

        if resp.status_code >= 400:
            raise ProxmoxAPIError(
                f"{method} {path} → HTTP {resp.status_code}: {resp.text.strip()[:500]}"
            )
        try:
            return resp.json().get("data")
        except ValueError as exc:
            raise ProxmoxAPIError(f"{method} {path} returned non-JSON: {resp.text[:200]}") from exc

    # -----------------------------------------------------------------
    # Cluster / nodes
    # -----------------------------------------------------------------

    def list_nodes(self) -> list[dict[str, Any]]:
        return self._request("GET", "/nodes") or []

    def first_node(self) -> str:
        nodes = self.list_nodes()
        if not nodes:
            raise ProxmoxAPIError("no Proxmox nodes returned by API")
        return nodes[0]["node"]

    # -----------------------------------------------------------------
    # VM lifecycle (idempotent)
    # -----------------------------------------------------------------

    def vm_exists(self, node: str, vmid: int) -> bool:
        try:
            self._request("GET", f"/nodes/{node}/qemu/{vmid}/config")
        except ProxmoxAPIError as exc:
            if "500" in str(exc) or "404" in str(exc) or "not exist" in str(exc).lower():
                return False
            raise
        return True

    def vm_status(self, node: str, vmid: int) -> str:
        """Return 'running', 'stopped', or 'unknown'."""
        try:
            data = self._request("GET", f"/nodes/{node}/qemu/{vmid}/status/current")
            return data.get("status") if isinstance(data, dict) else "unknown"
        except ProxmoxAPIError:
            return "unknown"

    def create_vm(
        self,
        node: str,
        vmid: int,
        *,
        name: str,
        cores: int,
        memory_mb: int,
        disk_gb: int,
        storage_pool: str,
        iso_volume_id: str,
        bridges: list[str],
        bios: str = "ovmf",
        machine: str = "q35",
        cpu_type: str = "host",
    ) -> None:
        """Create the VM. Idempotent: no-op if a VM with this vmid already exists."""
        if self.vm_exists(node, vmid):
            log.info("proxmox: VM %d already exists, skipping create", vmid)
            return

        params: dict[str, Any] = {
            "vmid": vmid,
            "name": name,
            "cores": cores,
            "memory": memory_mb,
            "ostype": "other",
            "bios": bios,
            "machine": machine,
            "cpu": cpu_type,
            "scsihw": "virtio-scsi-single",
            "scsi0": f"{storage_pool}:{disk_gb},format=qcow2",
            "ide2": f"{iso_volume_id},media=cdrom",
            "boot": "order=ide2;scsi0",
            "agent": "1",
            "onboot": 1,
        }
        # netN: virtio bridged adapters, one per requested bridge.
        for i, bridge in enumerate(bridges):
            params[f"net{i}"] = f"virtio,bridge={bridge}"

        log.info("proxmox: creating VM %d on node %s", vmid, node)
        self._request("POST", f"/nodes/{node}/qemu", data=params)

    def start_vm(self, node: str, vmid: int) -> None:
        if self.vm_status(node, vmid) == "running":
            log.info("proxmox: VM %d already running", vmid)
            return
        log.info("proxmox: starting VM %d", vmid)
        self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/start")

    def stop_vm(self, node: str, vmid: int) -> None:
        if self.vm_status(node, vmid) == "stopped":
            return
        self._request("POST", f"/nodes/{node}/qemu/{vmid}/status/stop")

    # -----------------------------------------------------------------
    # ISO upload / volume probe
    # -----------------------------------------------------------------

    def find_iso_volume(self, node: str, storage: str, basename: str) -> str | None:
        """Return the volume id (e.g. 'local:iso/foo.iso') or None.

        Idempotency check: 'have we already uploaded this ISO?' before fetching.
        """
        content = self._request("GET", f"/nodes/{node}/storage/{storage}/content",
                                params={"content": "iso"}) or []
        for item in content:
            volid = item.get("volid", "")
            if volid.endswith("/" + basename) or volid.endswith(":iso/" + basename):
                return volid
        return None

    def download_iso(
        self,
        node: str,
        storage: str,
        url: str,
        filename: str,
        *,
        checksum: str | None = None,
    ) -> str:
        """Ask Proxmox to fetch an ISO from a URL into a storage pool.

        Idempotent: returns the existing volume id if the ISO is already there.
        Otherwise issues download-url and waits for the task to finish.
        """
        existing = self.find_iso_volume(node, storage, filename)
        if existing:
            log.info("proxmox: ISO %s already present at %s", filename, existing)
            return existing

        params: dict[str, Any] = {
            "content": "iso",
            "filename": filename,
            "url": url,
        }
        if checksum:
            algo, _, hexdigest = checksum.partition(":")
            params["checksum"] = hexdigest
            params["checksum-algorithm"] = algo

        log.info("proxmox: downloading ISO %s into %s", filename, storage)
        upid = self._request(
            "POST",
            f"/nodes/{node}/storage/{storage}/download-url",
            data=params,
        )
        if isinstance(upid, str):
            self._wait_task(node, upid)
        return f"{storage}:iso/{filename}"

    # -----------------------------------------------------------------
    # Cloud-init / config injection
    # -----------------------------------------------------------------

    def attach_extra_iso(self, node: str, vmid: int, slot: str, volid: str) -> None:
        """Attach an additional ISO (e.g. config.xml ISO) to a free IDE slot."""
        log.info("proxmox: attaching %s as %s on VM %d", volid, slot, vmid)
        self._request(
            "PUT",
            f"/nodes/{node}/qemu/{vmid}/config",
            data={slot: f"{volid},media=cdrom"},
        )

    # -----------------------------------------------------------------
    # Guest agent — needed to discover the OPNsense LAN IP
    # -----------------------------------------------------------------

    def guest_agent_ip(
        self,
        node: str,
        vmid: int,
        interface_substring: str | None = None,
    ) -> str | None:
        """Query qemu-guest-agent for IPv4 addresses.

        Returns the first non-loopback IPv4 found. If interface_substring is
        provided, prefer interfaces whose name contains that substring (so we
        can target LAN over WAN when both are up).
        """
        try:
            data = self._request(
                "GET",
                f"/nodes/{node}/qemu/{vmid}/agent/network-get-interfaces",
            )
        except ProxmoxAPIError as exc:
            log.debug("proxmox: guest-agent not yet available: %s", exc)
            return None

        if not data or not isinstance(data, dict):
            return None
        interfaces = data.get("result") or []

        def ipv4_for(iface: dict[str, Any]) -> str | None:
            for addr in iface.get("ip-addresses", []) or []:
                if addr.get("ip-address-type") == "ipv4":
                    ip = addr.get("ip-address")
                    if ip and not ip.startswith("127."):
                        return ip
            return None

        if interface_substring:
            for iface in interfaces:
                if interface_substring in iface.get("name", ""):
                    ip = ipv4_for(iface)
                    if ip:
                        return ip
        for iface in interfaces:
            ip = ipv4_for(iface)
            if ip:
                return ip
        return None

    def wait_for_guest_ip(
        self,
        node: str,
        vmid: int,
        *,
        timeout: float = 600.0,
        interval: float = 5.0,
        interface_substring: str | None = None,
    ) -> str:
        deadline = time.monotonic() + timeout
        last_err: str | None = None
        while time.monotonic() < deadline:
            try:
                ip = self.guest_agent_ip(node, vmid, interface_substring=interface_substring)
                if ip:
                    return ip
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)
            time.sleep(interval)
        raise ProxmoxAPIError(
            f"timed out waiting for guest agent IP on VM {vmid} after {timeout}s"
            + (f" (last error: {last_err})" if last_err else "")
        )

    # -----------------------------------------------------------------
    # Bridge / VLAN-aware bridge management
    # -----------------------------------------------------------------

    def list_bridges(self, node: str) -> list[dict[str, Any]]:
        try:
            return self._request("GET", f"/nodes/{node}/network",
                                 params={"type": "any_bridge"}) or []
        except ProxmoxAPIError:
            # Fallback when filter is rejected on older PVE
            return [n for n in (self._request("GET", f"/nodes/{node}/network") or [])
                    if n.get("type") == "bridge"]

    def ensure_vlan_aware_bridge(self, node: str, name: str) -> None:
        """Create a vmbr if it does not exist; flip vlan_aware on if it isn't."""
        bridges = self.list_bridges(node)
        existing = next((b for b in bridges if b.get("iface") == name), None)
        if existing is None:
            log.info("proxmox: creating bridge %s", name)
            self._request("POST", f"/nodes/{node}/network", data={
                "iface": name,
                "type": "bridge",
                "autostart": 1,
                "bridge_vlan_aware": 1,
                "bridge_ports": "none",
            })
            self._reload_network(node)
            return

        if not existing.get("bridge_vlan_aware"):
            log.info("proxmox: enabling vlan_aware on bridge %s", name)
            self._request("PUT", f"/nodes/{node}/network/{name}", data={
                "type": "bridge",
                "bridge_vlan_aware": 1,
            })
            self._reload_network(node)

    def _reload_network(self, node: str) -> None:
        try:
            self._request("PUT", f"/nodes/{node}/network")
        except ProxmoxAPIError as exc:
            log.warning("proxmox: network reload failed (may be transient): %s", exc)

    # -----------------------------------------------------------------
    # Tasks
    # -----------------------------------------------------------------

    def _wait_task(self, node: str, upid: str, *, timeout: float = 1800.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self._request("GET", f"/nodes/{node}/tasks/{upid}/status")
            if not isinstance(data, dict):
                time.sleep(2)
                continue
            if data.get("status") == "stopped":
                exitstatus = data.get("exitstatus")
                if exitstatus and exitstatus != "OK":
                    raise ProxmoxAPIError(f"task {upid} ended with exit '{exitstatus}'")
                return
            time.sleep(2)
        raise ProxmoxAPIError(f"task {upid} timed out after {timeout}s")
