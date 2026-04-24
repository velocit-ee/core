# Contributing to velocitee core

Thanks for your interest in contributing.

## Before You Submit a PR

All contributors must sign the [Contributor License Agreement](CLA.md)
before their pull request can be merged. Add a comment to your pull request
stating: `I have read and agree to the Contributor License Agreement.`

## What to Contribute

- Bug fixes and improvements to existing engine modules
- Documentation corrections
- Test coverage

For anything larger (new features, architectural changes), open an issue
first to discuss before writing code.

## Code Standards

- Keep changes scoped to a single engine module per PR
- Each engine must remain independently functional
- If your change affects the handoff manifest schema, document it
- All Python code must pass `ruff check` — run `ruff check vme/cli/ shared/` before submitting
- Tests must pass: `cd vme && python -m pytest`
