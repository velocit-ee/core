"""RouterAdapter abstract base + registry.

Each adapter answers the same two questions:

  capabilities()  — given a DiscoveryReport, what can this adapter offer on
                    the joined network? Pure inspection, no side effects.

  execute()       — perform the join: pull richer state from the router (if
                    we have credentials), normalize into a JoinManifestFragment,
                    write per-adapter state to disk for resume.

The pattern mirrors `shared.renderer.Renderer` so future contributors find
it familiar.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..report import Capability, DiscoveryReport


@dataclass
class AdapterResult:
    """Output of RouterAdapter.execute().

    `manifest_fragment` is merged into the VNE output manifest under
    engines.vne.joined_network. Adapters never write top-level fields —
    deploy.py owns the manifest envelope.
    """
    success: bool
    adapter: str
    capabilities: list[Capability] = field(default_factory=list)
    manifest_fragment: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    error: str = ""


class RouterAdapter(ABC):
    """Abstract base. Subclasses commit to one router/firewall vendor."""

    slug: str = ""
    description: str = ""

    def __init__(self, *, report: DiscoveryReport, work_dir: Path, state_dir: Path):
        if not self.slug:
            raise TypeError(
                f"{type(self).__name__}: subclass must set the 'slug' class attribute"
            )
        self.report = report
        self.work_dir = Path(work_dir)
        self.state_dir = Path(state_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------
    # Required hooks
    # -------------------------------------------------------------------

    @abstractmethod
    def capabilities(self) -> list[Capability]:
        """Pure inspection: what features will the joined network support?"""

    @abstractmethod
    def execute(self) -> AdapterResult:
        """Run the join. Idempotent."""

    # -------------------------------------------------------------------
    # Convenience
    # -------------------------------------------------------------------

    @classmethod
    def matches(cls, report: DiscoveryReport) -> bool:
        """Heuristic: does this adapter look like the right pick for the report?

        Default 'matches' compares the slug against the discovered router's
        api_kind. Subclasses can override for more nuanced detection
        (e.g. matching multiple slugs, accepting any gateway).
        """
        return report.router.api_kind == cls.slug


# ---------------------------------------------------------------------------
# Registry — slug → class
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[RouterAdapter]] = {}


def register(slug: str, cls: type[RouterAdapter]) -> None:
    if slug in _REGISTRY:
        raise ValueError(f"router adapter '{slug}' is already registered")
    _REGISTRY[slug] = cls


def lookup(slug: str) -> type[RouterAdapter]:
    if slug not in _REGISTRY:
        raise KeyError(
            f"unknown router adapter '{slug}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none)'}"
        )
    return _REGISTRY[slug]


def available() -> list[str]:
    return sorted(_REGISTRY)


def autopick(report: DiscoveryReport, *, default: str = "unmanaged") -> str:
    """Choose the best-fit adapter slug for a discovery report."""
    for slug, cls in _REGISTRY.items():
        if slug == "unmanaged":
            continue
        try:
            if cls.matches(report):
                return slug
        except Exception:  # noqa: BLE001 — adapter matching must never crash join
            continue
    return default
