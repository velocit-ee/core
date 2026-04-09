"""Handoff manifest — written by each engine, passed to the next."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jsonschema

_SCHEMA_PATH = Path(__file__).parent.parent / "manifests" / "schema.json"

VME_VERSION = "0.1.0"


def _load_schema() -> dict[str, Any]:
    with open(_SCHEMA_PATH) as fh:
        return json.load(fh)


def validate(manifest: dict[str, Any]) -> list[str]:
    """Return a list of validation error strings. Empty = valid."""
    schema = _load_schema()
    validator = jsonschema.Draft7Validator(schema)
    errors = sorted(validator.iter_errors(manifest), key=lambda e: list(e.path))
    return [f"{'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}" for e in errors]


def build_vme(
    *,
    cfg: dict[str, Any],
    iso_path: Path,
    mac: str | None,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    """Build a manifest from a completed VME deployment."""
    target = cfg.get("target", {})
    os_name = target.get("os", "")
    duration = (completed_at - started_at).total_seconds()

    username = "root" if os_name == "proxmox-ve" else "ubuntu"

    manifest: dict[str, Any] = {
        "schema_version": "1.0",
        "target": {
            "hostname": target.get("hostname", ""),
            "ip":       target.get("ip", ""),
            "prefix":   int(target.get("prefix", 24)),
            "gateway":  target.get("gateway", ""),
            "dns":      target.get("dns", ""),
            "disk":     target.get("disk", ""),
            "os":       os_name,
        },
        "access": {
            "username":       username,
            "ssh_public_key": target.get("ssh_public_key", ""),
            "ssh_port":       22,
        },
        "engines": {
            "vme": {
                "status":           "success",
                "version":          VME_VERSION,
                "started_at":       started_at.isoformat(),
                "completed_at":     completed_at.isoformat(),
                "duration_seconds": round(duration, 1),
                "iso":              iso_path.name,
            }
        },
    }

    if mac:
        manifest["target"]["mac"] = mac

    errors = validate(manifest)
    if errors:
        raise ValueError("Manifest validation failed:\n" + "\n".join(errors))

    return manifest


def write(manifest: dict[str, Any], output_dir: Path) -> Path:
    """Persist manifest to output_dir/<hostname>-<timestamp>.json."""
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Sanitize hostname: allow only alphanumerics and hyphens to prevent
    # path traversal or invalid filenames.
    hostname = re.sub(r"[^a-zA-Z0-9-]", "_", manifest["target"]["hostname"])
    filename = f"{hostname}-{ts}.json"
    out_path = output_dir / filename
    with open(out_path, "w") as fh:
        json.dump(manifest, fh, indent=2)
    return out_path


def load(path: Path) -> dict[str, Any]:
    """Load and validate a manifest from disk."""
    with open(path) as fh:
        manifest = json.load(fh)
    errors = validate(manifest)
    if errors:
        raise ValueError(f"Manifest at '{path}' failed validation:\n" + "\n".join(errors))
    return manifest
