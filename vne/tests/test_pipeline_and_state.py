"""Pipeline phase-gating and the resumable renderer state file."""
from __future__ import annotations

import json
import os
import stat

import pytest

from shared.pipeline import Pipeline
from shared.renderer import Renderer
from shared.renderers._state import RenderState, StateError
from shared.schema import ProvisioningResult


# ── pipeline gating ──────────────────────────────────────────────────────────

class _FailInfra(Renderer):
    name = "fail-infra"
    phase = "infra"
    def validate(self): return []
    def execute(self, *, prior_outputs=None):
        return ProvisioningResult(success=False, renderer=self.name, phase="infra", error="boom")


class _ConfigTripwire(Renderer):
    name = "config-after"
    phase = "config"
    def validate(self): return []
    def execute(self, *, prior_outputs=None):
        raise AssertionError("config renderer ran after an infra failure")


class _OkInfra(Renderer):
    name = "ok-infra"
    phase = "infra"
    def validate(self): return []
    def execute(self, *, prior_outputs=None):
        return ProvisioningResult(success=True, renderer=self.name, phase="infra",
                                  outputs={"opnsense_ip": "10.0.0.1"})


def test_infra_failure_gates_config(intent, tmp_path):
    w, s = tmp_path / "w", tmp_path / "s"
    pipe = Pipeline([
        _ConfigTripwire(intent=intent, work_dir=w, state_dir=s),
        _FailInfra(intent=intent, work_dir=w, state_dir=s),
    ])
    result = pipe.run()
    assert not result.success
    skipped = [r for r in result.results if r.renderer == "config-after"][0]
    assert skipped.error.startswith("skipped")


def test_infra_outputs_thread_forward(intent, tmp_path):
    w, s = tmp_path / "w", tmp_path / "s"
    seen = {}

    class _Capture(Renderer):
        name = "capture"
        phase = "config"
        def validate(self): return []
        def execute(self, *, prior_outputs=None):
            seen.update(prior_outputs or {})
            return ProvisioningResult(success=True, renderer=self.name, phase="config")

    pipe = Pipeline([
        _Capture(intent=intent, work_dir=w, state_dir=s),
        _OkInfra(intent=intent, work_dir=w, state_dir=s),
    ])
    result = pipe.run()
    assert result.success
    assert seen.get("opnsense_ip") == "10.0.0.1"  # infra ran first, output threaded in


# ── state file ───────────────────────────────────────────────────────────────

def test_state_resume_skips_completed(tmp_path):
    p = tmp_path / "vne.state.json"
    st = RenderState(p)
    assert not st.is_completed("create_vm")
    st.mark_started("create_vm")
    st.mark_completed("create_vm", {"vmid": 100})
    # Reload from disk — a resume sees the completed step.
    st2 = RenderState(p)
    assert st2.is_completed("create_vm")
    assert st2.step_data("create_vm")["vmid"] == 100


def test_state_shared_values_persist(tmp_path):
    p = tmp_path / "vne.state.json"
    st = RenderState(p)
    st.shared_set("opnsense_api_key", "abc")
    assert RenderState(p).shared_get("opnsense_api_key") == "abc"


def test_corrupt_state_is_fatal_not_silent(tmp_path):
    p = tmp_path / "vne.state.json"
    p.write_text("{not valid json")
    with pytest.raises(StateError):
        RenderState(p)


def test_schema_mismatch_is_fatal(tmp_path):
    p = tmp_path / "vne.state.json"
    p.write_text(json.dumps({"schema_version": "999", "steps": {}, "shared": {}}))
    with pytest.raises(StateError):
        RenderState(p)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_state_file_is_owner_only(tmp_path):
    """State can hold API credentials — it must be written 0600."""
    p = tmp_path / "vne.state.json"
    RenderState(p).mark_completed("x")
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"
