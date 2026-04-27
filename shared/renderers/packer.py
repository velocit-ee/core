"""packer — HashiCorp Packer. Stub: not yet implemented (Packer's domain is
image building, not network configuration, but bundling it for users who
want a custom OPNsense image is plausible future work)."""
from ..renderer_registry import register
from ._stub import make_stub

PackerRenderer = make_stub("packer")
register("packer", PackerRenderer)
