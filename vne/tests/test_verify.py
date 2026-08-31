"""Tests for the VNE post-provisioning verification gate.

The gate decides whether VNE is allowed to write its handoff manifest, so a
check that wrongly returns PASS is worse than one that crashes: it lets a
broken network propagate to VSE/VLE as if it were verified. These tests pin
both the pass and fail branch of every check, and pin the TLS policy, which
carries the API credentials.
"""

from __future__ import annotations

import socket
import struct
from unittest import mock

import pytest
import requests

from vne.scripts import verify


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


def dns_reply(txid: bytes, *, rcode: int = 0, ancount: int = 1) -> bytes:
    """Minimal DNS response header the checker knows how to read."""
    flags = bytes([0x81, 0x80 | rcode])
    return txid + flags + struct.pack("!HHHH", 1, ancount, 0, 0) + b"\x00" * 8


# ---------------------------------------------------------------------------
# TLS policy
# ---------------------------------------------------------------------------

def test_tls_verify_defaults_to_verifying(monkeypatch):
    monkeypatch.delenv("OPNSENSE_INSECURE", raising=False)
    assert verify.tls_verify() is True


def test_tls_verify_opt_out_requires_the_exact_value(monkeypatch):
    # Anything other than "1" leaves verification on. A typo in the env var
    # must not silently disable certificate checking.
    for value in ("0", "", "true", "yes", "2"):
        monkeypatch.setenv("OPNSENSE_INSECURE", value)
        assert verify.tls_verify() is True, f"OPNSENSE_INSECURE={value!r}"


def test_tls_verify_honours_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("OPNSENSE_INSECURE", "1")
    assert verify.tls_verify() is False


def test_tls_policy_matches_the_opnsense_client(monkeypatch):
    # The gate and the client both authenticate to the same appliance with the
    # same credentials. If their defaults diverge, one of them is wrong.
    from shared.renderers import _opnsense_client

    monkeypatch.delenv("OPNSENSE_INSECURE", raising=False)
    client = _opnsense_client.OPNsenseClient("10.0.0.1", "key", "secret")
    assert client._session.verify is verify.tls_verify()


def test_tls_hint_only_fires_on_certificate_errors():
    assert "certificate not trusted" in verify._tls_hint(requests.exceptions.SSLError("bad cert"))
    assert verify._tls_hint(requests.exceptions.ConnectTimeout("timeout")) == ""
    assert verify._tls_hint(ValueError("unrelated")) == ""


# ---------------------------------------------------------------------------
# check_api_reachable
# ---------------------------------------------------------------------------

def test_api_reachable_passes_on_200(monkeypatch):
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(200))
    result = verify.check_api_reachable("10.0.0.1")
    assert result.passed is True
    assert result.name == "api_reachable"


def test_api_reachable_fails_on_non_200(monkeypatch):
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(403))
    result = verify.check_api_reachable("10.0.0.1")
    assert result.passed is False
    assert "403" in result.detail


