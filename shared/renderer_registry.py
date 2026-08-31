"""Provisioner-name -> Renderer-class registry.

The user picks a provisioner in velocitee.yml:

    velocitee:
      provisioner: "velocitee-native"

VNE looks up that string here and instantiates the matched class. Adding a
new provisioner is two steps: write a Renderer subclass, register it.

Some entries are tuples — provisioners like 'opentofu+ansible' that require
multiple Renderers run in sequence. The pipeline handles ordering.
"""

from __future__ import annotations

from typing import Sequence

from .renderer import Renderer

_REGISTRY: dict[str, Sequence[type[Renderer]]] = {}


def register(name: str, *renderers: type[Renderer]) -> None:
    if not renderers:
        raise ValueError(f"register('{name}', ...) needs at least one renderer class")
    if name in _REGISTRY:
        raise ValueError(f"provisioner '{name}' is already registered")
    _REGISTRY[name] = renderers


def lookup(name: str) -> Sequence[type[Renderer]]:
    if name not in _REGISTRY:
        raise KeyError(
            f"unknown provisioner '{name}'. "
            f"Available: {', '.join(sorted(_REGISTRY)) or '(none registered)'}"
        )
    return _REGISTRY[name]


def is_registered(name: str) -> bool:
    return name in _REGISTRY


def available() -> list[str]:
    return sorted(_REGISTRY)


def _autoregister() -> None:
    """Eagerly import supported renderer modules so the registry is populated."""
    from .renderers import (  # noqa: F401
        velocitee_native,
        opentofu,
        ansible,
    )

    # opentofu + ansible — composite backend run in sequence.
    if "opentofu+ansible" not in _REGISTRY:
        from .renderers.opentofu import OpenTofuRenderer
        from .renderers.ansible import AnsibleRenderer
        register("opentofu+ansible", OpenTofuRenderer, AnsibleRenderer)


def ensure_loaded() -> None:
    """Public entry point — VNE calls this once at startup."""
    if not _REGISTRY:
        _autoregister()
    elif "opentofu+ansible" not in _REGISTRY:
        _autoregister()
