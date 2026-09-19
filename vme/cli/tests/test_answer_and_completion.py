"""Proxmox answer-file rendering and the deploy completion signal.

Three findings are pinned here:

  M-01  The auto-installer's [network] `filter` is a udev-property *table*,
        not a string. VME rendered `filter = "eth0"`, which the answer-file
        check rejects — so no Proxmox target could ever install unattended.
  M-02  Only the Ubuntu autoinstall ever signalled completion. Proxmox had no
        hook, so `vme deploy` never finished and never wrote a manifest.
  M-04  The completion signal was an unauthenticated GET any host on the
        provisioning LAN could send, which made VME write a success manifest
        for a machine that never installed.
"""

from __future__ import annotations

import string
import tomllib
from pathlib import Path

import pytest

from vme.cli import vme


ANSWER_TEMPLATE = Path(vme.__file__).parent.parent / "targets" / "proxmox" / "answer.toml"


def _cfg(**target_extra) -> dict:
    target = {
        "hostname": "node-01",
        "ip": "192.168.100.10",
        "prefix": 24,
        "gateway": "192.168.100.1",
        "netmask": "255.255.255.0",
        "dns": "8.8.8.8",
        "os": "proxmox-ve",
        "disk": "/dev/sda",
        "timezone": "Europe/Berlin",
        "password_hash": "$6$salt$hash",
        "ssh_public_key": "ssh-ed25519 AAAA test@host",
    }
    target.update(target_extra)
    return {
        "provisioning_interface": "eth1",
        "dhcp_range_start": "192.168.100.100",
        "dhcp_range_end": "192.168.100.200",
        "target": target,
    }


def _render_answer(cfg: dict, *, deploy_token: str = "tok123") -> dict:
    subs = vme._template_subs(cfg, "192.168.100.1", deploy_token=deploy_token)
    rendered = string.Template(ANSWER_TEMPLATE.read_text()).safe_substitute(subs)
    assert "${" not in rendered, f"unsubstituted variable left in answer.toml: {rendered}"
    return tomllib.loads(rendered)


# ---------------------------------------------------------------------------
# M-01 — the NIC filter
# ---------------------------------------------------------------------------

def test_rendered_answer_file_is_valid_toml():
    # The whole point: the installer parses this file. If it does not parse
    # here, it will not parse there.
    _render_answer(_cfg())


def test_network_filter_is_a_table_not_a_string():
    answer = _render_answer(_cfg())
    assert isinstance(answer["network"]["filter"], dict), (
        "filter must be a udev-property table (filter.ID_NET_NAME_MAC = ...); "
        "a bare string is a schema violation the installer refuses"
    )
    assert "ID_NET_NAME_MAC" in answer["network"]["filter"]


def test_configured_mac_becomes_a_glob_on_the_udev_name():
    answer = _render_answer(_cfg(mac="E4:3D:1A:FA:37:9A"))
    # ID_NET_NAME_MAC looks like 'enxe43d1afa379a' — lowercase, no separators.
    assert answer["network"]["filter"]["ID_NET_NAME_MAC"] == "*e43d1afa379a"


def test_absent_mac_selects_the_first_nic():
    answer = _render_answer(_cfg())
    assert answer["network"]["filter"]["ID_NET_NAME_MAC"] == "*"


@pytest.mark.parametrize("raw,expected", [
    ("aa:bb:cc:dd:ee:ff", "aabbccddeeff"),
    ("AA-BB-CC-DD-EE-FF", "aabbccddeeff"),
    ("aabbccddeeff", "aabbccddeeff"),
    ("", ""),
    (None, ""),
])
def test_mac_suffix_normalises_every_accepted_form(raw, expected):
    assert vme._mac_suffix(raw) == expected


@pytest.mark.parametrize("bad", ["aa:bb:cc:dd:ee", "not-a-mac", "aa:bb:cc:dd:ee:ff:00"])
def test_mac_suffix_rejects_a_non_mac(bad):
    # Rendering '*' for a typo'd MAC would silently install onto whichever NIC
    # the installer happened to enumerate first.
    with pytest.raises(ValueError):
        vme._mac_suffix(bad)


