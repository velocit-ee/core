"""Handoff manifest generation and validation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent.parent / "manifests" / "schema.json"

VME_VERSION = "0.1.0"


def _load_schema() -> dict[str, Any]:
    """Load the manifest JSON Schema from disk."""
    with open(_SCHEMA_PATH) as fh:
        return json.load(fh)


def validate(manifest: dict[str, Any]) -> list[str]:
    """Validate a manifest dict against the schema.

    Returns a list of validation error messages. An empty list means the
    manifest is valid.
    """
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]


def build(
    *,
    hostname: str,
    ip: str,
    os: str,
    os_version: str,
    hardware: dict[str, Any],
    ssh_host_key_fingerprint: str,
    status: str = "success",
    failure_reason: str | None = None,
) -> dict[str, Any]:
    """Construct a manifest dict from provisioning results.

    Raises ValueError if the produced manifest fails schema validation.
    """
    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "vme_version": VME_VERSION,
        "status": status,
        "hostname": hostname,
        "ip": ip,
        "os": os,
        "os_version": os_version,
        "hardware": hardware,
        "ssh_host_key_fingerprint": ssh_host_key_fingerprint,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    if failure_reason is not None:
        manifest["failure_reason"] = failure_reason

    errors = validate(manifest)
    if errors:
        raise ValueError("Produced manifest failed schema validation:\n" + "\n".join(errors))

    return manifest


def write(manifest: dict[str, Any], output_dir: Path) -> Path:
    """Persist a manifest to output_dir/<hostname>-<timestamp>.json.

    Creates output_dir if it does not exist. Returns the written path.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"{manifest['hostname']}-{ts}.json"
    out_path = output_dir / filename
    with open(out_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    return out_path


def load(path: Path) -> dict[str, Any]:
    """Load and validate a manifest from disk.

    Raises ValueError with a clear message if validation fails.
    """
    with open(path) as fh:
        manifest = json.load(fh)
    errors = validate(manifest)
    if errors:
        raise ValueError(f"Manifest at '{path}' failed schema validation:\n" + "\n".join(errors))
    return manifest
