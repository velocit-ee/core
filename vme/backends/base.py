"""Backend ABC.

The contract is intentionally narrow: a backend takes a parsed
`vme-config.yml` and returns a `BackendResult` describing what was
provisioned. The CLI (`vme.cli.vme.deploy`) is the only thing that
instantiates a backend — engines never call backends directly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class BackendResult:
    """What a backend reports after a successful provisioning run.

    Carries everything the manifest builder needs. Backends do not write
    the manifest themselves — that's the CLI's job, so the manifest
    schema lives in exactly one place.
    """
    success: bool
    backend: str
    started_at: datetime
    completed_at: datetime
    target_ip: str = ""
    target_hostname: str = ""
    target_mac: str = ""
    target_os: str = ""
    iso_name: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class Backend(ABC):
    """One provisioning backend.

    Subclasses don't need state of their own — the config dict carries
    everything they need. Backends should be re-runnable; if a previous
    run partially completed, a re-run should pick up where it left off
    (the seed stack is naturally idempotent; MAAS already deals with
    re-deploys at its API level).
    """

    slug: str = ""
    description: str = ""

    @abstractmethod
    def deploy(
        self,
        cfg: dict[str, Any],
        *,
        verbose: bool = False,
    ) -> BackendResult:
        """Provision the target described in `cfg`. Return a BackendResult.

        Implementations must:
          - Validate any backend-specific config / env vars before doing
            real work, and raise a typer.BadParameter (or call shared.cli.fatal)
            with a clear message on failure.
          - Be safe to interrupt: Ctrl-C should leave nothing dangling that
            requires manual cleanup.
        """
