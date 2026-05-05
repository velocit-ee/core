"""MAAS backend — drive an existing Canonical MAAS deployment.

We don't ship MAAS, we don't run MAAS, we don't *replace* MAAS. This backend
is for operators who already operate a MAAS region+rack controller and want
to plug it into the velocit.ee pipeline as the metal-provisioning layer.

velocit.ee itself remains lighter: the default `builtin` backend still
runs on a Pi or a laptop. MAAS is a choice for users who specifically
want it.

## Authentication

MAAS uses OAuth 1.0 with three tokens encoded as a colon-separated string:

    consumer_key:token_key:token_secret

Generated via the MAAS UI ("API keys") or `maas apikey --username=<u>`.
We accept this string from one of:

  - env var `MAAS_API_KEY`
  - `vme-config.yml` field `maas.api_key` (discouraged — keys in config
    files are easy to leak; use env vars)

## What this backend does

For v1 we support the *deploy a known machine* path: the target machine
must already be enlisted, commissioned, and Ready in MAAS. VME issues a
deploy on it, polls until status=Deployed, then writes the same
`vme-manifest.json` shape that the builtin backend produces. Auto-enlist
and per-target commissioning are deliberately out of scope for v1 —
those flows are MAAS's domain and adding them here would duplicate UI
surface MAAS already does well.

## License + dependencies

MAAS itself is Apache 2.0 (no license trap). We do NOT import the
official `python-libmaas` library because it ships under LGPL v3 and the
license-clean route is the same one we took with discovery: subprocess +
HTTP. We talk to the MAAS REST API with `requests` — already a
transitive dep across the engines — and parse the JSON ourselves.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

import requests

from shared._retry import TransientAPIError, transient_retry
from shared.cli import fatal

from .base import Backend, BackendResult


class MAASAPIError(RuntimeError):
    pass


class MAASTransientError(MAASAPIError, TransientAPIError):
    """5xx or connection-level error worth retrying. See shared._retry."""


# ---------------------------------------------------------------------------
# OAuth 1.0 (PLAINTEXT) signer — what the MAAS API expects
# ---------------------------------------------------------------------------

class _MAASOAuth(requests.auth.AuthBase):
    """OAuth 1.0 with the PLAINTEXT signature method.

    MAAS supports HMAC-SHA1 too but PLAINTEXT is the default the project
    documents and is what `maas apikey` produces tokens for. PLAINTEXT
    means the secret is sent in cleartext under TLS — equivalent in
    practice to bearer-style auth.
    """

    def __init__(self, consumer_key: str, token_key: str, token_secret: str):
        self.consumer_key = consumer_key
        self.token_key = token_key
        self.token_secret = token_secret

    def __call__(self, request):
        # PLAINTEXT signature is just &<consumer_secret>&<token_secret>;
        # MAAS's consumer secret is empty, so the signature is &&<token_secret>.
        nonce = os.urandom(8).hex()
        ts = str(int(time.time()))
        header = (
            'OAuth '
            f'oauth_version="1.0", '
            f'oauth_signature_method="PLAINTEXT", '
            f'oauth_consumer_key="{self.consumer_key}", '
            f'oauth_token="{self.token_key}", '
            f'oauth_signature="&{self.token_secret}", '
            f'oauth_nonce="{nonce}", '
            f'oauth_timestamp="{ts}"'
        )
        request.headers["Authorization"] = header
        return request


# ---------------------------------------------------------------------------
# MAAS REST client
# ---------------------------------------------------------------------------

class MAASClient:
    """Minimal MAAS API client. Just the endpoints VME needs."""

    def __init__(self, base_url: str, api_key: str, *, verify_ssl: bool = True, timeout: float = 30.0):
        if "://" not in base_url:
            base_url = f"https://{base_url}"
        # Canonical form: <scheme>://<host>/MAAS/api/2.0
        if not base_url.rstrip("/").endswith("/MAAS/api/2.0"):
            base_url = base_url.rstrip("/") + "/MAAS/api/2.0"
        self.base = base_url
        try:
            consumer, token, secret = api_key.split(":")
        except ValueError as exc:
            raise MAASAPIError(
                "MAAS_API_KEY must be 'consumer_key:token_key:token_secret' — "
                "generate with `maas apikey --username=<user>`"
            ) from exc

        self._session = requests.Session()
        self._session.auth = _MAASOAuth(consumer, token, secret)
        self._session.verify = verify_ssl
        self._timeout = timeout

    @transient_retry()
    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = self.base + path
        kwargs.setdefault("timeout", self._timeout)
        try:
            resp = self._session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise MAASTransientError(f"{method} {path} network error: {exc}") from exc

        if 500 <= resp.status_code < 600:
            raise MAASTransientError(
                f"{method} {path} → HTTP {resp.status_code}: {resp.text.strip()[:200]}"
            )
        if resp.status_code >= 400:
            raise MAASAPIError(
                f"{method} {path} → HTTP {resp.status_code}: {resp.text.strip()[:500]}"
            )
        if not resp.text:
            return None
        try:
            return resp.json()
        except ValueError as exc:
            raise MAASAPIError(f"{method} {path} returned non-JSON: {resp.text[:200]}") from exc

    # -------------------------------------------------------------------
    # Machines
    # -------------------------------------------------------------------

    def list_machines(self) -> list[dict[str, Any]]:
        return self._request("GET", "/machines/") or []

    def find_machine(self, *, system_id: str = "", hostname: str = "", mac: str = "") -> dict[str, Any] | None:
        """Locate a machine by system_id, hostname, or MAC. First match wins."""
        if system_id:
            return self._request("GET", f"/machines/{system_id}/")
        for m in self.list_machines():
            if hostname and m.get("hostname", "").lower() == hostname.lower():
                return m
            if mac:
                for iface in m.get("interface_set") or []:
                    if (iface.get("mac_address") or "").lower() == mac.lower():
                        return m
        return None

    def deploy(
        self,
        system_id: str,
        *,
        distro_series: str = "",
        user_data_b64: str = "",
    ) -> dict[str, Any]:
        """Issue a deploy. Returns the updated machine JSON."""
        data: dict[str, Any] = {}
        if distro_series:
            data["distro_series"] = distro_series
        if user_data_b64:
            data["user_data"] = user_data_b64
        return self._request("POST", f"/machines/{system_id}/?op=deploy", data=data)

    def get_machine(self, system_id: str) -> dict[str, Any]:
        return self._request("GET", f"/machines/{system_id}/")


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------

# MAAS uses these status codes — see src/maasserver/enum.py upstream.
_MAAS_STATUS_DEPLOYED = 6
_MAAS_STATUS_DEPLOYING = 9
_MAAS_STATUS_FAILED_DEPLOYMENT = 11
_MAAS_STATUS_BROKEN = 8
_MAAS_STATUS_READY = 4


class MAASBackend(Backend):
    slug = "maas"
    description = (
        "Hand off provisioning to an existing Canonical MAAS deployment. "
        "Requires MAAS_URL and MAAS_API_KEY in the environment."
    )

    def deploy(self, cfg: dict[str, Any], *, verbose: bool = False) -> BackendResult:
        started_at = datetime.now(timezone.utc)

        url = os.environ.get("MAAS_URL") or _cfg_get(cfg, "maas.url")
        api_key = os.environ.get("MAAS_API_KEY") or _cfg_get(cfg, "maas.api_key")
        verify_ssl = os.environ.get("MAAS_INSECURE", "0") != "1"
        if not url or not api_key:
            fatal(
                "MAAS backend requires MAAS_URL and MAAS_API_KEY.",
                hint="generate the key with `maas apikey --username=<u>` and export both before `vme deploy`.",
            )

        target = cfg.get("target", {}) or {}
        hostname = target.get("hostname", "")
        mac = (target.get("mac") or "").lower()
        system_id = _cfg_get(cfg, "maas.system_id") or ""

        os_slug = target.get("os", "")
        distro_series = _maas_distro_series(os_slug)

        try:
            client = MAASClient(url, api_key, verify_ssl=verify_ssl)
        except MAASAPIError as exc:
            return _failed("maas", started_at, str(exc))

        try:
            machine = client.find_machine(
                system_id=system_id, hostname=hostname, mac=mac,
            )
        except MAASAPIError as exc:
            return _failed("maas", started_at, f"machine lookup failed: {exc}")

        if not machine:
            return _failed(
                "maas", started_at,
                f"no MAAS machine found matching system_id='{system_id}', "
                f"hostname='{hostname}', mac='{mac}' — enlist + commission it in MAAS first.",
            )

        sid = machine["system_id"]
        status = int(machine.get("status", -1))
        if status not in (_MAAS_STATUS_READY, _MAAS_STATUS_DEPLOYED):
            return _failed(
                "maas", started_at,
                f"machine {sid} is in MAAS status {status} ({machine.get('status_name')}); "
                f"need 'Ready' or 'Deployed' before VME can drive it.",
            )

        if status != _MAAS_STATUS_DEPLOYED:
            try:
                client.deploy(sid, distro_series=distro_series)
            except MAASAPIError as exc:
                return _failed("maas", started_at, f"deploy call failed: {exc}")

        # Poll until deployed/failed.
        deadline = time.monotonic() + 60 * 60  # 1h ceiling — MAAS deploys vary wildly
        last_status = -1
        while time.monotonic() < deadline:
            try:
                machine = client.get_machine(sid)
            except MAASAPIError as exc:
                return _failed("maas", started_at, f"poll failed: {exc}")
            status = int(machine.get("status", -1))
            if status != last_status:
                last_status = status
                if verbose:
                    print(f"  [MAAS] {sid} status={status} ({machine.get('status_name')})")
            if status == _MAAS_STATUS_DEPLOYED:
                break
            if status in (_MAAS_STATUS_FAILED_DEPLOYMENT, _MAAS_STATUS_BROKEN):
                return _failed(
                    "maas", started_at,
                    f"machine {sid} entered terminal status {status} "
                    f"({machine.get('status_name')}) — check MAAS UI for details.",
                )
            time.sleep(15)
        else:
            return _failed("maas", started_at, "deploy poll timed out after 1h")

        completed_at = datetime.now(timezone.utc)

        # Pick a usable IP from the machine's interfaces.
        ip = ""
        for iface in machine.get("interface_set") or []:
            for link in iface.get("links") or []:
                if link.get("ip_address"):
                    ip = link["ip_address"]
                    break
            if ip:
                break

        # Pick a usable MAC.
        mac_out = mac
        if not mac_out:
            for iface in machine.get("interface_set") or []:
                if iface.get("mac_address"):
                    mac_out = iface["mac_address"].lower()
                    break

        return BackendResult(
            success=True,
            backend="maas",
            started_at=started_at,
            completed_at=completed_at,
            target_ip=ip,
            target_hostname=machine.get("hostname", "") or hostname,
            target_mac=mac_out,
            target_os=os_slug or distro_series,
            iso_name=f"maas:{distro_series or 'default'}",
            extra={
                "maas_system_id": sid,
                "maas_url": url,
                "maas_status_name": machine.get("status_name", ""),
                "maas_distro_series": distro_series,
            },
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _maas_distro_series(os_slug: str) -> str:
    """Translate VME's OS_REGISTRY slugs into MAAS distro_series codenames.

    MAAS does not natively ship Proxmox VE; users with Proxmox in their
    MAAS catalog supply a custom image and reference it via
    `maas.distro_series` in vme-config.yml. For the common Ubuntu LTS
    paths we map slugs to the obvious series codename so the config
    doesn't need to know MAAS-specific naming.
    """
    if not os_slug:
        return ""
    mapping = {
        "ubuntu-22.04": "jammy",
        "ubuntu-24.04": "noble",
        "ubuntu-server-22.04": "jammy",
        "ubuntu-server-24.04": "noble",
    }
    return mapping.get(os_slug, os_slug)


def _cfg_get(cfg: dict[str, Any], path: str, default: Any = None) -> Any:
    """Read 'a.b.c' out of a nested config dict. Missing → default."""
    cur: Any = cfg
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


def _failed(backend: str, started_at: datetime, msg: str) -> BackendResult:
    return BackendResult(
        success=False,
        backend=backend,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc),
        error=msg,
    )
