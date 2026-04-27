"""pulumi — Pulumi (Python or TypeScript). Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

PulumiRenderer = make_stub("pulumi")
register("pulumi", PulumiRenderer)
