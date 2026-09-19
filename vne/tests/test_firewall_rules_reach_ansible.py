"""Declared firewall allow-rules must survive the OpenTofu → Ansible handoff.

N-04: the OpenTofu renderer wrote every other part of the intent into
infra_manifest.json but not the allow rules, and the Ansible renderer wrote
group_vars without an `allow_rules` key. roles/opnsense-firewall loops over
`allow_rules | default([])`, so the loop was always empty: a deployment with
declared allow rules applied the default-block policy and none of the allows.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from shared.renderers.ansible import AnsibleRenderer
from shared.renderers.opentofu import OpenTofuRenderer


def _infra_manifest(intent, tmp_path: Path) -> dict:
    r = OpenTofuRenderer(intent=intent, work_dir=tmp_path / "w", state_dir=tmp_path / "s")
    return json.loads(r._write_infra_manifest({"opnsense_ip": "10.10.10.1"}).read_text())


def test_infra_manifest_carries_the_declared_allow_rules(intent, tmp_path):
    payload = _infra_manifest(intent, tmp_path)
    rules = payload["intent"]["firewall_allow_rules"]
    assert [r["description"] for r in rules] == ["mgmt to any"]
    assert rules[0]["src_vlan"] == 10
    assert rules[0]["action"] == "allow"


def test_group_vars_expose_them_under_the_key_the_role_reads(intent, tmp_path):
    payload = _infra_manifest(intent, tmp_path)
    r = AnsibleRenderer(intent=intent, work_dir=tmp_path / "a", state_dir=tmp_path / "s")
    r._render_group_vars(payload)
    gv = yaml.safe_load((r._ansible_dir() / "group_vars" / "opnsense.yml").read_text())
    assert gv["allow_rules"], "roles/opnsense-firewall loops over allow_rules"
    assert gv["allow_rules"][0]["description"] == "mgmt to any"
    assert gv["firewall_default_policy"] == "block"


def test_group_vars_tolerate_an_intent_with_no_allow_rules(intent, tmp_path):
    r = AnsibleRenderer(intent=intent, work_dir=tmp_path / "a", state_dir=tmp_path / "s")
    r._render_group_vars({"outputs": {"opnsense_ip": "10.10.10.1"}, "intent": {}})
    gv = yaml.safe_load((r._ansible_dir() / "group_vars" / "opnsense.yml").read_text())
    assert gv["allow_rules"] == []
