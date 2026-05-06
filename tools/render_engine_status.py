#!/usr/bin/env python3
"""Regenerate marker-delimited status regions in markdown files.

`engines.json` is the canonical source of truth for engine status,
version, phase, and descriptive copy. This script reads it, validates
it against `tools/engines_schema.json`, then walks a list of target
markdown files and rewrites the content between paired markers like:

    <!-- ENGINE-STATUS:BEGIN region=engine-table -->
    ...rendered content goes here...
    <!-- ENGINE-STATUS:END region=engine-table -->

A region is identified by its `region=<name>` attribute. Each region
maps to a render function (see RENDERERS at the bottom of this file).
Adding a new region = write the function, add it to RENDERERS, drop
the marker pair into the target file. No glue code in between.

## Modes

  --check   Compare what would be rendered against what's on disk.
            Exits 1 with a diff if any target has drifted. Used by CI.

  --write   Rewrite targets in place. Used by pre-commit hooks and
            local development. Idempotent — re-running on a clean
            tree produces no changes.

  (default) --check.

## Why marker pairs and not Jinja templates

Targets are checked into the repo and viewed on GitHub, where Jinja
isn't an option. We need pre-rendered markdown that any reader sees
identically. Marker pairs let us regenerate just the dynamic regions
while leaving the surrounding hand-written prose alone — so the
README still reads like a hand-written README.

The CI gate is what makes this trustworthy: if you edit `engines.json`
and forget to run the renderer, CI fails on your PR.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import jsonschema


# ---------------------------------------------------------------------------
# Configuration: which files have which regions
#
# Paths are relative to the repo root. The same script handles both
# velocit-ee/core and velocit-ee/.github by passing --root.
# ---------------------------------------------------------------------------

# (target_relpath, region_name)
TARGETS_CORE: list[tuple[str, str]] = [
    ("README.md",          "engine-table"),
    ("CHANGELOG.md",       "engine-table-unreleased"),
    ("vme/README.md",      "engine-pill-vme"),
    ("vne/README.md",      "engine-pill-vne"),
]

TARGETS_DOTGITHUB: list[tuple[str, str]] = [
    ("profile/README.md", "engine-pill-vme"),
    ("profile/README.md", "engine-pill-vne"),
    ("profile/README.md", "engine-pill-vse"),
    ("profile/README.md", "engine-pill-vle"),
    ("profile/README.md", "trailing-status"),
]

# Pattern for marker pairs. Captures the whole block (markers + body)
# so we can replace it wholesale.
_MARKER = re.compile(
    r"(<!--\s*ENGINE-STATUS:BEGIN\s+region=(?P<name>[a-z0-9-]+)\s*-->\n?)"
    r"(?P<body>.*?)"
    r"(<!--\s*ENGINE-STATUS:END\s+region=(?P=name)\s*-->)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

def load_engines(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"engines.json not found at {path}")
    data = json.loads(path.read_text())
    schema_path = path.parent / "tools" / "engines_schema.json"
    if not schema_path.exists():
        # When called from .github/, the schema lives there too.
        schema_path = path.parent / "tools" / "engines_schema.json"
    schema = json.loads(schema_path.read_text())
    jsonschema.Draft7Validator.check_schema(schema)
    errors = sorted(
        jsonschema.Draft7Validator(schema).iter_errors(data),
        key=lambda e: list(e.path),
    )
    if errors:
        msg = "\n".join(
            f"  - {'.'.join(str(p) for p in e.path) or '(root)'}: {e.message}"
            for e in errors
        )
        raise SystemExit(f"engines.json failed schema validation:\n{msg}")
    return data


def engine_by_slug(data: dict, slug: str) -> dict:
    for e in data["engines"]:
        if e["slug"] == slug:
            return e
    raise SystemExit(f"engines.json missing entry for slug '{slug}'")


# ---------------------------------------------------------------------------
# Renderers — one per region name
#
# Each takes the validated `engines.json` dict + the region's optional
# attribute keyword args (parsed from the marker line) and returns the
# string that should sit between the BEGIN and END markers — including a
# trailing newline to keep the markers on their own lines.
# ---------------------------------------------------------------------------

def _slug_cell(engine: dict) -> str:
    """Slug cell with bold for Stable engines, padded to 7 chars (== width of '**VME**')."""
    slug = engine["slug"].upper()
    cell = f"**{slug}**" if engine["status"] == "Stable" else slug
    return cell.ljust(7)


def _render_engine_table(data: dict) -> str:
    """Markdown table: Engine | Phase | Status | Description.

    Used in the core README. Bold slugs are Stable; un-bolded are Planned.
    Column widths chosen so the raw markdown reads cleanly even before
    GitHub's renderer collapses whitespace.
    """
    rows = ["| Engine  | Phase | Status  | Description |",
            "|---------|:-----:|---------|-------------|"]
    for e in data["engines"]:
        rows.append(
            f"| {_slug_cell(e)} | "
            f"{e['phase']}     | "
            f"{e['status']:<7} | "
            f"{e['short']} |"
        )
    return "\n".join(rows) + "\n"


def _render_engine_table_compact(data: dict) -> str:
    """Compact variant for the CHANGELOG: Engine | Phase | Status only."""
    rows = ["| Engine  | Phase | Status  |",
            "|---------|:-----:|---------|"]
    for e in data["engines"]:
        rows.append(
            f"| {_slug_cell(e)} | "
            f"{e['phase']}     | "
            f"{e['status']:<7} |"
        )
    return "\n".join(rows) + "\n"


def _render_engine_pill(data: dict, *, slug: str) -> str:
    """One-line pill at the top of an engine README: '**Phase 1 · Stable**'."""
    e = engine_by_slug(data, slug)
    return f"**Phase {e['phase']} · {e['status']}**\n"


def _render_trailing_status(data: dict) -> str:
    """Bottom-of-page status block in the org profile README:

        license:  Apache 2.0 (engines)
        status:   VME stable · VNE stable · VSE planned · VLE planned
    """
    parts = []
    for e in data["engines"]:
        parts.append(f"{e['slug'].upper()} {e['status'].lower()}")
    return (
        "```\n"
        "license:  Apache 2.0 (engines)\n"
        f"status:   {' · '.join(parts)}\n"
        "```\n"
    )


# Region name → callable. The callable signature is
#   (data: dict, **kwargs) -> str
# where kwargs come from key=value attributes on the BEGIN marker.
RENDERERS: dict[str, Callable[..., str]] = {
    "engine-table":             lambda d: _render_engine_table(d),
    "engine-table-unreleased":  lambda d: _render_engine_table_compact(d),
    "engine-pill-vme":          lambda d: _render_engine_pill(d, slug="vme"),
    "engine-pill-vne":          lambda d: _render_engine_pill(d, slug="vne"),
    "engine-pill-vse":          lambda d: _render_engine_pill(d, slug="vse"),
    "engine-pill-vle":          lambda d: _render_engine_pill(d, slug="vle"),
    "trailing-status":          lambda d: _render_trailing_status(d),
}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def render_file(path: Path, data: dict) -> tuple[str, list[str]]:
    """Return (new_content, list_of_regions_changed) for `path`.

    Walks every BEGIN/END marker pair in the file, dispatches to the
    matching renderer, and substitutes the rendered body between the
    markers. Regions whose name doesn't appear in RENDERERS are an error.
    """
    text = path.read_text()
    found: list[str] = []
    unknown: list[str] = []

    def _replace(match: re.Match) -> str:
        name = match.group("name")
        found.append(name)
        renderer = RENDERERS.get(name)
        if renderer is None:
            unknown.append(name)
            return match.group(0)
        body = renderer(data)
        if not body.endswith("\n"):
            body += "\n"
        return f"{match.group(1)}{body}{match.group(4)}"

    new_text = _MARKER.sub(_replace, text)
    if unknown:
        raise SystemExit(
            f"{path}: unknown region(s): {', '.join(sorted(set(unknown)))}\n"
            f"Add a renderer in tools/render_engine_status.py:RENDERERS."
        )
    return new_text, found


def diff(old: str, new: str, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            old.splitlines(keepends=True),
            new.splitlines(keepends=True),
            fromfile=f"{label} (on disk)",
            tofile=f"{label} (rendered)",
            n=2,
        )
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Regenerate engine-status regions from engines.json.",
    )
    p.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Repo root containing engines.json (defaults to this script's parent).",
    )
    p.add_argument(
        "--targets",
        choices=("core", "dotgithub", "auto"),
        default="auto",
        help=(
            "Which target list to apply. 'auto' detects: if --root contains "
            "profile/README.md it picks dotgithub, otherwise core."
        ),
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Exit non-zero on drift; no writes.")
    mode.add_argument("--write", action="store_true", help="Rewrite drifted files in place.")
    args = p.parse_args(argv)

    if not args.check and not args.write:
        args.check = True  # default

    engines_path = args.root / "engines.json"
    data = load_engines(engines_path)

    targets_choice = args.targets
    if targets_choice == "auto":
        if (args.root / "profile" / "README.md").exists():
            targets_choice = "dotgithub"
        else:
            targets_choice = "core"
    targets = TARGETS_CORE if targets_choice == "core" else TARGETS_DOTGITHUB

    drifted: list[str] = []
    for relpath, _region in targets:
        path = args.root / relpath
        if not path.exists():
            print(f"warning: target missing: {path}", file=sys.stderr)
            continue
        old = path.read_text()
        new, _found = render_file(path, data)
        if new == old:
            continue
        if args.write:
            path.write_text(new)
            print(f"rewrote: {relpath}")
        else:
            drifted.append(relpath)
            print(diff(old, new, relpath), end="")

    if args.check and drifted:
        print(
            f"\nERROR: {len(drifted)} file(s) drifted from engines.json:\n  - "
            + "\n  - ".join(drifted)
            + "\n\nFix with:  python tools/render_engine_status.py --write",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
