"""Tests for cli/images.py — cache management and version resolution (no network)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cli import images as img


_CONFIG = {"image_cache_dir": "~/.velocitee/cache/images/"}


# ---------------------------------------------------------------------------
# cache_dir_for
# ---------------------------------------------------------------------------


def test_cache_dir_default():
    path = img.cache_dir_for({})
    assert str(path).endswith("/.velocitee/cache/images")


def test_cache_dir_custom():
    path = img.cache_dir_for({"image_cache_dir": "/tmp/myimages/"})
    assert str(path) == "/tmp/myimages"


# ---------------------------------------------------------------------------
# list_cached
# ---------------------------------------------------------------------------


def test_list_cached_empty(tmp_path):
    cfg = {"image_cache_dir": str(tmp_path)}
    assert img.list_cached(cfg) == []


def test_list_cached_returns_isos(tmp_path):
    (tmp_path / "proxmox-ve_8.2-1.iso").write_bytes(b"x" * 1024 * 1024)
    (tmp_path / "ubuntu-24.04-live-server-amd64.iso").write_bytes(b"y" * 2 * 1024 * 1024)
    cfg = {"image_cache_dir": str(tmp_path)}
    result = img.list_cached(cfg)
    filenames = [r["filename"] for r in result]
    assert "proxmox-ve_8.2-1.iso" in filenames
    assert "ubuntu-24.04-live-server-amd64.iso" in filenames


def test_list_cached_ignores_non_iso(tmp_path):
    (tmp_path / "some.tmp").write_bytes(b"x")
    (tmp_path / "readme.txt").write_bytes(b"y")
    cfg = {"image_cache_dir": str(tmp_path)}
    assert img.list_cached(cfg) == []


# ---------------------------------------------------------------------------
# clean_cache
# ---------------------------------------------------------------------------


def test_clean_removes_isos(tmp_path):
    for name in ["a.iso", "b.iso", "c.tmp"]:
        (tmp_path / name).write_bytes(b"x")
    cfg = {"image_cache_dir": str(tmp_path)}
    count = img.clean_cache(cfg)
    assert count == 3
    assert list(tmp_path.glob("*.iso")) == []


def test_clean_empty_dir(tmp_path):
    cfg = {"image_cache_dir": str(tmp_path)}
    assert img.clean_cache(cfg) == 0


def test_clean_nonexistent_dir(tmp_path):
    cfg = {"image_cache_dir": str(tmp_path / "nonexistent")}
    assert img.clean_cache(cfg) == 0


# ---------------------------------------------------------------------------
# ensure_image — cached path (no download)
# ---------------------------------------------------------------------------


def test_ensure_image_returns_cached_file(tmp_path):
    iso = tmp_path / "proxmox-ve_8.2-1.iso"
    iso.write_bytes(b"fake-iso")
    cfg = {"image_cache_dir": str(tmp_path)}

    with patch.object(img, "_resolve_proxmox_latest", return_value=("8.2-1", "http://x/proxmox-ve_8.2-1.iso", "http://x/SHA256SUMS")):
        result = img.ensure_image("proxmox-ve", cfg)

    assert result == iso


# ---------------------------------------------------------------------------
# ensure_image — checksum mismatch aborts and deletes
# ---------------------------------------------------------------------------


def test_ensure_image_checksum_mismatch_deletes_file(tmp_path):
    cfg = {"image_cache_dir": str(tmp_path)}
    fake_content = b"corrupted-iso-data"
    filename = "proxmox-ve_8.2-1.iso"
    iso_url = f"http://example.com/{filename}"
    sha256_url = "http://example.com/SHA256SUMS"

    # Simulate download writing the file
    def fake_download(url, dest, progress=True):
        dest.write_bytes(fake_content)

    with patch.object(img, "_resolve_proxmox_latest", return_value=("8.2-1", iso_url, sha256_url)), \
         patch.object(img, "_download", side_effect=fake_download), \
         patch.object(img, "_expected_sha256", return_value="deadbeef" * 8):
        with pytest.raises(RuntimeError, match="Checksum mismatch"):
            img.ensure_image("proxmox-ve", cfg)

    # File must be deleted on mismatch
    assert not (tmp_path / filename).exists()


# ---------------------------------------------------------------------------
# ensure_image — unknown OS
# ---------------------------------------------------------------------------


def test_ensure_image_unknown_os(tmp_path):
    cfg = {"image_cache_dir": str(tmp_path)}
    with pytest.raises(ValueError, match="Unknown OS"):
        img.ensure_image("freebsd", cfg)


# ---------------------------------------------------------------------------
# _expected_sha256 parsing
# ---------------------------------------------------------------------------


def test_expected_sha256_found():
    sha256_text = (
        "abc123  proxmox-ve_8.2-1.iso\n"
        "def456  other.iso\n"
    )
    mock_resp = MagicMock()
    mock_resp.text = sha256_text
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        result = img._expected_sha256("http://x/SHA256SUMS", "proxmox-ve_8.2-1.iso")
    assert result == "abc123"


def test_expected_sha256_not_found():
    mock_resp = MagicMock()
    mock_resp.text = "abc123  other.iso\n"
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="not found"):
            img._expected_sha256("http://x/SHA256SUMS", "proxmox-ve_8.2-1.iso")


def test_expected_sha256_star_prefix():
    """Some checksum files use *filename format."""
    sha256_text = "abc123  *ubuntu-24.04-live-server-amd64.iso\n"
    mock_resp = MagicMock()
    mock_resp.text = sha256_text
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        result = img._expected_sha256("http://x/SHA256SUMS", "ubuntu-24.04-live-server-amd64.iso")
    assert result == "abc123"


# ---------------------------------------------------------------------------
# _resolve_proxmox_latest version selection
# ---------------------------------------------------------------------------


def test_resolve_proxmox_picks_highest_version():
    html = """
    <a href="proxmox-ve_7.4-1.iso">proxmox-ve_7.4-1.iso</a>
    <a href="proxmox-ve_8.1-2.iso">proxmox-ve_8.1-2.iso</a>
    <a href="proxmox-ve_8.2-1.iso">proxmox-ve_8.2-1.iso</a>
    """
    mock_resp = MagicMock()
    mock_resp.text = html
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        version, iso_url, sha256_url = img._resolve_proxmox_latest()

    assert version == "8.2-1"
    assert "proxmox-ve_8.2-1.iso" in iso_url


def test_resolve_proxmox_no_isos_raises():
    mock_resp = MagicMock()
    mock_resp.text = "<html>no isos here</html>"
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="Could not find"):
            img._resolve_proxmox_latest()


# ---------------------------------------------------------------------------
# _resolve_ubuntu_latest_lts version selection
# ---------------------------------------------------------------------------


def test_resolve_ubuntu_picks_latest_lts():
    api_response = {
        "entries": [
            {"version": "20.04", "supported": True, "lts": True},
            {"version": "22.04", "supported": True, "lts": True},
            {"version": "24.04", "supported": True, "lts": True},
            {"version": "23.10", "supported": False, "lts": False},
        ]
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        version, iso_url, sha256_url = img._resolve_ubuntu_latest_lts()

    assert version == "24.04"
    assert "24.04" in iso_url


def test_resolve_ubuntu_no_lts_raises():
    api_response = {"entries": [{"version": "23.10", "supported": False, "lts": False}]}
    mock_resp = MagicMock()
    mock_resp.json.return_value = api_response
    mock_resp.raise_for_status = MagicMock()

    with patch("requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError, match="No supported LTS"):
            img._resolve_ubuntu_latest_lts()
