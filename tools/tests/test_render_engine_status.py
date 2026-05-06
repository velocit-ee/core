"""Unit tests for the engine-status renderer.

Covers:
  - schema validation against engines.json
  - each render function produces stable output for known input
  - render_file substitution: unchanged surrounding text, replaced body
  - --check mode exits 1 on drift, 0 on clean
  - --write mode rewrites and is idempotent on second run
  - unknown region names error loudly (typo defence)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import render_engine_status as r


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE = {
    "schema_version": "1.0",
    "shared": {"library": "velocitee-shared", "version": "0.4.0"},
    "engines": [
        {
            "slug": "vme", "name": "vme engine", "phase": 1, "status": "Stable",
            "version": "0.1.0", "verb": "provision",
            "short": "VME short",
            "long": "VME long.",
        },
        {
            "slug": "vne", "name": "vne engine", "phase": 2, "status": "Stable",
            "version": "0.1.0", "verb": "network",
            "short": "VNE short",
            "long": "VNE long.",
        },
        {
            "slug": "vse", "name": "vse engine", "phase": 3, "status": "Planned",
            "version": "0.0.0", "verb": "services",
            "short": "VSE short",
            "long": "VSE long.",
        },
        {
            "slug": "vle", "name": "vle engine", "phase": 4, "status": "Planned",
            "version": "0.0.0", "verb": "lifecycle",
            "short": "VLE short",
            "long": "VLE long.",
        },
    ],
}


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    """Lay out a fake repo root with engines.json, schema, and a target file."""
    (tmp_path / "tools").mkdir()
    schema_src = Path(__file__).parent.parent / "engines_schema.json"
    (tmp_path / "tools" / "engines_schema.json").write_text(schema_src.read_text())
    (tmp_path / "engines.json").write_text(json.dumps(SAMPLE))
    return tmp_path


# ---------------------------------------------------------------------------
# Schema + load
# ---------------------------------------------------------------------------

def test_load_engines_validates(fake_root: Path) -> None:
    data = r.load_engines(fake_root / "engines.json")
    assert data["shared"]["version"] == "0.4.0"
    assert len(data["engines"]) == 4


def test_load_engines_rejects_bad_data(fake_root: Path) -> None:
    bad = dict(SAMPLE)
    bad_engines = [dict(e) for e in bad["engines"]]
    bad_engines[0] = {**bad_engines[0], "status": "WhateverIWant"}  # not in enum
    bad["engines"] = bad_engines
    (fake_root / "engines.json").write_text(json.dumps(bad))
    with pytest.raises(SystemExit) as exc:
        r.load_engines(fake_root / "engines.json")
    assert "schema validation" in str(exc.value)


def test_engine_by_slug_returns_match() -> None:
    e = r.engine_by_slug(SAMPLE, "vne")
    assert e["phase"] == 2


def test_engine_by_slug_missing_slug_errors() -> None:
    with pytest.raises(SystemExit):
        r.engine_by_slug(SAMPLE, "nope")


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def test_render_engine_table_includes_all_four_engines() -> None:
    out = r._render_engine_table(SAMPLE)
    assert "VME" in out and "VNE" in out and "VSE" in out and "VLE" in out
    # Stable engines are bolded
    assert "**VME**" in out and "**VNE**" in out
    # Planned engines are not bolded (assertion: no '**VSE**' literal)
    assert "**VSE**" not in out and "**VLE**" not in out


def test_render_engine_table_compact_columns() -> None:
    out = r._render_engine_table_compact(SAMPLE)
    assert out.startswith("| Engine  | Phase | Status  |")


def test_render_engine_pill_format() -> None:
    out = r._render_engine_pill(SAMPLE, slug="vme")
    assert out.strip() == "**Phase 1 · Stable**"


def test_render_trailing_status_format() -> None:
    out = r._render_trailing_status(SAMPLE)
    assert "VME stable · VNE stable · VSE planned · VLE planned" in out
    assert "Apache 2.0" in out


# ---------------------------------------------------------------------------
# render_file end-to-end
# ---------------------------------------------------------------------------

def test_render_file_replaces_marker_region(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    target.write_text(
        "Header text.\n\n"
        "<!-- ENGINE-STATUS:BEGIN region=engine-pill-vme -->\n"
        "stale content\n"
        "<!-- ENGINE-STATUS:END region=engine-pill-vme -->\n\n"
        "Footer text.\n"
    )
    new, found = r.render_file(target, SAMPLE)
    assert "**Phase 1 · Stable**" in new
    assert "Header text." in new and "Footer text." in new
    assert found == ["engine-pill-vme"]
    # Markers themselves preserved.
    assert "ENGINE-STATUS:BEGIN region=engine-pill-vme" in new
    assert "ENGINE-STATUS:END region=engine-pill-vme" in new


def test_render_file_idempotent(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    target.write_text(
        "<!-- ENGINE-STATUS:BEGIN region=engine-pill-vne -->\n"
        "<!-- ENGINE-STATUS:END region=engine-pill-vne -->\n"
    )
    once, _ = r.render_file(target, SAMPLE)
    target.write_text(once)
    twice, _ = r.render_file(target, SAMPLE)
    assert once == twice


def test_render_file_unknown_region_errors(tmp_path: Path) -> None:
    target = tmp_path / "doc.md"
    target.write_text(
        "<!-- ENGINE-STATUS:BEGIN region=not-a-real-region -->\n"
        "<!-- ENGINE-STATUS:END region=not-a-real-region -->\n"
    )
    with pytest.raises(SystemExit) as exc:
        r.render_file(target, SAMPLE)
    assert "unknown region" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# CLI: --check / --write
# ---------------------------------------------------------------------------

def test_cli_check_passes_on_clean_tree(fake_root: Path) -> None:
    """With no targets in the fake root, --check is trivially clean."""
    rc = r.main(["--root", str(fake_root), "--targets", "core", "--check"])
    assert rc == 0


def test_cli_check_fails_on_drift(fake_root: Path) -> None:
    target = fake_root / "README.md"
    target.write_text(
        "<!-- ENGINE-STATUS:BEGIN region=engine-table -->\n"
        "stale table\n"
        "<!-- ENGINE-STATUS:END region=engine-table -->\n"
    )
    rc = r.main(["--root", str(fake_root), "--targets", "core", "--check"])
    assert rc == 1


def test_cli_write_fixes_drift_and_is_idempotent(fake_root: Path) -> None:
    target = fake_root / "README.md"
    target.write_text(
        "<!-- ENGINE-STATUS:BEGIN region=engine-table -->\n"
        "stale table\n"
        "<!-- ENGINE-STATUS:END region=engine-table -->\n"
    )
    rc = r.main(["--root", str(fake_root), "--targets", "core", "--write"])
    assert rc == 0
    rc2 = r.main(["--root", str(fake_root), "--targets", "core", "--check"])
    assert rc2 == 0  # writing once leaves --check clean


# ---------------------------------------------------------------------------
# Real-tree smoke: the actual core repo's --check should pass after we apply
# markers. Skipped when run from a sandboxed checkout that hasn't been
# rendered yet.
# ---------------------------------------------------------------------------

def test_real_repo_check_passes() -> None:
    """Sanity check against the live core repo (skipped in sandboxed test runs)."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    if not (repo_root / "engines.json").exists():
        pytest.skip("engines.json not at expected location")
    proc = subprocess.run(
        [sys.executable, "tools/render_engine_status.py", "--check"],
        cwd=repo_root, capture_output=True, text=True,
    )
    if proc.returncode != 0:
        pytest.fail(
            "live repo has drifted from engines.json:\n" + proc.stdout + proc.stderr
        )
