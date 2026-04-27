"""helm — Helm. Stub: not yet implemented (Helm is a poor fit for VNE
specifically — it's likely to land in VSE first)."""
from ..renderer_registry import register
from ._stub import make_stub

HelmRenderer = make_stub("helm")
register("helm", HelmRenderer)
