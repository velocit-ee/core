"""chef — Chef Infra. Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

ChefRenderer = make_stub("chef")
register("chef", ChefRenderer)
