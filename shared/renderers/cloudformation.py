"""cloudformation — AWS CloudFormation. Stub: not yet implemented (and unlikely
to be relevant for VNE specifically since OPNsense lives on Proxmox)."""
from ..renderer_registry import register
from ._stub import make_stub

CloudFormationRenderer = make_stub("cloudformation")
register("cloudformation", CloudFormationRenderer)
