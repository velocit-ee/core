"""cloud_init — cloud-init only. Stub: not yet implemented (cloud-init isn't
a full provisioner for VNE's scope, but it's reasonable for users to want it
at the seam between VME and VNE for image personalisation)."""
from ..renderer_registry import register
from ._stub import make_stub

CloudInitRenderer = make_stub("cloud-init")
register("cloud-init", CloudInitRenderer)
