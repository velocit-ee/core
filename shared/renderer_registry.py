"""Provisioner-name → Renderer-class registry.

The user picks a provisioner in velocitee.yml:

    velocitee:
      provisioner: "velocitee-native"

VNE looks up that string here and instantiates the matched class. Adding a
new provisioner is exactly two steps: write a Renderer subclass, register it.

Every backend mentioned in the VNE build prompt has an entry below — fully
implemented backends and stubs alike. A 'stub' raises NotImplementedError on
execute() but still validates() so config-time checks pass cleanly.

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
    """Eagerly import every renderer module so the registry is populated.

    Done lazily on first lookup so that simply importing the registry doesn't
    drag in OpenTofu/Ansible imports unless a user actually selects them.
    """
    # Import inside the function to keep top-level import light.
    # Each renderer module calls register(...) at import time.
    from .renderers import (  # noqa: F401  (side-effect: registers backends)
        velocitee_native,
        opentofu,
        ansible,
        ansible_only,
        pulumi,
        salt,
        chef,
        puppet,
        cloudformation,
        bicep,
        nix,
        cloud_init,
        helm,
        packer,
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
        # Composite may need late binding even if individual backends loaded.
        _autoregister()
