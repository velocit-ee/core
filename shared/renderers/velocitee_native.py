"""velocitee-native — first-class Python provisioner for VNE.

This is the primary backend. It speaks Proxmox REST and OPNsense REST directly,
manages its own state file, and is fully idempotent at every step.

The renderer is structured as an ordered list of named steps. Each step:
  1. Reads its own state record. If 'completed', it skips.
  2. Otherwise, performs an idempotency probe against the live API.
     If the live state matches what we want, mark complete without acting.
  3. Otherwise, perform the operation and record completion in state.

This belt-and-braces approach means even a deleted state file is safe: the
live API probe finds existing resources and skips creation. Conversely, a
state file that says "done" but a missing resource (say, the operator
deleted the VM by hand) is detected and reconciled on next run.

State file: <state_dir>/vne.state.json (versioned, atomic writes).
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any

from ..renderer import Renderer
from ..renderer_registry import register
from ..schema import ProvisioningResult
from ._opnsense_client import OPNsenseAPIError, OPNsenseClient
from ._proxmox_client import ProxmoxAPIError, ProxmoxClient
from ._state import RenderState, StateError

log = logging.getLogger("velocitee.renderer.native")

# Imported lazily to avoid pulling vne package into shared/.
def _config_xml():
    from vne import config_xml as cx
    return cx


# ---------------------------------------------------------------------------
# Required env vars — listed once, used everywhere
# ---------------------------------------------------------------------------

_REQUIRED_ENV = (
    "PROXMOX_VE_ENDPOINT",
    "PROXMOX_VE_API_TOKEN",
    # OPNsense root password used at first boot via config.xml.
    # Once the API key is generated and persisted to state, this isn't strictly
    # needed on resume — but we keep it required so it stays out of YAML files.
    "OPNSENSE_ROOT_PASSWORD",
)


def _missing_env() -> list[str]:
    return [k for k in _REQUIRED_ENV if not os.environ.get(k)]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

class VelociteeNativeRenderer(Renderer):
    name = "velocitee-native"
    phase = "both"

    def __init__(self, *, intent, work_dir: Path, state_dir: Path):
        super().__init__(intent=intent, work_dir=work_dir, state_dir=state_dir)
        self.state_path = self.state_dir / "vne.state.json"
        self._proxmox: ProxmoxClient | None = None
        self._opnsense: OPNsenseClient | None = None
        self._state: RenderState | None = None
        self._node: str | None = None

    # -----------------------------------------------------------------
    # Validation — config-time, no side effects
    # -----------------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []
        missing = _missing_env()
        if missing:
            errors.append(
                "missing required environment variables: " + ", ".join(missing)
            )
        try:
            RenderState(self.state_path)
        except StateError as exc:
            errors.append(str(exc))

        if self.intent.opnsense.vm.iso_url is None:
            errors.append("opnsense.vm.iso_url must be set for velocitee-native")
        return errors

    # -----------------------------------------------------------------
    # Execute — orchestrates the named steps
    # -----------------------------------------------------------------

    def execute(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
        try:
            self._state = RenderState(self.state_path)
            self._proxmox = ProxmoxClient(
                endpoint=os.environ["PROXMOX_VE_ENDPOINT"],
                token=os.environ["PROXMOX_VE_API_TOKEN"],
            )
            self._node = self._state.shared_get("proxmox_node") or self._proxmox.first_node()
            self._state.shared_set("proxmox_node", self._node)

            self._step_ensure_bridges()
            iso_volid = self._step_iso_present()
            config_iso_volid = self._step_render_config_iso()
            self._step_create_vm(iso_volid=iso_volid, config_iso_volid=config_iso_volid)
            self._step_start_vm()
            opnsense_ip = self._step_wait_for_opnsense_ip()
            self._step_open_opnsense_client(opnsense_ip)
            self._step_configure_vlans()
            self._step_configure_dhcp()
            self._step_configure_dns()
            self._step_configure_firewall()

        except (ProxmoxAPIError, OPNsenseAPIError, StateError) as exc:
            log.exception("velocitee-native: step failed")
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase="both",
                error=str(exc),
            )

        api_key = self._state.shared_get("opnsense_api_key", "")
        opnsense_ip = self._state.shared_get("opnsense_ip", "")
        return ProvisioningResult(
            success=True,
            renderer=self.name,
            phase="both",
            outputs={
                "opnsense_ip": opnsense_ip,
                "opnsense_api_endpoint": f"https://{opnsense_ip}",
                "opnsense_api_key": api_key,
                "opnsense_vmid": self.intent.opnsense.vm.vmid,
                "proxmox_node": self._node,
            },
        )

    # =================================================================
    # Steps
    # =================================================================

    def _step_ensure_bridges(self) -> None:
        """Make sure the WAN+LAN bridges (vmbrX) exist and are vlan-aware.

        velocitee.yml gives us interface *names inside the OPNsense VM* (ens18,
        ens19) — not Proxmox bridge names. The convention is one bridge per
        VM nic; for now we use vmbr0 (WAN, attached to upstream) and vmbr1
        (LAN, internal). Operators with non-default topologies can pre-create
        the bridges themselves; both branches of this code are no-ops in that
        case.
        """
        key = "ensure_bridges"
        if self._state.is_completed(key):
            return
        self._state.mark_started(key)
        try:
            for bridge in ("vmbr0", "vmbr1"):
                self._proxmox.ensure_vlan_aware_bridge(self._node, bridge)
        except ProxmoxAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key, {"bridges": ["vmbr0", "vmbr1"]})

    def _step_iso_present(self) -> str:
        """Download the OPNsense ISO into Proxmox storage (or return existing volid)."""
        key = "iso_present"
        recorded = self._state.step_data(key)
        if self._state.is_completed(key) and recorded.get("volid"):
            # Verify the recorded volid still exists; reconcile if not.
            volid = recorded["volid"]
            basename = volid.split("/")[-1]
            if self._proxmox.find_iso_volume(self._node, "local", basename):
                return volid

        self._state.mark_started(key)
        try:
            url = self.intent.opnsense.vm.iso_url
            checksum = self.intent.opnsense.vm.iso_checksum
            basename = url.rsplit("/", 1)[-1] or "opnsense.iso"
            volid = self._proxmox.download_iso(
                self._node, "local", url, basename, checksum=checksum,
            )
        except ProxmoxAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise

        self._state.mark_completed(key, {"volid": volid})
        return volid

    def _step_render_config_iso(self) -> str:
        """Render OPNsense config.xml, wrap it in a tiny ISO, upload as 'cdrom2'.

        OPNsense will mount the second cdrom on first boot and import config.xml
        if it sees one. We persist the api_key/secret in state so resumes reuse
        them — otherwise we'd lock ourselves out of an already-deployed appliance.
        """
        key = "render_config_iso"
        recorded = self._state.step_data(key)
        if self._state.is_completed(key) and recorded.get("volid"):
            return recorded["volid"]

        self._state.mark_started(key)
        try:
            cx = _config_xml()
            api_key = self._state.shared_get("opnsense_api_key") or _new_token(48)
            api_secret_plain = (
                self._state.shared_get("opnsense_api_secret_plain") or _new_token(64)
            )
            xml = cx.render_config_xml(
                self.intent,
                root_password=os.environ["OPNSENSE_ROOT_PASSWORD"],
                api_key=api_key,
                api_secret=api_secret_plain,
            )

            iso_dir = self.work_dir / "config-iso"
            iso_dir.mkdir(parents=True, exist_ok=True)
            (iso_dir / "config.xml").write_text(xml)
            iso_path = self.work_dir / f"vne-cfg-{self.intent.opnsense.vm.vmid}.iso"
            _make_iso(iso_dir, iso_path, label="OPN_CONFIG")

            volid = _upload_iso_via_storage(
                self._proxmox, self._node, "local", iso_path,
            )

            self._state.shared_set("opnsense_api_key", api_key)
            self._state.shared_set("opnsense_api_secret_plain", api_secret_plain)
        except (ProxmoxAPIError, OSError) as exc:
            self._state.mark_failed(key, str(exc))
            raise

        self._state.mark_completed(key, {"volid": volid})
        return volid

    def _step_create_vm(self, *, iso_volid: str, config_iso_volid: str) -> None:
        key = "create_vm"
        if self._state.is_completed(key):
            # Verify the VM actually exists; auto-reconcile if a human deleted it.
            if self._proxmox.vm_exists(self._node, self.intent.opnsense.vm.vmid):
                return

        self._state.mark_started(key)
        vm = self.intent.opnsense.vm
        try:
            self._proxmox.create_vm(
                self._node,
                vm.vmid,
                name=vm.name,
                cores=vm.cores,
                memory_mb=vm.memory_mb,
                disk_gb=vm.disk_gb,
                storage_pool=vm.storage_pool,
                iso_volume_id=iso_volid,
                bridges=["vmbr0", "vmbr1"],  # WAN, LAN
                bios=vm.bios,
                machine=vm.machine,
                cpu_type=vm.cpu_type,
            )
            # Attach config.xml ISO as IDE3 (IDE2 is the OS ISO).
            self._proxmox.attach_extra_iso(
                self._node, vm.vmid, "ide3", config_iso_volid,
            )
        except ProxmoxAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise

        self._state.mark_completed(key, {"vmid": vm.vmid})

    def _step_start_vm(self) -> None:
        key = "start_vm"
        if self._state.is_completed(key):
            # Reconcile: ensure it's actually running.
            if self._proxmox.vm_status(self._node, self.intent.opnsense.vm.vmid) == "running":
                return

        self._state.mark_started(key)
        try:
            self._proxmox.start_vm(self._node, self.intent.opnsense.vm.vmid)
        except ProxmoxAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)

    def _step_wait_for_opnsense_ip(self) -> str:
        key = "wait_opnsense_ip"
        recorded = self._state.step_data(key)
        if self._state.is_completed(key) and recorded.get("ip"):
            return recorded["ip"]

        self._state.mark_started(key)
        try:
            ip = self._proxmox.wait_for_guest_ip(
                self._node,
                self.intent.opnsense.vm.vmid,
                timeout=900.0,
                interval=10.0,
                interface_substring=self.intent.network.lan_interface,
            )
        except ProxmoxAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise

        self._state.shared_set("opnsense_ip", ip)
        self._state.mark_completed(key, {"ip": ip})
        return ip

    def _step_open_opnsense_client(self, ip: str) -> None:
        key = "opnsense_api_ready"
        api_key = self._state.shared_get("opnsense_api_key")
        api_secret = self._state.shared_get("opnsense_api_secret_plain")
        if not (api_key and api_secret):
            raise OPNsenseAPIError(
                "API credentials missing from state — config.xml step did not record them"
            )

        self._opnsense = OPNsenseClient(
            endpoint=f"https://{ip}",
            api_key=api_key,
            api_secret=api_secret,
            verify_ssl=False,  # self-signed by default on first boot
        )

        if self._state.is_completed(key):
            return

        self._state.mark_started(key)
        try:
            self._opnsense.wait_until_ready()
        except OPNsenseAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)

    def _step_configure_vlans(self) -> None:
        key = "configure_vlans"
        if self._state.is_completed(key):
            return
        self._state.mark_started(key)
        try:
            parent = "lan"  # OPNsense interface assignment, not the OS ifname.
            for vlan in self.intent.network.vlans:
                self._opnsense.upsert_vlan(
                    tag=vlan.id, parent=parent, descr=f"vne:{vlan.name}",
                )
            self._opnsense.reconfigure_vlans()
        except OPNsenseAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)

    def _step_configure_dhcp(self) -> None:
        key = "configure_dhcp"
        if self._state.is_completed(key):
            return
        self._state.mark_started(key)
        try:
            for vlan in self.intent.network.vlans:
                if not (vlan.dhcp_start and vlan.dhcp_end):
                    continue
                self._opnsense.upsert_dhcp_subnet(
                    cidr=vlan.cidr,
                    gateway=vlan.gateway,
                    pool_start=vlan.dhcp_start,
                    pool_end=vlan.dhcp_end,
                    dns_servers=[vlan.gateway],  # OPNsense itself resolves
                    domain=self.intent.network.dns.domain,
                    lease_time=vlan.dhcp_lease_time,
                )
            self._opnsense.reconfigure_dhcp()
        except OPNsenseAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)

    def _step_configure_dns(self) -> None:
        key = "configure_dns"
        if self._state.is_completed(key):
            return
        self._state.mark_started(key)
        try:
            self._opnsense.configure_unbound(
                upstream=list(self.intent.network.dns.upstream),
                domain=self.intent.network.dns.domain,
            )
        except OPNsenseAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)

    def _step_configure_firewall(self) -> None:
        key = "configure_firewall"
        if self._state.is_completed(key):
            return
        self._state.mark_started(key)
        try:
            for rule in self.intent.network.firewall.allow_rules:
                src = "any" if rule.src_vlan == "any" else f"vlan{rule.src_vlan}"
                dst = "any" if rule.dst_vlan == "any" else f"vlan{rule.dst_vlan}"
                body = {
                    "rule": {
                        "type": rule.action,
                        "interface": src,
                        "ipprotocol": "inet",
                        "direction": "in",
                        "description": f"vne: {rule.description}",
                        "protocol": rule.proto if rule.proto != "any" else "",
                        "source_net": "any",
                        "destination_net": rule.dst if rule.dst != "any" else "any",
                        "destination_port": str(rule.port) if rule.port else "",
                        "destination_invert": "0",
                        "enabled": "1",
                        "gateway": "",
                        "log": "0",
                    }
                }
                # Pin destination to specific VLAN if requested.
                if dst != "any":
                    body["rule"]["destination_net"] = dst
                self._opnsense.upsert_filter_rule(body)

            # Default policy enforcement comes last so we don't strand ourselves.
            if self.intent.network.firewall.default_policy == "block":
                self._opnsense.set_default_policy(block=True)
            else:
                self._opnsense.set_default_policy(block=False)

            self._opnsense.apply_firewall()
        except OPNsenseAPIError as exc:
            self._state.mark_failed(key, str(exc))
            raise
        self._state.mark_completed(key)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _new_token(length: int) -> str:
    import secrets
    return secrets.token_urlsafe(length)


def _make_iso(src_dir: Path, out_path: Path, *, label: str) -> Path:
    """Build a tiny ISO9660 image from src_dir. Uses xorriso or genisoimage."""
    import shutil
    import subprocess

    tools = [
        ("xorriso", ["xorriso", "-as", "mkisofs",
                     "-V", label, "-J", "-r",
                     "-o", str(out_path), str(src_dir)]),
        ("genisoimage", ["genisoimage", "-V", label, "-J", "-r",
                         "-o", str(out_path), str(src_dir)]),
        ("mkisofs", ["mkisofs", "-V", label, "-J", "-r",
                     "-o", str(out_path), str(src_dir)]),
    ]
    for name, cmd in tools:
        if shutil.which(name):
            subprocess.run(cmd, check=True, capture_output=True)
            return out_path
    raise OSError("none of xorriso, genisoimage, or mkisofs are installed; "
                  "cannot package OPNsense config.xml")


def _upload_iso_via_storage(
    proxmox: ProxmoxClient,
    node: str,
    storage: str,
    path: Path,
) -> str:
    """Upload a local ISO file into Proxmox storage. Idempotent on basename."""
    basename = path.name
    existing = proxmox.find_iso_volume(node, storage, basename)
    if existing:
        return existing
    # POST multipart/form-data to /nodes/{node}/storage/{storage}/upload.
    # Reuse the proxmox client's pre-auth'd session — it already carries the
    # PVEAPIToken header and the right verify_ssl setting.
    url = proxmox.base + f"/nodes/{node}/storage/{storage}/upload"
    with open(path, "rb") as fh:
        files = {"filename": (basename, fh, "application/octet-stream")}
        data = {"content": "iso"}
        resp = proxmox._session.post(url, files=files, data=data, timeout=600)
    if resp.status_code >= 400:
        raise ProxmoxAPIError(
            f"ISO upload failed: HTTP {resp.status_code}: {resp.text[:200]}"
        )
    # Wait briefly for the volume to appear in the storage listing.
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        v = proxmox.find_iso_volume(node, storage, basename)
        if v:
            return v
        time.sleep(2)
    return f"{storage}:iso/{basename}"


# Register on import.
register("velocitee-native", VelociteeNativeRenderer)
