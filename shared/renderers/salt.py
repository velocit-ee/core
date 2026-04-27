"""salt — SaltStack. Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

SaltRenderer = make_stub("salt")
register("salt", SaltRenderer)
