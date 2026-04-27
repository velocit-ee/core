"""Helper for stub renderers — backends registered for completeness but not yet
implemented. validate() returns no errors so config-time checks pass; execute()
raises NotImplementedError with a clear message naming the provisioner."""

from __future__ import annotations

import logging
from typing import Any

from ..renderer import Renderer
from ..schema import ProvisioningResult

log = logging.getLogger("velocitee.renderer.stub")


def make_stub(provisioner_name: str, *, follow_up: str = "") -> type[Renderer]:
    """Return a Renderer class that politely refuses to run.

    follow_up: extra message tail (e.g. tracking issue number).
    """

    class _Stub(Renderer):
        name = provisioner_name
        phase = "both"

        def validate(self) -> list[str]:
            # No errors — the registry pretends this provisioner is selectable
            # so deploy.py can list it. We fail at execute() instead.
            return []

        def execute(self, *, prior_outputs: dict[str, Any] | None = None) -> ProvisioningResult:
            tail = f" — {follow_up}" if follow_up else ""
            log.error("renderer '%s' invoked but not yet implemented%s",
                      provisioner_name, tail)
            return ProvisioningResult(
                success=False,
                renderer=provisioner_name,
                phase="both",
                error=(
                    f"the '{provisioner_name}' provisioner is registered but not "
                    f"yet implemented{tail}. Pick a different velocitee.provisioner "
                    f"or contribute an implementation."
                ),
            )

    _Stub.__name__ = f"{provisioner_name.replace('-', '_').title()}StubRenderer"
    return _Stub
