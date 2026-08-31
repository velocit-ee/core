"""Tests for the OPNsense and Proxmox REST clients.

These two modules are the engines' only path to real infrastructure: they
create VMs, rewrite firewall rules, and carry the API credentials that let
them. They were at zero coverage. The properties pinned here are the ones
whose absence would be a security or correctness incident rather than a bug:

  - TLS verification is on unless explicitly opted out of.
  - A 4xx is never retried (retrying an authentication failure is how you get
    an account locked out, and retrying a malformed write is how you get two).
  - A 5xx or a connection error *is* retried, because the whole point of the
    shared retry policy is that reconfigure endpoints hiccup.
  - Credential material never lands in an exception message.
"""

from __future__ import annotations

import itertools

import pytest
import requests

from shared._retry import TransientAPIError
from shared.renderers._opnsense_client import (
    OPNsenseAPIError,
    OPNsenseClient,
    OPNsenseTransientError,
)
from shared.renderers._proxmox_client import (
    ProxmoxAPIError,
    ProxmoxClient,
    ProxmoxTransientError,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, status_code=200, payload=None, text="", raise_on_json=False):
        self.status_code = status_code
        self._payload = payload
        self.text = text
        self._raise_on_json = raise_on_json

    def json(self):
        if self._raise_on_json:
            raise ValueError("not json")
        return self._payload


