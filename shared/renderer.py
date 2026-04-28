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

## Plugin descriptor (`config_keys`)

Each renderer self-declares its config keys via `config_keys`. This replaces
the old approach of duplicating each backend's required-env-var inventory
inside `vne/deploy.py`. The descriptor is the source of truth for:

  - which env vars deploy.py's pre-flight check looks for,
  - what the (eventual) SaaS UI renders as a config form,
  - which fields the OSS docs document for that backend.

Inspired by ARCTIC's `shard.yml` plugin descriptors, but inlined into Python
since velocitee renderers are first-class Python classes (no separate
plugin loader).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .schema import ProvisioningResult, VNEIntent

Phase = Literal["infra", "config", "both"]
ConfigType = Literal["string", "password", "url", "bool", "int"]


@dataclass(frozen=True)
class ConfigKey:
    """One config key the renderer needs. Descriptor for env-var inventory.

    `env` is the environment variable name the renderer reads at runtime.
    `type` and `required` drive UI rendering and pre-flight validation.
    `description` is shown to the user in error messages and docs.
    """
    env: str
    description: str
    type: ConfigType = "string"
    required: bool = True
    default: str | None = None


class Renderer(ABC):
    """Abstract base class. Every provisioner backend is a Renderer subclass."""

    name: str = ""  # e.g. "velocitee-native", "opentofu", "ansible"
    phase: Phase = "both"

    # Subclasses override with their backend-specific keys. Empty list = no
    # config required (e.g. stub renderers). Treated as immutable per class —
    # never mutate at the class level, replace the whole list in subclasses.
    config_keys: list[ConfigKey] = []

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

    # -----------------------------------------------------------------
    # Class-level helpers (no instance needed)
    # -----------------------------------------------------------------

    @classmethod
    def required_env(cls) -> list[str]:
        """Names of env vars the user must set for this renderer.

        deploy.py's pre-flight uses this to build a per-backend missing-vars
        list before any renderer is instantiated.
        """
        return [k.env for k in cls.config_keys if k.required]

    @classmethod
    def optional_env(cls) -> list[str]:
        return [k.env for k in cls.config_keys if not k.required]

    @classmethod
    def missing_env(cls, env: dict[str, str]) -> list[str]:
        """Return names of required env vars not present in `env`."""
        return [name for name in cls.required_env() if not env.get(name)]
