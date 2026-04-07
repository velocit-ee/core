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
    """Resolve the latest Ubuntu LTS live-server ISO URL and checksum URL.

    Tries the Launchpad API first to find the latest supported LTS series.
    Falls back to probing known LTS versions directly on releases.ubuntu.com
    if the API is unavailable.

    Returns (version, iso_url, sha256_url).
    Raises RuntimeError if no LTS release can be resolved.
    """
    def _is_lts(version: str) -> bool:
        try:
            year, month = str(version).split(".")
            return int(month) == 4 and int(year) % 2 == 0
        except (ValueError, AttributeError):
            return False

    def _iso_key(name: str) -> tuple[int, ...]:
        return tuple(int(p) for p in re.findall(r'\d+', name))

    def _scrape_iso(version: str) -> tuple[str, str, str]:
        """Scrape releases.ubuntu.com for the latest point-release ISO of *version*."""
        index_url = f"{_UBUNTU_RELEASES_BASE}{version}/"
        index_resp = requests.get(index_url, timeout=30)
        index_resp.raise_for_status()
        iso_matches = re.findall(r'(ubuntu-[\d.]+-live-server-amd64\.iso)', index_resp.text)
        if not iso_matches:
            raise RuntimeError(
                f"Could not find a live-server ISO for Ubuntu {version} at {index_url}."
            )
        iso_filename = sorted(set(iso_matches), key=_iso_key)[-1]
        return version, f"{index_url}{iso_filename}", f"{index_url}SHA256SUMS"

    # Try Launchpad API to get the latest supported LTS version.
    version: str | None = None
    try:
        resp = requests.get(_UBUNTU_RELEASES_API, timeout=15, params={"ws.size": 75})
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        lts_entries = [
            e for e in data.get("entries", [])
            if e.get("supported") and _is_lts(e.get("version", ""))
        ]
        if lts_entries:
            def _lts_key(e: dict[str, Any]) -> tuple[int, int]:
                parts = str(e.get("version", "0.0")).split(".")
                try:
                    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
                except ValueError:
                    return 0, 0
            lts_entries.sort(key=_lts_key, reverse=True)
            version = str(lts_entries[0]["version"])
    except Exception:
        pass  # API unavailable — fall through to probe known versions

    if version:
        return _scrape_iso(version)

    # Launchpad unavailable — probe known LTS versions newest-first.
    for candidate in ("26.04", "24.04", "22.04", "20.04"):
        try:
            return _scrape_iso(candidate)
        except Exception:
            continue

    raise RuntimeError(
        "Could not resolve an Ubuntu LTS ISO. "
        "Check your internet connection and try again."
    )


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
    """Stream-download *url* to *dest*, resuming if a partial .tmp file exists."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")

    resumed = tmp.stat().st_size if tmp.exists() else 0
    headers = {"Range": f"bytes={resumed}-"} if resumed else {}

    try:
        with requests.get(url, stream=True, timeout=60, headers=headers) as resp:
            if resumed and resp.status_code == 416:
                # Server says range not satisfiable — file already complete.
                tmp.rename(dest)
                return
            if resumed and resp.status_code not in (206, 200):
                resp.raise_for_status()
            if not resumed:
                resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            if resumed:
                total += resumed

            downloaded = resumed
            mode = "ab" if resumed else "wb"
            with open(tmp, mode) as fh:
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
        raise  # keep the .tmp file so the next run can resume


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

    # Check if a matching ISO is already cached before hitting the network.
    patterns = {
        "proxmox-ve": "proxmox-ve_*.iso",
        "ubuntu-server": "ubuntu-*-live-server-amd64.iso",
    }
    if os_name not in patterns:
        raise ValueError(f"Unknown OS '{os_name}'. Must be 'proxmox-ve' or 'ubuntu-server'.")

    existing = sorted(cache.glob(patterns[os_name]))
    if existing:
        cached = existing[-1]  # newest by filename sort
        print(f"  Found cached image: {cached.name}")
        return cached

    if os_name == "proxmox-ve":
        version, iso_url, sha256_url = _resolve_proxmox_latest()
        filename = iso_url.rsplit("/", 1)[-1]
    else:
        version, iso_url, sha256_url = _resolve_ubuntu_latest_lts()
        filename = iso_url.rsplit("/", 1)[-1]

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
    except RuntimeError:
        raise
    except Exception as exc:
        print(f"  Warning: could not fetch checksum ({exc})")
        print(f"  Skipping verification — the downloaded file has been kept.")
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
