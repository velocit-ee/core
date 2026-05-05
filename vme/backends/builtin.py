"""Built-in VME backend — the dnsmasq + iPXE + nginx seed stack.

This module is deliberately thin: the actual provisioning flow lives in
`vme.cli.vme.deploy()` for now. We expose it as a Backend so the registry
machinery is consistent across builtin and MAAS, but we *don't* refactor
the existing flow into this class — that would be a churn-heavy change
with no operator-visible benefit.

When the CLI sees `backend: builtin` (the default), it stays on the
existing inline path. The registry only routes when the operator picks
something other than `builtin` (e.g. MAAS).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .base import Backend, BackendResult


class BuiltinBackend(Backend):
    slug = "builtin"
    description = (
        "Runs a docker-compose seed stack (dnsmasq + iPXE + nginx) on the local "
        "machine to PXE-boot and unattended-install the target. Default backend "
        "— no external services required."
    )

    def deploy(self, cfg: dict[str, Any], *, verbose: bool = False) -> BackendResult:
        # Intentionally not implemented here — the CLI keeps the existing
        # inline flow for backend='builtin'. If something other than the
        # CLI ever calls into BuiltinBackend.deploy(), surface a clear
        # message rather than silently returning success.
        raise NotImplementedError(
            "BuiltinBackend.deploy is a registry placeholder; the actual "
            "deploy flow lives in vme.cli.vme.deploy() for backend='builtin'. "
            "Use that path."
        )

    @staticmethod
    def synthesize_result(
        *,
        cfg: dict[str, Any],
        iso_name: str,
        target_mac: str,
        started_at: datetime,
        completed_at: datetime,
    ) -> BackendResult:
        """Helper used by vme.cli.vme.deploy when it finishes the inline flow,
        so callers downstream see a consistent BackendResult shape regardless
        of which backend ran."""
        target = cfg.get("target", {})
        return BackendResult(
            success=True,
            backend="builtin",
            started_at=started_at,
            completed_at=completed_at,
            target_ip=target.get("ip", ""),
            target_hostname=target.get("hostname", ""),
            target_mac=target_mac,
            target_os=target.get("os", ""),
            iso_name=iso_name,
        )


def now() -> datetime:
    return datetime.now(timezone.utc)
