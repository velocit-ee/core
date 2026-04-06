"""OS image lifecycle management — fetch, verify, cache, and serve."""

from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

_DEFAULT_CACHE = Path("~/.velocitee/cache/images/").expanduser()

# Public release index URLs — no version numbers hardcoded here.
_PROXMOX_INDEX_URL = "http://download.proxmox.com/iso/"
_UBUNTU_RELEASES_API = "https://api.launchpad.net/1.0/ubuntu/series"
_UBUNTU_RELEASES_BASE = "https://releases.ubuntu.com/"


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


def _resolve_proxmox_latest() -> tuple[str, str, str]:
    """Parse the Proxmox ISO directory listing and return (version, filename, sha256_url).

    Raises RuntimeError if the listing cannot be parsed or no ISO is found.
    """
    resp = requests.get(_PROXMOX_INDEX_URL, timeout=30)
    resp.raise_for_status()

    # Matches: proxmox-ve_8.2-1.iso  (major.minor-patch)
    iso_pattern = re.compile(r'href="(proxmox-ve_([\d]+\.[\d]+-[\d]+)\.iso)"')
    matches = iso_pattern.findall(resp.text)
    if not matches:
        raise RuntimeError(
            f"Could not find any Proxmox VE ISO links at {_PROXMOX_INDEX_URL}. "
            "The directory listing format may have changed."
        )

    def _version_key(m: tuple[str, str]) -> tuple[int, ...]:
        # m[1] is the version string like "8.2-1"
        parts = re.split(r"[.\-]", m[1])
        return tuple(int(p) for p in parts)

    matches.sort(key=_version_key, reverse=True)
    filename, version = matches[0]
    iso_url = urljoin(_PROXMOX_INDEX_URL, filename)

    # Proxmox publishes SHA256SUMS in the same directory.
    sha256_url = urljoin(_PROXMOX_INDEX_URL, "SHA256SUMS")
    return version, iso_url, sha256_url


def _resolve_ubuntu_latest_lts() -> tuple[str, str, str]:
    """Query the Ubuntu release API for the latest LTS, then resolve the ISO URL.

    Returns (version, iso_url, sha256_url).
    Raises RuntimeError if no LTS release can be found.
    """
    resp = requests.get(_UBUNTU_RELEASES_API, timeout=30, params={"ws.size": 75})
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()

    lts_entries = [
        e for e in data.get("entries", [])
        if e.get("supported") and e.get("lts")
    ]
    if not lts_entries:
        raise RuntimeError(
            "No supported LTS Ubuntu series found via the Launchpad API. "
            "Check your internet connection or try again later."
        )

    # Sort by version descending (e.g. "24.04" > "22.04")
    def _lts_key(e: dict[str, Any]) -> tuple[int, int]:
        parts = str(e.get("version", "0.0")).split(".")
        try:
            return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except ValueError:
            return 0, 0

    lts_entries.sort(key=_lts_key, reverse=True)
    latest = lts_entries[0]
    version = latest["version"]  # e.g. "24.04"

    # Construct the ISO URL from releases.ubuntu.com.
    iso_filename = f"ubuntu-{version}-live-server-amd64.iso"
    iso_url = f"{_UBUNTU_RELEASES_BASE}{version}/{iso_filename}"
    sha256_url = f"{_UBUNTU_RELEASES_BASE}{version}/SHA256SUMS"

    return version, iso_url, sha256_url


# ---------------------------------------------------------------------------
# Download + verify
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    """Compute hex SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_sha256(sha256_url: str, filename: str) -> str:
    """Fetch a SHA256SUMS file and extract the hash for *filename*.

    Raises RuntimeError if the filename is not found in the checksum file.
    """
    resp = requests.get(sha256_url, timeout=30)
    resp.raise_for_status()
    for line in resp.text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1].lstrip("*") == filename:
            return parts[0]
    raise RuntimeError(
        f"Checksum for '{filename}' not found in {sha256_url}. "
        "The checksum file format may have changed."
    )


def _download(url: str, dest: Path, progress: bool = True) -> None:
    """Stream-download *url* to *dest*, showing a simple progress indicator."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    try:
        with requests.get(url, stream=True, timeout=60) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(tmp, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    if progress and total:
                        pct = downloaded * 100 // total
                        print(f"\r  {pct:3d}%  {downloaded // (1024**2)} MB / {total // (1024**2)} MB", end="", flush=True)
            if progress and total:
                print()
        tmp.rename(dest)
    except Exception:
        if tmp.exists():
            tmp.unlink()
        raise


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def cache_dir_for(config: dict) -> Path:
    """Resolve the image cache directory from config."""
    raw = config.get("image_cache_dir", "~/.velocitee/cache/images/")
    return Path(raw).expanduser()


def ensure_image(os_name: str, config: dict) -> Path:
    """Ensure the image for *os_name* is cached locally and checksum-verified.

    Downloads and verifies if not already present. Returns the local path.
    Raises RuntimeError with a clear message on any failure.
    """
    cache = cache_dir_for(config)
    cache.mkdir(parents=True, exist_ok=True)

    if os_name == "proxmox-ve":
        version, iso_url, sha256_url = _resolve_proxmox_latest()
        filename = iso_url.rsplit("/", 1)[-1]
    elif os_name == "ubuntu-server":
        version, iso_url, sha256_url = _resolve_ubuntu_latest_lts()
        filename = iso_url.rsplit("/", 1)[-1]
    else:
        raise ValueError(f"Unknown OS '{os_name}'. Must be 'proxmox-ve' or 'ubuntu-server'.")

    dest = cache / filename

    if dest.exists():
        return dest

    print(f"  Resolving {os_name} → version {version}")
    print(f"  Downloading {filename} ...")
    try:
        _download(iso_url, dest)
    except Exception as exc:
        raise RuntimeError(f"Failed to download {filename}: {exc}") from exc

    print(f"  Verifying checksum ...")
    try:
        expected = _expected_sha256(sha256_url, filename)
    except Exception as exc:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to fetch checksum: {exc}") from exc

    actual = _sha256_file(dest)
    if actual != expected:
        dest.unlink()
        raise RuntimeError(
            f"Checksum mismatch for {filename}.\n"
            f"  Expected: {expected}\n"
            f"  Got:      {actual}\n"
            "The file has been deleted. Try running 'vme images pull' again."
        )

    print(f"  Checksum OK.")
    return dest


def list_cached(config: dict) -> list[dict]:
    """Return a list of cached image dicts with name, path, size_mb."""
    cache = cache_dir_for(config)
    if not cache.exists():
        return []
    images = []
    for f in sorted(cache.glob("*.iso")):
        images.append({
            "filename": f.name,
            "path": str(f),
            "size_mb": round(f.stat().st_size / (1024**2), 1),
        })
    return images


def clean_cache(config: dict) -> int:
    """Delete all cached images. Returns number of files removed."""
    cache = cache_dir_for(config)
    if not cache.exists():
        return 0
    count = 0
    for f in cache.glob("*.iso"):
        f.unlink()
        count += 1
    for f in cache.glob("*.tmp"):
        f.unlink()
        count += 1
    return count
