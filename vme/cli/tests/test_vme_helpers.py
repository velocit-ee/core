"""Tests for the pure helpers inside vme/cli/vme.py.

vme.py is the largest module in the repository and was at zero coverage. Most
of it drives Docker, PXE and real hardware and is not unit-testable without a
lab. These tests cover the parts that are: the iPXE boot-menu generator, the
log-event formatter operators watch during a deploy, and the initrd repack
pipeline that replaced a shell string.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vme.cli import vme


# ---------------------------------------------------------------------------
# _fmt_bytes / _fmt_duration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    # Sub-kilobyte values round to "0 KB". These are nginx transfer sizes for
    # boot artifacts, which are never that small, so the rounding is harmless
    # -- but it is the behaviour, so pin it rather than assume otherwise.
    ("512", "0 KB"),
    ("1024", "1 KB"),
    ("1536", "2 KB"),
    (str(5 * 1024 ** 2), "5.0 MB"),
    (str(3 * 1024 ** 3), "3.0 GB"),
])
def test_fmt_bytes_scales_to_the_right_unit(raw, expected):
    assert vme._fmt_bytes(raw) == expected


@pytest.mark.parametrize("raw", ["", "not-a-number", None])
def test_fmt_bytes_passes_through_unparseable_input(raw):
    # These strings come out of nginx access logs. A malformed one must not
    # crash the deploy monitor.
    assert vme._fmt_bytes(raw) == raw


@pytest.mark.parametrize("seconds,expected", [
    (0, "0s"), (9, "9s"), (59, "59s"),
    (60, "1m 00s"), (61, "1m 01s"), (3661, "61m 01s"),
])
def test_fmt_duration(seconds, expected):
    assert vme._fmt_duration(seconds) == expected


# ---------------------------------------------------------------------------
# _format_event
# ---------------------------------------------------------------------------

def test_format_event_returns_none_when_the_pattern_does_not_match():
    pattern = re.compile(r"nothing-here")
    assert vme._format_event(pattern, "x", "some log line") is None


def test_format_event_substitutes_capture_groups():
    pattern = re.compile(r"lease (\d+\.\d+\.\d+\.\d+) to (\S+)")
    out = vme._format_event(pattern, "got {1} for {2}", "lease 10.0.0.5 to aa:bb")
    assert out == "got 10.0.0.5 for aa:bb"


def test_format_event_formats_a_byte_group():
    pattern = re.compile(r"sent (\d+)")
    out = vme._format_event(pattern, "size {1B}", "sent 2097152")
    assert out == "size 2.0 MB"


def test_format_event_handles_an_unmatched_optional_group():
    pattern = re.compile(r"a(x)?b")
    assert vme._format_event(pattern, "[{1}]", "ab") == "[]"


@pytest.mark.parametrize("pattern,template,dedupe", [
    e for e in vme._LOG_EVENTS if e[1] is not None
])
def test_every_configured_log_event_has_a_usable_template(pattern, template, dedupe):
    # Guards against a template referencing a group the regex does not capture.
    assert isinstance(template, str) and template
    for i in range(pattern.groups + 1, 5):
        assert f"{{{i}}}" not in template, f"template references missing group {i}"
        assert f"{{{i}B}}" not in template


def test_dhcp_ack_event_matches_a_real_dnsmasq_line():
    pattern, template, _ = vme._LOG_EVENTS[1]
    line = "dnsmasq-dhcp[1]: DHCPACK(eth0) 10.20.0.55 aa:bb:cc:dd:ee:ff"
    out = vme._format_event(pattern, template, line)
    assert out is not None
    assert "10.20.0.55" in out and "aa:bb:cc:dd:ee:ff" in out


def test_iso_download_event_formats_the_size():
    pattern, template, _ = next(
        e for e in vme._LOG_EVENTS if "OS image downloaded" in (e[1] or "")
    )
    line = '10.0.0.5 - - "GET /images/proxmox.iso HTTP/1.1" 200 1073741824'
    out = vme._format_event(pattern, template, line)
    assert "proxmox.iso" in out and "1.0 GB" in out


# ---------------------------------------------------------------------------
# _generate_boot_ipxe
# ---------------------------------------------------------------------------

def test_boot_menu_with_no_images_tells_the_operator_what_to_run():
    script = vme._generate_boot_ipxe("10.0.0.1", [], "proxmox")
    assert script.startswith("#!ipxe")
    assert "vme images pull" in script
    assert "http://10.0.0.1" in script


def test_boot_menu_lists_every_entry():
    entries = [
        ("proxmox", "pve", "Proxmox VE 8", Path("/img/pve.iso")),
        ("ubuntu", "ubu", "Ubuntu Server 24.04", Path("/img/ubu.iso")),
    ]
    script = vme._generate_boot_ipxe("10.0.0.1", entries, "ubuntu")
    assert "Proxmox VE 8" in script
    assert "Ubuntu Server 24.04" in script
    assert script.startswith("#!ipxe")


def test_boot_menu_selects_the_requested_default():
    entries = [
        ("proxmox", "pve", "Proxmox VE 8", Path("/img/pve.iso")),
        ("ubuntu", "ubu", "Ubuntu Server 24.04", Path("/img/ubu.iso")),
    ]
    assert "--default ubu" in vme._generate_boot_ipxe("10.0.0.1", entries, "ubuntu")


def test_boot_menu_falls_back_to_the_first_entry_for_an_unknown_default():
    entries = [
        ("proxmox", "pve", "Proxmox VE 8", Path("/img/pve.iso")),
        ("ubuntu", "ubu", "Ubuntu Server 24.04", Path("/img/ubu.iso")),
    ]
    assert "--default pve" in vme._generate_boot_ipxe("10.0.0.1", entries, "nonexistent")


def test_boot_menu_never_emits_a_literal_semicolon_in_a_command():
    # iPXE treats ';' as a command separator, which is why the script sets
    # sc:hex instead. A literal one would silently truncate a command line.
    entries = [("proxmox", "pve", "Proxmox VE 8", Path("/img/pve.iso"))]
    script = vme._generate_boot_ipxe("10.0.0.1", entries, "proxmox")
    assert "set sc:hex 3b" in script


# ---------------------------------------------------------------------------
# _manifest_outdir
# ---------------------------------------------------------------------------

def test_manifest_outdir_honours_the_config_override():
    assert vme._manifest_outdir({"manifest_output_dir": "/tmp/out"}) == Path("/tmp/out")


def test_manifest_outdir_expands_a_tilde():
    result = vme._manifest_outdir({"manifest_output_dir": "~/manifests"})
    assert "~" not in str(result)
    assert result.is_absolute()


def test_manifest_outdir_falls_back_to_the_default():
    assert vme._manifest_outdir({}) == vme._MANIFEST_OUTDIR


# ---------------------------------------------------------------------------
# _repack_initrd
# ---------------------------------------------------------------------------

needs_cpio = pytest.mark.skipif(
    not all(shutil.which(b) for b in ("find", "cpio", "zstd")),
    reason="requires find, cpio and zstd on PATH",
)


@needs_cpio
def test_repack_initrd_produces_a_readable_archive(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "lib").mkdir(parents=True)
    (rootfs / "init").write_text("#!/bin/sh\necho hello\n")
    (rootfs / "lib" / "mod.ko").write_bytes(b"\x00" * 32)
    dest = tmp_path / "initrd.zst"

    vme._repack_initrd(rootfs, dest)

    assert dest.exists() and dest.stat().st_size > 0
    listing = subprocess.run(
        f"zstd -dc {dest} | cpio -it",
        shell=True, capture_output=True, text=True,
    ).stdout
    # BSD cpio (macOS) keeps the "./" that `find .` emits; GNU cpio (Linux)
    # normalises it away. Both archives are correct, so compare on the paths
    # rather than on whichever prefix the local cpio happens to print.
    entries = {line.strip().lstrip("./") for line in listing.splitlines() if line.strip()}
    assert "init" in entries
    assert "lib/mod.ko" in entries


@needs_cpio
def test_repack_initrd_survives_a_path_containing_a_space(tmp_path):
    # This is the case the old shell=True pipeline got wrong: the destination
    # was interpolated straight into a shell command line.
    rootfs = tmp_path / "root fs"
    rootfs.mkdir()
    (rootfs / "init").write_text("x")
    dest = tmp_path / "out dir" / "initrd.zst"
    dest.parent.mkdir()

    vme._repack_initrd(rootfs, dest)
    assert dest.exists() and dest.stat().st_size > 0


@needs_cpio
def test_repack_initrd_survives_a_path_containing_shell_metacharacters(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "init").write_text("x")
    dest = tmp_path / "a;b$(id)`whoami`.zst"

    vme._repack_initrd(rootfs, dest)
    assert dest.exists()
    # The filename survived verbatim: nothing expanded it.
    assert dest.name == "a;b$(id)`whoami`.zst"


def test_repack_initrd_raises_when_a_stage_fails(tmp_path):
    # zstd cannot write into a directory that does not exist.
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "init").write_text("x")
    dest = tmp_path / "missing" / "deep" / "initrd.zst"

    if not all(shutil.which(b) for b in ("find", "cpio", "zstd")):
        pytest.skip("requires find, cpio and zstd on PATH")

    with pytest.raises(RuntimeError, match="repack failed"):
        vme._repack_initrd(rootfs, dest)
