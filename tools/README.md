# tools — single source of truth for engine status

`engines.json` (at the repo root) is the canonical source of truth for
engine status, phase, version, and descriptive copy. Any markdown file
that displays this information regenerates from it automatically — no
hand-editing.

## Files

- **`../engines.json`** — the data. JSON, schema-validated.
- **`engines_schema.json`** — JSON Schema Draft 7 the data must satisfy.
- **`render_engine_status.py`** — the renderer. Reads `engines.json`,
  rewrites marker-delimited regions in target markdown files.
- **`tests/test_render_engine_status.py`** — unit tests.

## How it works

Each markdown surface contains `<!-- ENGINE-STATUS:BEGIN region=NAME -->`
and `<!-- ENGINE-STATUS:END region=NAME -->` marker pairs. The renderer
finds every pair, dispatches on `NAME` to a render function, and rewrites
the body between the markers.

```
<!-- ENGINE-STATUS:BEGIN region=engine-pill-vme -->
**Phase 1 · Stable**
<!-- ENGINE-STATUS:END region=engine-pill-vme -->
```

Both repos that consume engine status carry their own copy of this
script + schema:

- **`velocit-ee/core`** — the canonical home. Targets: `README.md`,
  `CHANGELOG.md`, `vme/README.md`, `vne/README.md`.
- **`velocit-ee/.github`** — mirrors the script + schema + data file.
  Target: `profile/README.md` (the GitHub org page).

CI in both repos runs `python tools/render_engine_status.py --check` and
fails on drift.

## When to use it

### Updating engine status / version

Edit `engines.json` only. Run the renderer, commit both files together:

```bash
$EDITOR engines.json
python tools/render_engine_status.py --write
git add engines.json README.md CHANGELOG.md vme/README.md vne/README.md
git commit
```

The pre-commit hook does the `--write` step automatically when any of
the relevant files changes — most commits don't need the manual step.

### Adding a new region

1. Pick a region name (e.g. `engine-summary`).
2. Add a render function in `render_engine_status.py:RENDERERS`. Take
   the validated `engines.json` dict; return the markdown body.
3. Add the marker pair in the target markdown file(s).
4. Add the `(target_relpath, region_name)` tuple to `TARGETS_CORE`
   and/or `TARGETS_DOTGITHUB`.
5. Run `--write`. Commit.

### Adding a new field to engines.json

1. Update `engines_schema.json` with the new property.
2. Add the field to every engine object in `engines.json`.
3. Reference it in whichever render function needs it.
4. Update the schema-validation tests if you added a hard constraint.

### Adding a new engine

1. Append a new entry to `engines.json` `engines[]` (slug, phase,
   status, version, short, long, verb).
2. Run the renderer. The engine table picks it up automatically; if
   the engine needs its own pill region (e.g. on the org profile),
   add the marker pair in the target file and a `RENDERERS` entry.

## Cross-repo synchronisation (TODO)

`engines.json` currently lives in **both** `velocit-ee/core` and
`velocit-ee/.github`. The core copy is canonical — when it changes,
the `.github` copy needs to be updated to match. Future work:

- A GitHub Action in core that watches `engines.json` and opens a PR
  in `.github` with the updated file when it changes. Requires a PAT
  or GitHub App with cross-repo write access.

For now, when you change `engines.json` in core, manually copy it to
`.github` in the same PR sequence:

```bash
cp ~/Projects/velocitee-org/core/engines.json ~/Projects/velocitee-org/dot-github/engines.json
cd ~/Projects/velocitee-org/dot-github && python tools/render_engine_status.py --write
```

CI in `.github` will still catch drift, so you'll know if you forgot
this step.

## Docs site integration (TODO)

`docs.velocit.ee` (the MkDocs Material site at `velocit-ee/docs`) is
not yet wired into this system. Two paths to consider:

1. **Build-time clone + `mkdocs-include-markdown-plugin`** — the docs
   site's build step clones core, then includes selected README
   sections directly. Single source of truth; docs site loses ability
   to build without network.
2. **`mkdocs-macros-plugin` reading a copied `engines.json`** — same
   data file, dynamically interpolated into docs pages with
   `{{ engines.vme.status }}` etc. Same drift problem as `.github`
   today; same Action-based fix would apply.

Either way, that integration is a follow-up PR. Update this section
when it lands.