def test_password_hash_is_rendered_hashed_never_plaintext():
    answer = _render_answer(_cfg())
    assert answer["global"]["root-password-hashed"] == "$6$salt$hash"
    assert "root-password" not in answer["global"]


# ---------------------------------------------------------------------------
# M-02 — Proxmox signals completion
# ---------------------------------------------------------------------------

def test_answer_file_carries_a_post_installation_webhook():
    answer = _render_answer(_cfg(), deploy_token="abc123")
    url = answer["post-installation-webhook"]["url"]
    assert url == "http://192.168.100.1/vme-provision-complete/abc123"


def test_ubuntu_autoinstall_signals_with_the_same_token():
    preseed = (Path(vme.__file__).parent.parent / "targets" / "ubuntu" / "preseed.cfg").read_text()
    subs = vme._template_subs(_cfg(), "192.168.100.1", deploy_token="abc123")
    rendered = string.Template(preseed).safe_substitute(subs)
    assert "/vme-provision-complete/abc123" in rendered


# ---------------------------------------------------------------------------
# M-04 — the signal is authenticated
# ---------------------------------------------------------------------------

def _log_line(ip: str, token: str) -> str:
    # nginx combined format, as it appears through `docker compose logs`.
    return (
        f'nginx-1  | {ip} - - [19/Sep/2026:10:00:00 +0000] '
        f'"GET /vme-provision-complete/{token} HTTP/1.1" 204 0 "-" "curl/8.5.0"'
    )


def test_completion_accepts_the_right_token_from_the_expected_address():
    accepted, why = vme._completion_event(
        _log_line("192.168.100.10", "tok123"),
        deploy_token="tok123", expected_ips={"192.168.100.10"},
    )
    assert accepted and why == ""


def test_completion_rejects_a_wrong_token():
    accepted, why = vme._completion_event(
        _log_line("192.168.100.10", "guessed"),
        deploy_token="tok123", expected_ips={"192.168.100.10"},
    )
    assert not accepted
    assert "wrong deploy token" in why


def test_completion_rejects_a_bare_path_with_no_token():
    # This is exactly the old signal: any host could send it.
    line = 'nginx-1  | 192.168.100.77 - - [19/Sep/2026:10:00:00 +0000] "GET /vme-provision-complete HTTP/1.1" 204 0'
    accepted, _ = vme._completion_event(line, deploy_token="tok123", expected_ips=set())
    assert not accepted


def test_completion_rejects_an_unexpected_source_address():
    accepted, why = vme._completion_event(
        _log_line("192.168.100.250", "tok123"),
        deploy_token="tok123", expected_ips={"192.168.100.10"},
    )
    assert not accepted
    assert "unexpected address" in why


def test_completion_ignores_an_unrelated_log_line():
    accepted, why = vme._completion_event(
        'nginx-1  | 10.0.0.9 - - [19/Sep/2026:10:00:00 +0000] "GET /images/pve.iso HTTP/1.1" 200 1024',
        deploy_token="tok123", expected_ips={"10.0.0.9"},
    )
    assert not accepted and why == ""


def test_completion_never_accepts_when_no_token_was_issued():
    # Empty token must fail closed, not match everything.
    accepted, _ = vme._completion_event(
        _log_line("192.168.100.10", "anything"),
        deploy_token="", expected_ips={"192.168.100.10"},
    )
    assert not accepted


def test_dhcpack_regex_learns_the_lease_address_and_mac():
    line = "dnsmasq-dhcp-1  | dnsmasq-dhcp[1]: DHCPACK(eth1) 192.168.100.123 aa:bb:cc:dd:ee:ff node-01"
    m = vme._DHCPACK_RE.search(line)
    assert m and m.group(1) == "192.168.100.123" and m.group(2) == "aa:bb:cc:dd:ee:ff"
