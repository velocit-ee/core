"""Versioned, resumable state file for the velocitee-native renderer.

Every renderer step records its outcome here. On resume, the renderer reads
this file, sees what's already done, and skips ahead — interrupted runs
continue from the last successful step instead of starting over.

Schema versioning is paranoid by design: if a state file from an older VNE
version is loaded by a newer one, we want a loud, clean error, not a silent
attempt at "resuming" something that drifted out of shape.

Corruption handling: any JSON parse failure or schema mismatch is fatal. Never
silently overwrite a state file we couldn't read — manual recovery is the
right escape hatch when state is on fire.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

STATE_SCHEMA_VERSION = "1"


class StateError(Exception):
    """Raised when state on disk is unreadable or schema-incompatible."""


class RenderState:
    """File-backed step ledger.

    Steps are identified by string keys. Each key carries:
      - status: 'pending' | 'in_progress' | 'completed' | 'failed'
      - completed_at: ISO timestamp when it finished
      - data: arbitrary renderer-specific dict (VM IDs, IPs, API creds, …)

    `with state.step("create_vm") as ctx:` records start, runs the body, and
    records completion (or failure) on exit. Resuming skips completed steps.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        self._payload: dict[str, Any] = self._load_or_init()

    # -----------------------------------------------------------------
    # Persistence
    # -----------------------------------------------------------------

    def _load_or_init(self) -> dict[str, Any]:
        if not self.path.exists():
            return {
                "schema_version": STATE_SCHEMA_VERSION,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "steps": {},
                "shared": {},
            }

        try:
            with open(self.path) as fh:
                payload = json.load(fh)
        except json.JSONDecodeError as exc:
            raise StateError(
                f"state file is corrupt: {self.path}\n"
                f"  parse error: {exc}\n"
                f"  to recover: inspect the file, then delete it to start fresh "
                f"(idempotent steps will safely re-run)"
            ) from exc

        sv = payload.get("schema_version")
        if sv != STATE_SCHEMA_VERSION:
            raise StateError(
                f"state file schema mismatch at {self.path}: "
                f"file is '{sv}', VNE expects '{STATE_SCHEMA_VERSION}'.\n"
                f"  delete the file to start fresh, or downgrade VNE to a compatible version"
            )

        for key in ("steps", "shared"):
            if key not in payload or not isinstance(payload[key], dict):
                raise StateError(f"state file is missing or malformed key '{key}': {self.path}")

        return payload

    def _atomic_write(self) -> None:
        # Atomic write — survive a kill between fsync calls without leaving
        # a half-written state file.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(self._payload, fh, indent=2, sort_keys=True)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, self.path)
        except Exception:
            # Best-effort cleanup, don't shadow the real exception.
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # -----------------------------------------------------------------
    # Step API
    # -----------------------------------------------------------------

    def is_completed(self, key: str) -> bool:
        return self._payload["steps"].get(key, {}).get("status") == "completed"

    def step_data(self, key: str) -> dict[str, Any]:
        return dict(self._payload["steps"].get(key, {}).get("data") or {})

    def mark_started(self, key: str) -> None:
        self._payload["steps"][key] = {
            "status": "in_progress",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        self._atomic_write()

    def mark_completed(self, key: str, data: dict[str, Any] | None = None) -> None:
        existing = self._payload["steps"].get(key, {})
        existing.update({
            "status": "completed",
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        })
        self._payload["steps"][key] = existing
        self._atomic_write()

    def mark_failed(self, key: str, reason: str) -> None:
        existing = self._payload["steps"].get(key, {})
        existing.update({
            "status": "failed",
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
        })
        self._payload["steps"][key] = existing
        self._atomic_write()

    # -----------------------------------------------------------------
    # Cross-step shared values (e.g. API credentials reused across steps)
    # -----------------------------------------------------------------

    def shared_get(self, key: str, default: Any = None) -> Any:
        return self._payload["shared"].get(key, default)

    def shared_set(self, key: str, value: Any) -> None:
        self._payload["shared"][key] = value
        self._atomic_write()

    # -----------------------------------------------------------------
    # Iteration / inspection
    # -----------------------------------------------------------------

    def completed_steps(self) -> Iterator[str]:
        for k, v in self._payload["steps"].items():
            if v.get("status") == "completed":
                yield k