class RecordingSession:
    """Stands in for requests.Session, replaying a scripted list of outcomes."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []
        self.auth = None
        self.headers = {}
        self.verify = True

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else self.outcomes_default()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def outcomes_default(self):
        return FakeResponse(200, {})


def opnsense(monkeypatch, outcomes, **kwargs):
    client = OPNsenseClient("10.0.0.1", "key", "secret", **kwargs)
    session = RecordingSession(outcomes)
    client._session = session
    return client, session


def proxmox(monkeypatch, outcomes, **kwargs):
    client = ProxmoxClient("https://10.0.0.2:8006", "root@pam!tok=secret", **kwargs)
    session = RecordingSession(outcomes)
    client._session = session
    return client, session


@pytest.fixture(autouse=True)
def no_backoff_sleep(monkeypatch):
    """Retries use exponential backoff; don't spend real seconds on it."""
    monkeypatch.setattr("tenacity.nap.time.sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def clean_insecure_env(monkeypatch):
    monkeypatch.delenv("OPNSENSE_INSECURE", raising=False)
    monkeypatch.delenv("PROXMOX_VE_INSECURE", raising=False)


# ---------------------------------------------------------------------------
# Construction and TLS policy
# ---------------------------------------------------------------------------

def test_opnsense_defaults_to_https_and_verifies():
    client = OPNsenseClient("10.0.0.1", "key", "secret")
    assert client.base == "https://10.0.0.1/api"
    assert client._session.verify is True


def test_opnsense_respects_an_explicit_scheme():
    assert OPNsenseClient("http://10.0.0.1", "k", "s").base == "http://10.0.0.1/api"


def test_opnsense_strips_a_trailing_slash():
    assert OPNsenseClient("https://10.0.0.1/", "k", "s").base == "https://10.0.0.1/api"


def test_opnsense_insecure_requires_the_exact_env_value(monkeypatch):
    for value in ("0", "", "true", "yes"):
        monkeypatch.setenv("OPNSENSE_INSECURE", value)
        assert OPNsenseClient("10.0.0.1", "k", "s")._session.verify is True
    monkeypatch.setenv("OPNSENSE_INSECURE", "1")
    assert OPNsenseClient("10.0.0.1", "k", "s")._session.verify is False


def test_opnsense_explicit_argument_beats_the_environment(monkeypatch):
    monkeypatch.setenv("OPNSENSE_INSECURE", "1")
    assert OPNsenseClient("10.0.0.1", "k", "s", verify_ssl=True)._session.verify is True


def test_opnsense_sends_basic_auth():
    assert OPNsenseClient("10.0.0.1", "key", "secret")._session.auth == ("key", "secret")


def test_proxmox_defaults_to_https_8006_and_verifies():
    client = ProxmoxClient("10.0.0.2", "root@pam!tok=secret")
    assert client.base == "https://10.0.0.2:8006/api2/json"
    assert client._session.verify is True


def test_proxmox_rejects_a_non_http_endpoint():
    with pytest.raises(ValueError, match="http"):
        ProxmoxClient("ftp://10.0.0.2", "root@pam!tok=secret")


@pytest.mark.parametrize("token", ["nosecret", "root@pam", "root@pamtok=secret", "abc=def"])
def test_proxmox_rejects_a_malformed_token(token):
    # Catching this at construction turns a confusing 401 at deploy time into
    # a clear message before anything has been touched.
    with pytest.raises(ValueError, match="user@realm"):
        ProxmoxClient("https://10.0.0.2:8006", token)


def test_proxmox_accepts_a_well_formed_token():
    client = ProxmoxClient("https://10.0.0.2:8006", "root@pam!tok=secret")
    assert client._session.headers["Authorization"] == "PVEAPIToken=root@pam!tok=secret"


def test_proxmox_insecure_requires_the_exact_env_value(monkeypatch):
    monkeypatch.setenv("PROXMOX_VE_INSECURE", "0")
    assert ProxmoxClient("10.0.0.2", "root@pam!tok=s")._session.verify is True
    monkeypatch.setenv("PROXMOX_VE_INSECURE", "1")
    assert ProxmoxClient("10.0.0.2", "root@pam!tok=s")._session.verify is False


# ---------------------------------------------------------------------------
# Request layer: what is retried and what is not
# ---------------------------------------------------------------------------

def test_opnsense_4xx_raises_immediately_and_is_not_retried(monkeypatch):
    client, session = opnsense(monkeypatch, [FakeResponse(401, text="bad credentials")])
    with pytest.raises(OPNsenseAPIError) as exc:
        client._get("/core/system/status")
    assert "401" in str(exc.value)
    assert len(session.calls) == 1, "a 4xx must never be retried"


def test_opnsense_5xx_is_retried_up_to_three_attempts(monkeypatch):
    client, session = opnsense(monkeypatch, [FakeResponse(503)] * 3)
    with pytest.raises(OPNsenseTransientError):
        client._get("/core/system/status")
    assert len(session.calls) == 3


def test_opnsense_5xx_that_recovers_returns_the_later_response(monkeypatch):
    client, session = opnsense(
        monkeypatch, [FakeResponse(503), FakeResponse(200, {"ok": True})]
    )
    assert client._get("/core/system/status") == {"ok": True}
    assert len(session.calls) == 2


def test_opnsense_network_error_is_retried(monkeypatch):
    client, session = opnsense(
        monkeypatch,
        [requests.ConnectionError("reset"), FakeResponse(200, {"ok": True})],
    )
    assert client._get("/x") == {"ok": True}
    assert len(session.calls) == 2


def test_opnsense_transient_error_is_recognised_by_the_shared_policy():
    assert issubclass(OPNsenseTransientError, TransientAPIError)
    assert issubclass(OPNsenseTransientError, OPNsenseAPIError)
    assert not issubclass(OPNsenseAPIError, TransientAPIError)


def test_opnsense_non_json_body_is_a_fatal_error(monkeypatch):
    client, session = opnsense(monkeypatch, [FakeResponse(200, raise_on_json=True)])
    with pytest.raises(OPNsenseAPIError, match="non-JSON"):
        client._get("/x")
    assert len(session.calls) == 1


def test_opnsense_error_text_is_truncated(monkeypatch):
    client, _ = opnsense(monkeypatch, [FakeResponse(400, text="x" * 5000)])
    with pytest.raises(OPNsenseAPIError) as exc:
        client._get("/x")
    assert len(str(exc.value)) < 700


def test_opnsense_never_leaks_the_api_secret_into_an_error(monkeypatch):
    client, _ = opnsense(monkeypatch, [FakeResponse(403, text="denied")])
    with pytest.raises(OPNsenseAPIError) as exc:
        client._get("/x")
    assert "secret" not in str(exc.value)


def test_proxmox_4xx_raises_immediately(monkeypatch):
    client, session = proxmox(monkeypatch, [FakeResponse(403, text="forbidden")])
    with pytest.raises(ProxmoxAPIError):
        client._request("GET", "/nodes")
    assert len(session.calls) == 1


def test_proxmox_5xx_is_retried(monkeypatch):
    client, session = proxmox(monkeypatch, [FakeResponse(500)] * 3)
    with pytest.raises(ProxmoxTransientError):
        client._request("GET", "/nodes")
    assert len(session.calls) == 3


def test_proxmox_unwraps_the_data_envelope(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(200, {"data": [{"node": "pve"}]})])
    assert client._request("GET", "/nodes") == [{"node": "pve"}]


def test_proxmox_never_leaks_the_token_into_an_error(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(401, text="unauthorized")])
    with pytest.raises(ProxmoxAPIError) as exc:
        client._request("GET", "/nodes")
    assert "secret" not in str(exc.value)


def test_request_applies_the_configured_timeout(monkeypatch):
    # A request with no timeout hangs the whole deploy on a silently dropped
    # connection, with no way for the operator to tell what it is waiting on.
    client, session = opnsense(monkeypatch, [FakeResponse(200, {})], timeout=7.5)
    client._get("/x")
    assert session.calls[0][2]["timeout"] == 7.5


