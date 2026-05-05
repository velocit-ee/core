"""VME backend registry — multiple ways to PXE-boot and install a target.

VME has always been able to do bare-metal provisioning end-to-end with its
own dnsmasq + iPXE + nginx seed stack (the `builtin` backend). For users who
already operate **Canonical MAAS** — a heavier, datacentre-class metal
provisioner — VME can hand off to MAAS instead and still produce the same
handoff manifest the rest of the pipeline (VNE → VSE → VLE) expects.

The two backends solve the same problem at different ends of the spectrum:

  - `builtin` — runs on a laptop or a Pi. No external services needed. The
    default. velocit.ee's pitch is built around this: lightweight,
    low-resource, scales down to a school IT closet.
  - `maas`    — drives an existing MAAS region+rack controller. Useful when
    the operator already runs MAAS for inventory / IPMI / commissioning and
    just wants VME's downstream engines (VNE/VSE/VLE) to plug into that
    workflow. velocit.ee does not ship MAAS; you point VME at yours.

Both backends produce the same `vme-manifest.json` contract. Downstream
engines can't tell which produced it.

Adding a new backend = subclass `Backend`, register the slug. No core
changes. The pattern intentionally mirrors `shared.renderer.Renderer`.
"""

from __future__ import annotations

from .base import Backend, BackendResult
from .builtin import BuiltinBackend
from .maas import MAASBackend

_REGISTRY: dict[str, type[Backend]] = {}


def register(slug: str, cls: type[Backend]) -> None:
    if slug in _REGISTRY:
        raise ValueError(f"VME backend '{slug}' is already registered")
    _REGISTRY[slug] = cls


def lookup(slug: str) -> type[Backend]:
    if slug not in _REGISTRY:
        raise KeyError(
            f"unknown VME backend '{slug}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none)'}"
        )
    return _REGISTRY[slug]


def available() -> list[str]:
    return sorted(_REGISTRY)


register("builtin", BuiltinBackend)
register("maas", MAASBackend)


__all__ = [
    "Backend",
    "BackendResult",
    "BuiltinBackend",
    "MAASBackend",
    "available",
    "lookup",
    "register",
]