def test_api_reachable_fails_closed_on_transport_error(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(verify.requests, "get", boom)
    result = verify.check_api_reachable("10.0.0.1")
    assert result.passed is False
    assert "no route to host" in result.detail


def test_api_reachable_appends_the_tls_hint_on_certificate_failure(monkeypatch):
    def boom(*a, **k):
        raise requests.exceptions.SSLError("self signed certificate")

    monkeypatch.setattr(verify.requests, "get", boom)
    result = verify.check_api_reachable("10.0.0.1")
    assert result.passed is False
    assert "OPNSENSE_INSECURE=1" in result.detail


def test_api_reachable_passes_verification_setting_to_requests(monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs)
        seen["url"] = url
        return FakeResponse(200)

    monkeypatch.setattr(verify.requests, "get", capture)
    monkeypatch.delenv("OPNSENSE_INSECURE", raising=False)
    verify.check_api_reachable("10.0.0.1", api_key="k", api_secret="s")

    assert seen["verify"] is True, "credentials must not go out over an unverified channel"
    assert seen["auth"] == ("k", "s")
    assert seen["url"].startswith("https://")


def test_api_reachable_sends_no_auth_when_credentials_are_absent(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        verify.requests, "get",
        lambda url, **kw: (seen.update(kw), FakeResponse(200))[1],
    )
    verify.check_api_reachable("10.0.0.1", api_key="k", api_secret=None)
    assert seen["auth"] is None


# ---------------------------------------------------------------------------
# check_dns_resolving
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_dns(monkeypatch):
    """Patch socket.socket so the DNS check talks to a scripted responder."""
    state = {"sent": None, "reply": None, "raises": None}

    class FakeSocket:
        def __init__(self, *a, **k):
            pass

        def settimeout(self, _t):
            pass

        def sendto(self, packet, _addr):
            state["sent"] = packet

        def recvfrom(self, _n):
            if state["raises"]:
                raise state["raises"]
            return state["reply"](state["sent"]), ("10.0.0.1", 53)

        def close(self):
            pass

    monkeypatch.setattr(verify.socket, "socket", FakeSocket)
    return state


def test_dns_passes_when_the_server_answers(fake_dns):
    fake_dns["reply"] = lambda sent: dns_reply(sent[:2], ancount=2)
    result = verify.check_dns_resolving("10.0.0.1")
    assert result.passed is True
    assert "answers=2" in result.detail


def test_dns_fails_on_txid_mismatch(fake_dns):
    # A reply carrying someone else's transaction ID is the signature of an
    # off-path spoofing attempt, not a healthy resolver.
    fake_dns["reply"] = lambda sent: dns_reply(b"\xff\xff")
    result = verify.check_dns_resolving("10.0.0.1")
    assert result.passed is False
    assert "TXID mismatch" in result.detail


def test_dns_fails_on_nonzero_rcode(fake_dns):
    fake_dns["reply"] = lambda sent: dns_reply(sent[:2], rcode=3)  # NXDOMAIN
    result = verify.check_dns_resolving("10.0.0.1")
    assert result.passed is False
    assert "rcode=3" in result.detail


def test_dns_fails_when_there_are_no_answers(fake_dns):
    fake_dns["reply"] = lambda sent: dns_reply(sent[:2], ancount=0)
    result = verify.check_dns_resolving("10.0.0.1")
    assert result.passed is False
    assert "0 answers" in result.detail


def test_dns_fails_on_timeout(fake_dns):
    fake_dns["raises"] = socket.timeout("timed out")
    result = verify.check_dns_resolving("10.0.0.1")
    assert result.passed is False
    assert "failed" in result.detail


def test_dns_query_is_addressed_to_the_opnsense_resolver(fake_dns):
    # The point of the check is to exercise the new appliance's resolver, not
    # whatever /etc/resolv.conf happens to say.
    fake_dns["reply"] = lambda sent: dns_reply(sent[:2])
    verify.check_dns_resolving("10.0.0.1", hostname="one.one.one.one")
    assert b"\x03one\x03one\x03one\x03one\x00" in fake_dns["sent"]


# ---------------------------------------------------------------------------
# check_internet_egress
# ---------------------------------------------------------------------------

def test_egress_passes_when_the_connection_succeeds(monkeypatch):
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **k: mock.MagicMock())
    result = verify.check_internet_egress()
    assert result.passed is True


def test_egress_reports_itself_as_seed_egress(monkeypatch):
    # Named deliberately: this measures the seed machine's connectivity, not
    # egress through the new appliance. The manifest must not overclaim.
    monkeypatch.setattr(verify.socket, "create_connection", lambda *a, **k: mock.MagicMock())
    assert verify.check_internet_egress().name == "seed_egress"


def test_egress_fails_when_unreachable(monkeypatch):
    def boom(*a, **k):
        raise OSError("network unreachable")

    monkeypatch.setattr(verify.socket, "create_connection", boom)
    result = verify.check_internet_egress()
    assert result.passed is False
    assert "unreachable" in result.detail


# ---------------------------------------------------------------------------
# check_vlans_up
# ---------------------------------------------------------------------------

def test_vlans_pass_when_all_expected_are_present(monkeypatch):
    payload = {"rows": [{"tag": "10"}, {"tag": "20"}, {"tag": "99"}]}
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    result = verify.check_vlans_up("10.0.0.1", [10, 20], api_key="k", api_secret="s")
    assert result.passed is True


def test_vlans_fail_and_name_the_missing_ones(monkeypatch):
    payload = {"rows": [{"tag": "10"}]}
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    result = verify.check_vlans_up("10.0.0.1", [10, 20, 99], api_key="k", api_secret="s")
    assert result.passed is False
    assert "20" in result.detail and "99" in result.detail


def test_vlans_skip_cleanly_without_credentials():
    result = verify.check_vlans_up("10.0.0.1", [10])
    assert result.passed is True
    assert "skipped" in result.detail


