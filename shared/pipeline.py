"""Pipeline orchestrator — runs renderers in dependency order.

Two ordering rules, both enforced here (not by renderers):

  1. Phase order is absolute: every 'infra' renderer runs before any 'config'
     renderer. Renderers tagged 'both' run in their own slot.

  2. Failure gating: if any 'infra' renderer fails, no 'config' renderer runs.
     Verification is the user's responsibility — the pipeline only guarantees
     each renderer ran in the right order with the right inputs.

Renderers within the same phase run sequentially in registration order. We
don't run them in parallel: a single deployment touches one Proxmox host and
one OPNsense VM, and the orchestration cost of parallelism isn't worth the
small wall-clock saving.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .renderer import Renderer
from .schema import ProvisioningResult

log = logging.getLogger("velocitee.pipeline")


@dataclass
class PipelineResult:
    success: bool
    results: list[ProvisioningResult] = field(default_factory=list)
    aggregated_outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def failure_reason(self) -> str | None:
        for r in self.results:
            if not r.success:
                return f"{r.renderer} ({r.phase}): {r.error or 'unknown error'}"
        return None


class Pipeline:
    """Sequential, phase-ordered runner with hard failure gating between phases."""

    def __init__(self, renderers: list[Renderer]):
        if not renderers:
            raise ValueError("Pipeline requires at least one renderer")
        self.renderers = renderers

    @staticmethod
    def _phase_order(r: Renderer) -> int:
        # 'both' renderers run before pure 'config' renderers; they can complete
        # the infra step themselves and then continue into config without an
        # intermediate handoff. 'infra' is first, 'both' second, 'config' third.
        return {"infra": 0, "both": 1, "config": 2}[r.get_execution_phase()]

    def run(self) -> PipelineResult:
        ordered = sorted(self.renderers, key=self._phase_order)
        log.info("pipeline: %d renderer(s) — order: %s",
                 len(ordered), [r.name for r in ordered])

        result = PipelineResult(success=True)
        infra_failed = False

        for renderer in ordered:
            phase = renderer.get_execution_phase()

            if phase == "config" and infra_failed:
                # Hard gate — config never runs after infra failure.
                log.error(
                    "pipeline: skipping %s (config phase) — earlier infra phase failed",
                    renderer.name,
                )
                result.results.append(ProvisioningResult(
                    success=False,
                    renderer=renderer.name,
                    phase="config",
                    error="skipped: prior infra phase failed",
                ))
                result.success = False
                continue

            log.info("pipeline: running %s (%s)", renderer.name, phase)
            r = renderer.render(prior_outputs=dict(result.aggregated_outputs))
            result.results.append(r)

            if r.outputs:
                result.aggregated_outputs.update(r.outputs)

            if not r.success:
                log.error("pipeline: %s failed: %s", renderer.name, r.error)
                result.success = False
                if phase in ("infra", "both"):
                    infra_failed = True
                # Continue to record skips for downstream config renderers.

        return result
