"""bicep — Azure Bicep. Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

BicepRenderer = make_stub("bicep")
register("bicep", BicepRenderer)
