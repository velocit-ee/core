"""Tests for the VME backend registry + MAAS helpers.

We don't drive a real MAAS deployment here — the tests exercise:

  - backend registry: lookup, available, registration of builtin + maas
  - MAAS OAuth header construction
  - MAAS distro_series mapping for the slugs VME hands out
  - MAASClient base URL canonicalisation

The end-to-end deploy path is left to manual verification because CI
won't have a MAAS region+rack to talk to and faking one is more code
than the integration justifies.
"""

from __future__ import annotations

import pytest

from vme import backends as vme_backends
from vme.backends.maas import (
    MAASAPIError,
    MAASBackend,
    MAASClient,
    _MAASOAuth,
    _maas_distro_series,
)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_has_both_backends() -> None:
    assert "builtin" in vme_backends.available()
    assert "maas" in vme_backends.available()


def test_lookup_returns_class() -> None:
    cls = vme_backends.lookup("maas")
    assert cls is MAASBackend


def test_lookup_unknown_raises() -> None:
    with pytest.raises(KeyError):
        vme_backends.lookup("nonsense")


# ---------------------------------------------------------------------------
# MAAS distro_series mapping
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("slug", "expected"),
    [
        ("ubuntu-22.04", "jammy"),
        ("ubuntu-24.04", "noble"),
        ("ubuntu-server-22.04", "jammy"),
        ("ubuntu-server-24.04", "noble"),
        # Unknown slug passes through unchanged so users with custom
        # MAAS images can keep their config working.
        ("my-custom-image", "my-custom-image"),
        ("", ""),
    ],
)
def test_maas_distro_series_mapping(slug: str, expected: str) -> None:
    assert _maas_distro_series(slug) == expected


# ---------------------------------------------------------------------------
# OAuth header
# ---------------------------------------------------------------------------

class _FakeRequest:
    def __init__(self) -> None:
        self.headers: dict[str, str] = {}


def test_oauth_signs_with_plaintext_signature() -> None:
    auth = _MAASOAuth("CKEY", "TKEY", "TSECRET")
    req = _FakeRequest()
    auth(req)
    header = req.headers["Authorization"]
    assert header.startswith("OAuth ")
    assert 'oauth_consumer_key="CKEY"' in header
    assert 'oauth_token="TKEY"' in header
    assert 'oauth_signature="&TSECRET"' in header
    assert 'oauth_signature_method="PLAINTEXT"' in header
    assert 'oauth_nonce=' in header
    assert 'oauth_timestamp=' in header


# ---------------------------------------------------------------------------
# MAASClient URL canonicalisation
# ---------------------------------------------------------------------------

def test_client_normalises_bare_host() -> None:
    client = MAASClient("maas.lab.local", "C:T:S")
    assert client.base == "https://maas.lab.local/MAAS/api/2.0"


def test_client_normalises_url_with_scheme() -> None:
    client = MAASClient("http://maas.lab.local:5240", "C:T:S")
    assert client.base == "http://maas.lab.local:5240/MAAS/api/2.0"


def test_client_keeps_full_path() -> None:
    client = MAASClient("https://maas.lab.local/MAAS/api/2.0", "C:T:S")
    assert client.base == "https://maas.lab.local/MAAS/api/2.0"


def test_client_rejects_malformed_api_key() -> None:
    with pytest.raises(MAASAPIError):
        MAASClient("https://maas.lab.local", "bad-key-no-colons")