# ---------------------------------------------------------------------------
# OPNsense: higher-level behaviour
# ---------------------------------------------------------------------------

def test_ping_is_true_on_success_and_false_on_api_error(monkeypatch):
    client, _ = opnsense(monkeypatch, [FakeResponse(200, {})])
    assert client.ping() is True

    client, _ = opnsense(monkeypatch, [FakeResponse(401, text="nope")])
    assert client.ping() is False


def test_list_vlans_tolerates_missing_and_null_rows(monkeypatch):
    for payload in ({}, {"rows": None}, None):
        client, _ = opnsense(monkeypatch, [FakeResponse(200, payload)])
        assert client.list_vlans() == []


def test_find_vlan_matches_on_both_tag_and_parent(monkeypatch):
    rows = {"rows": [
        {"uuid": "a", "tag": "10", "if": "vtnet1"},
        {"uuid": "b", "tag": "10", "if": "vtnet2"},
    ]}
    client, _ = opnsense(monkeypatch, [FakeResponse(200, rows)])
    assert client.find_vlan(10, "vtnet2")["uuid"] == "b"

    client, _ = opnsense(monkeypatch, [FakeResponse(200, rows)])
    assert client.find_vlan(10, "vtnet9") is None


def test_upsert_vlan_updates_in_place_when_the_vlan_exists(monkeypatch):
    # Idempotence is the whole promise of these engines: a second run must
    # update the existing VLAN, not create a duplicate.
    existing = {"rows": [{"uuid": "abc", "tag": "10", "if": "vtnet1"}]}
    client, session = opnsense(
        monkeypatch, [FakeResponse(200, existing), FakeResponse(200, {})]
    )
    assert client.upsert_vlan(tag=10, parent="vtnet1", descr="servers") == "abc"
    assert "setItem/abc" in session.calls[1][1]


def test_upsert_vlan_creates_when_absent(monkeypatch):
    client, session = opnsense(
        monkeypatch,
        [FakeResponse(200, {"rows": []}), FakeResponse(200, {"uuid": "new"})],
    )
    assert client.upsert_vlan(tag=20, parent="vtnet1", descr="clients") == "new"
    assert "addItem" in session.calls[1][1]


def test_upsert_vlan_rejects_a_create_response_without_a_uuid(monkeypatch):
    client, _ = opnsense(
        monkeypatch, [FakeResponse(200, {"rows": []}), FakeResponse(200, {"status": "ok"})]
    )
    with pytest.raises(OPNsenseAPIError, match="unexpected addItem response"):
        client.upsert_vlan(tag=20, parent="vtnet1", descr="clients")


def test_wait_until_ready_returns_as_soon_as_ping_succeeds(monkeypatch):
    monkeypatch.setattr("shared.renderers._opnsense_client.time.sleep", lambda _s: None)
    client, _ = opnsense(monkeypatch, [FakeResponse(503), FakeResponse(503), FakeResponse(200, {})])
    client.wait_until_ready(timeout=60, interval=0)


def test_wait_until_ready_raises_once_the_deadline_passes(monkeypatch):
    # `import time` in the module under test binds the real module, so
    # patching monotonic here also drives tenacity's own clock. Use a
    # monotonically advancing fake rather than a fixed list, or tenacity
    # exhausts it mid-backoff.
    monkeypatch.setattr("shared.renderers._opnsense_client.time.sleep", lambda _s: None)
    clock = itertools.count(start=0.0, step=5.0)
    monkeypatch.setattr(
        "shared.renderers._opnsense_client.time.monotonic", lambda: next(clock)
    )
    client = OPNsenseClient("10.0.0.1", "k", "s")
    client._session = RecordingSession([FakeResponse(503)] * 200)
    with pytest.raises(OPNsenseAPIError, match="did not become ready"):
        client.wait_until_ready(timeout=10, interval=0)


# ---------------------------------------------------------------------------
# Proxmox: higher-level behaviour
# ---------------------------------------------------------------------------

def test_first_node_returns_the_only_node(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(200, {"data": [{"node": "pve"}]})])
    assert client.first_node() == "pve"


def test_first_node_raises_when_the_cluster_is_empty(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(200, {"data": []})])
    with pytest.raises(ProxmoxAPIError):
        client.first_node()


def test_vm_exists_is_false_on_a_404(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(404, text="not found")])
    assert client.vm_exists("pve", 100) is False


def test_vm_exists_is_true_when_the_config_comes_back(monkeypatch):
    client, _ = proxmox(monkeypatch, [FakeResponse(200, {"data": {"name": "opnsense"}})])
    assert client.vm_exists("pve", 100) is True
