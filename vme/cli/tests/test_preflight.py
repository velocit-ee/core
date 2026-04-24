"""Tests for cli/preflight.py — all run without hardware or Docker."""

from __future__ import annotations

import textwrap
from unittest.mock import MagicMock, patch


from cli import preflight as pf


# ---------------------------------------------------------------------------
# check_docker
# ---------------------------------------------------------------------------


def test_docker_not_installed():
    with patch("shutil.which", return_value=None):
        result = pf.check_docker()
    assert not result.passed
    assert "not found" in result.detail


def test_docker_daemon_not_running():
    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        result = pf.check_docker()
    assert not result.passed
    assert "not running" in result.detail


def test_docker_running():
    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        result = pf.check_docker()
    assert result.passed


def test_docker_timeout():
    import subprocess
    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired("docker", 10)):
        result = pf.check_docker()
    assert not result.passed
    assert "timed out" in result.detail


# ---------------------------------------------------------------------------
# check_interface
# ---------------------------------------------------------------------------


def test_interface_missing():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1, stdout="")
        result = pf.check_interface("eth99")
    assert not result.passed
    assert "not found" in result.detail


def test_interface_down():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="eth0: <BROADCAST> state DOWN")
        result = pf.check_interface("eth0")
    assert not result.passed
    assert "not up" in result.detail


def test_interface_up():
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="eth0: <BROADCAST,MULTICAST,UP> state UP")
        result = pf.check_interface("eth0")
    assert result.passed


def test_interface_unknown_state_accepted():
    """UNKNOWN is a valid state for some NICs (e.g. loopback, some virtio)."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="eth0: <> state UNKNOWN")
        result = pf.check_interface("eth0")
    assert result.passed


# ---------------------------------------------------------------------------
# check_no_conflicting_dhcp
# ---------------------------------------------------------------------------


def test_dhcp_no_nmap_soft_pass():
    with patch("shutil.which", return_value=None):
        result = pf.check_no_conflicting_dhcp("eth0")
    assert result.passed
    assert "nmap not found" in result.detail


def test_dhcp_conflict_detected():
    with patch("shutil.which", return_value="/usr/bin/nmap"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Server Identifier: 192.168.1.1",
            stderr="",
        )
        result = pf.check_no_conflicting_dhcp("eth0")
    assert not result.passed
    assert "DHCP server" in result.detail


def test_dhcp_no_conflict():
    with patch("shutil.which", return_value="/usr/bin/nmap"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="No results.", stderr="")
        result = pf.check_no_conflicting_dhcp("eth0")
    assert result.passed


# ---------------------------------------------------------------------------
# check_config
# ---------------------------------------------------------------------------


def test_config_missing(tmp_path):
    result = pf.check_config(tmp_path / "nonexistent.yml")
    assert not result.passed
    assert "not found" in result.detail


def test_config_invalid_yaml(tmp_path):
    cfg = tmp_path / "vme-config.yml"
    cfg.write_text("key: [unclosed")
    result = pf.check_config(cfg)
    assert not result.passed
    assert "YAML" in result.detail


def test_config_missing_field(tmp_path):
    cfg = tmp_path / "vme-config.yml"
    cfg.write_text(textwrap.dedent("""\
        dhcp_range_start: 192.168.100.100
        dhcp_range_end: 192.168.100.200
        target:
          hostname: node-01
          ip: 192.168.100.10
          gateway: 192.168.100.1
          netmask: 255.255.255.0
          os: proxmox-ve
          disk: /dev/sda
          ssh_public_key: "ssh-ed25519 AAAA..."
    """))
    result = pf.check_config(cfg)
    assert not result.passed
    assert "provisioning_interface" in result.detail


def test_config_invalid_os(tmp_path):
    cfg = tmp_path / "vme-config.yml"
    cfg.write_text(textwrap.dedent("""\
        provisioning_interface: eth0
        dhcp_range_start: 192.168.100.100
        dhcp_range_end: 192.168.100.200
        target:
          hostname: node-01
          ip: 192.168.100.10
          gateway: 192.168.100.1
          netmask: 255.255.255.0
          os: windows
          disk: /dev/sda
          ssh_public_key: "ssh-ed25519 AAAA..."
    """))
    result = pf.check_config(cfg)
    assert not result.passed
    assert "windows" in result.detail


def test_config_valid(tmp_path):
    cfg = tmp_path / "vme-config.yml"
    cfg.write_text(textwrap.dedent("""\
        provisioning_interface: eth0
        dhcp_range_start: 192.168.100.100
        dhcp_range_end: 192.168.100.200
        target:
          hostname: node-01
          ip: 192.168.100.10
          gateway: 192.168.100.1
          netmask: 255.255.255.0
          os: proxmox-ve
          disk: /dev/sda
          ssh_public_key: "ssh-ed25519 AAAA..."
    """))
    result = pf.check_config(cfg)
    assert result.passed


# ---------------------------------------------------------------------------
# check_disk_space
# ---------------------------------------------------------------------------


def test_disk_space_sufficient(tmp_path):
    from collections import namedtuple
    DiskUsage = namedtuple("usage", ["total", "used", "free"])
    with patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = DiskUsage(total=100 * 1024**3, used=10 * 1024**3, free=90 * 1024**3)
        result = pf.check_disk_space(tmp_path / "cache")
    assert result.passed


def test_disk_space_insufficient(tmp_path):
    from collections import namedtuple
    DiskUsage = namedtuple("usage", ["total", "used", "free"])
    with patch("shutil.disk_usage") as mock_du:
        mock_du.return_value = DiskUsage(total=30 * 1024**3, used=25 * 1024**3, free=5 * 1024**3)
        result = pf.check_disk_space(tmp_path / "cache")
    assert not result.passed
    assert "GB free" in result.detail
