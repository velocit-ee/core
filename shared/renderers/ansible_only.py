"""ansible_only — Ansible without OpenTofu. Stub: not yet implemented.

Why this is a separate provisioner: pairing OpenTofu with Ansible has a hard
phase split that doesn't apply to Ansible-only flows (Ansible would have to
do VM creation via the proxmox modules, which has very different idempotency
properties from Terraform-managed infra).
"""
from ..renderer_registry import register
from ._stub import make_stub

AnsibleOnlyRenderer = make_stub("ansible-only")
register("ansible-only", AnsibleOnlyRenderer)