def test_vlans_pass_trivially_when_none_are_expected():
    result = verify.check_vlans_up("10.0.0.1", [], api_key="k", api_secret="s")
    assert result.passed is True
    assert "nothing to check" in result.detail


def test_vlans_ignore_non_numeric_tags(monkeypatch):
    payload = {"rows": [{"tag": "10"}, {"tag": ""}, {"tag": None}, {"tag": "abc"}]}
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(200, payload))
    result = verify.check_vlans_up("10.0.0.1", [10], api_key="k", api_secret="s")
    assert result.passed is True


def test_vlans_fail_on_http_error(monkeypatch):
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(401))
    result = verify.check_vlans_up("10.0.0.1", [10], api_key="k", api_secret="s")
    assert result.passed is False
    assert "401" in result.detail


def test_vlans_tolerate_a_null_json_body(monkeypatch):
    monkeypatch.setattr(verify.requests, "get", lambda *a, **k: FakeResponse(200, None))
    result = verify.check_vlans_up("10.0.0.1", [10], api_key="k", api_secret="s")
    assert result.passed is False


# ---------------------------------------------------------------------------
# run_all — the gate itself
# ---------------------------------------------------------------------------

def test_run_all_halts_on_the_first_failure(monkeypatch):
    calls = []

    def fail_api(*a, **k):
        calls.append("api")
        return verify.CheckResult("api_reachable", False, "down")

    def unreachable(*a, **k):
        calls.append("dns")
        return verify.CheckResult("dns_resolving", True, "ok")

    monkeypatch.setattr(verify, "check_api_reachable", fail_api)
    monkeypatch.setattr(verify, "check_dns_resolving", unreachable)

    passed, results = verify.run_all(opnsense_ip="10.0.0.1", expected_vlan_ids=[])
    assert passed is False
    assert calls == ["api"], "later checks must not run once one has failed"
    assert len(results) == 1


def test_run_all_passes_only_when_every_check_passes(monkeypatch):
    ok = lambda name: (lambda *a, **k: verify.CheckResult(name, True, "ok"))  # noqa: E731
    monkeypatch.setattr(verify, "check_api_reachable", ok("api_reachable"))
    monkeypatch.setattr(verify, "check_dns_resolving", ok("dns_resolving"))
    monkeypatch.setattr(verify, "check_internet_egress", ok("seed_egress"))
    monkeypatch.setattr(verify, "check_vlans_up", ok("vlans_up"))

    passed, results = verify.run_all(opnsense_ip="10.0.0.1", expected_vlan_ids=[10])
    assert passed is True
    assert len(results) == 4


def test_check_result_line_renders_pass_and_fail():
    assert "[PASS]" in verify.CheckResult("x", True, "d").line()
    assert "[FAIL]" in verify.CheckResult("x", False, "d").line()


# ---------------------------------------------------------------------------
# main — exit codes are the contract with the caller
# ---------------------------------------------------------------------------

def test_main_returns_zero_when_the_gate_passes(monkeypatch):
    monkeypatch.setattr(verify.sys, "argv", ["verify", "--opnsense-ip", "10.0.0.1"])
    monkeypatch.setattr(verify, "run_all", lambda **kw: (True, []))
    assert verify.main() == 0


def test_main_returns_one_when_the_gate_fails(monkeypatch):
    monkeypatch.setattr(verify.sys, "argv", ["verify", "--opnsense-ip", "10.0.0.1"])
    monkeypatch.setattr(verify, "run_all", lambda **kw: (False, []))
    assert verify.main() == 1


def test_main_rejects_malformed_vlan_list(monkeypatch):
    monkeypatch.setattr(
        verify.sys, "argv",
        ["verify", "--opnsense-ip", "10.0.0.1", "--vlans", "10,not-a-number"],
    )
    assert verify.main() == 2


def test_main_parses_the_vlan_list_and_reads_credentials_from_env(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        verify.sys, "argv",
        ["verify", "--opnsense-ip", "10.0.0.1", "--vlans", " 10 , 20 ,"],
    )
    monkeypatch.setenv("OPNSENSE_API_KEY", "k")
    monkeypatch.setenv("OPNSENSE_API_SECRET", "s")
    monkeypatch.setattr(verify, "run_all", lambda **kw: (seen.update(kw), (True, []))[1])

    verify.main()
    assert seen["expected_vlan_ids"] == [10, 20]
    assert seen["api_key"] == "k"
    assert seen["api_secret"] == "s"
