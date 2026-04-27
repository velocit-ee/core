"""Renderer abstract base class.

A Renderer is the bridge between the provisioner-agnostic VNEIntent model and
a concrete provisioner. Subclasses commit to one provisioner backend. The
pipeline orchestrator drives one or more renderers per deployment.

Two execution phases exist:

  - 'infra'   — creates compute (VMs, networks, hardware)
  - 'config'  — configures running systems (API calls into appliances)

Some renderers (velocitee-native) cover both phases in one render() call. Others
(opentofu + ansible) split: OpenTofu does 'infra', Ansible does 'config'. The
pipeline runs all 'infra' renderers in order, then all 'config' renderers in
order. A 'config' renderer never runs if any 'infra' renderer in the same
pipeline failed — that gate is enforced by the pipeline, not the renderer.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Literal

from .schema import ProvisioningResult, VNEIntent

Phase = Literal["infra", "config", "both"]


class Renderer(ABC):
    """Abstract base class. Every provisioner backend is a Renderer subclass."""

    name: str = ""  # e.g. "velocitee-native", "opentofu", "ansible"
    phase: Phase = "both"

    def __init__(self, *, intent: VNEIntent, work_dir: Path, state_dir: Path):
        if not self.name:
            raise TypeError(
                f"{type(self).__name__}: subclass must set the 'name' class attribute"
            )
        self.intent = intent
        self.work_dir = Path(work_dir)
        self.state_dir = Path(state_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------
    # Required hooks
    # -----------------------------------------------------------------

    @abstractmethod
    def validate(self) -> list[str]:
        """Return a list of human-readable validation errors. Empty = ok.

        Called before execute(). Renderers check provisioner-specific
        prerequisites here: required env vars, reachable APIs, installed
        binaries, etc. Pure check — no side effects, no execution.
        """

    @abstractmethod
    def execute(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
        """Run the renderer. Must be idempotent — safe to call repeatedly.

        prior_outputs: outputs from earlier-phase renderers in the same pipeline
        (e.g. an Ansible renderer reads the OpenTofu renderer's outputs here).
        """

    # -----------------------------------------------------------------
    # Convenience
    # -----------------------------------------------------------------

    def get_execution_phase(self) -> Phase:
        return self.phase

    def render(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
        """Validate then execute. The standard entry point used by the pipeline."""
        errors = self.validate()
        if errors:
            return ProvisioningResult(
                success=False,
                renderer=self.name,
                phase=self.phase if self.phase != "both" else "infra",
                error="validation failed:\n  " + "\n  ".join(errors),
            )
        return self.execute(prior_outputs=prior_outputs or {})
