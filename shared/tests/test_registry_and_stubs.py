"""Tests for the provisioner registry and the stub renderers.

The registry is what turns a string in velocitee.yml into executable code, and
the stub renderers are the reason that string can name a backend that does not
exist. That combination is exactly the honesty problem the project README now
calls out: `provisioner: pulumi` validates cleanly and then refuses to run.

These tests pin that behaviour so it stays deliberate — a stub must fail
loudly at execute() and must never be mistaken for a working backend.
"""

from __future__ import annotations

import pytest

from shared import renderer_registry
from shared.renderer_registry import available, is_registered, lookup, register
from shared.renderers._stub import make_stub

# Backends that actually do something.
IMPLEMENTED = {"velocitee-native", "opentofu", "ansible", "opentofu+ansible"}

# Registered names that validate and then refuse. Reserved slots, not options.
STUBS = {
    "ansible-only", "pulumi", "salt", "chef", "puppet",
    "cloudformation", "bicep", "nix", "cloud-init", "helm", "packer",
}


@pytest.fixture(autouse=True)
def loaded_registry():
    renderer_registry.ensure_loaded()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_every_documented_backend_is_registered():
    names = set(available())
    missing = (IMPLEMENTED | STUBS) - names
    assert not missing, f"registry is missing {sorted(missing)}"


def test_lookup_returns_renderer_classes():
    assert all(isinstance(r, type) for r in lookup("velocitee-native"))


def test_lookup_raises_a_helpful_error_for_an_unknown_name():
    with pytest.raises(KeyError) as exc:
        lookup("terraform")
    # The message must list the alternatives; a bare KeyError leaves the
    # operator guessing at a name they typed into a YAML file.
    assert "Available" in str(exc.value)
    assert "velocitee-native" in str(exc.value)


def test_composite_backend_runs_two_renderers_in_order():
    renderers = lookup("opentofu+ansible")
    assert len(renderers) == 2
    assert [r.name for r in renderers] == ["opentofu", "ansible"]


def test_is_registered_agrees_with_available():
    for name in available():
        assert is_registered(name)
    assert not is_registered("does-not-exist")


def test_register_rejects_a_duplicate_name():
    with pytest.raises(ValueError, match="already registered"):
        register("velocitee-native", make_stub("velocitee-native"))


def test_register_rejects_an_empty_renderer_list():
    with pytest.raises(ValueError, match="at least one renderer"):
        register("brand-new-backend")


def test_ensure_loaded_is_idempotent():
    before = available()
    renderer_registry.ensure_loaded()
    renderer_registry.ensure_loaded()
    assert available() == before


def test_available_is_sorted():
    assert available() == sorted(available())


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", sorted(STUBS))
def test_stub_validates_clean(name, tmp_path):
    # This is the trap: validation passing is what lets a stub reach execute().
    # It is intentional (deploy.py can list the backend), but it means the
    # config-time check gives no warning at all.
    cls = lookup(name)[0]
    stub = cls.__new__(cls)
    assert stub.validate() == []


@pytest.mark.parametrize("name", sorted(STUBS))
def test_stub_refuses_to_execute_and_names_itself(name):
    cls = lookup(name)[0]
    stub = cls.__new__(cls)
    result = stub.execute()
    assert result.success is False
    assert result.renderer == cls.name
    assert "not " in result.error and "implemented" in result.error
    assert cls.name in result.error


@pytest.mark.parametrize("name", sorted(STUBS))
def test_stub_error_tells_the_operator_what_to_do(name):
    cls = lookup(name)[0]
    result = cls.__new__(cls).execute()
    assert "Pick a different velocitee.provisioner" in result.error


def test_stub_returns_a_result_rather_than_raising():
    # The pipeline collects ProvisioningResults; a raised NotImplementedError
    # would escape it and lose the per-renderer reporting.
    result = make_stub("example").__new__(make_stub("example")).execute()
    assert result.success is False


def test_make_stub_appends_a_follow_up_when_given_one():
    cls = make_stub("someday", follow_up="tracked in #42")
    result = cls.__new__(cls).execute()
    assert "tracked in #42" in result.error


def test_make_stub_names_the_generated_class_after_the_backend():
    assert make_stub("cloud-init").__name__ == "Cloud_InitStubRenderer"


def test_stub_count_matches_what_the_readme_claims():
    # core/README.md tells readers that two backends work and the rest are
    # reserved slots. If someone implements one, this test fails and the
    # README gets updated in the same change.
    registered_stubs = {
        name for name in available()
        if lookup(name)[0].__name__.endswith("StubRenderer")
    }
    assert registered_stubs == STUBS
