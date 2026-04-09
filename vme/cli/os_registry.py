"""OS registry — single source of truth for supported operating systems.

To add a new OS, add an entry here. Everything else (boot menu, image
management, config validation) derives from this registry at runtime.
"""

from __future__ import annotations

from pathlib import Path


OS_REGISTRY: dict[str, dict] = {
    "proxmox-ve": {
        "label": "Proxmox VE (latest stable)",
        "menu_key": "proxmox",       # short token used in iPXE :labels and choose
        "iso_pattern": "proxmox-ve_*.iso",
        "boot_method": "sanboot",    # sanboot = HTTP ISO block device; no memdisk
    },
    "ubuntu-server": {
        "label":       "Ubuntu Server LTS (latest)",
        "menu_key":    "ubuntu",
        "iso_pattern": "ubuntu-*-live-server-amd64.iso",
        "boot_method": "kernel",
        "kernel_path": "casper/vmlinuz",
        "initrd_path": "casper/initrd",
    },
}


def find_cached(slug: str, cache_dir: Path) -> Path | None:
    """Return the newest cached ISO for *slug*, or None if not cached."""
    meta = OS_REGISTRY.get(slug)
    if not meta:
        return None
    matches = sorted(cache_dir.glob(meta["iso_pattern"]))
    return matches[-1] if matches else None


def cached_entries(cache_dir: Path) -> list[tuple[str, str, str, Path]]:
    """Scan *cache_dir* and return one entry per cached OS.

    Returns list of (slug, menu_key, label, iso_path) sorted by registry order.
    """
    result = []
    if not cache_dir.exists():
        return result
    for slug, meta in OS_REGISTRY.items():
        iso = find_cached(slug, cache_dir)
        if iso:
            result.append((slug, meta["menu_key"], meta["label"], iso))
    return result
