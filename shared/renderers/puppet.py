"""puppet — Puppet. Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

PuppetRenderer = make_stub("puppet")
register("puppet", PuppetRenderer)
