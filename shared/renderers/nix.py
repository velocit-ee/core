"""nix — Nix / NixOS. Stub: not yet implemented."""
from ..renderer_registry import register
from ._stub import make_stub

NixRenderer = make_stub("nix")
register("nix", NixRenderer)
