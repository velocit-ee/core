"""Shared CLI utilities — used by all velocitee engines (VME, VNE, VSE, VLE).

Provides:
  make_app(engine_name)  — Typer app factory with clean error handling
  fatal(msg, hint)       — print "error: ..." and exit 1
  warn(msg)              — print "warning: ..."
  run_app(app)           — run a Typer app without raw Python tracebacks
"""

from __future__ import annotations

import difflib
import sys

import click
import typer
from typer.core import TyperGroup


def _make_group(engine_name: str) -> type[TyperGroup]:
    """Return a TyperGroup subclass that formats unknown-command errors cleanly."""

    class VelociteeGroup(TyperGroup):
        def resolve_command(self, ctx: click.Context, args: list[str]):
            try:
                return super().resolve_command(ctx, args)
            except click.UsageError:
                cmd = args[0] if args else ""
                available = list(self.list_commands(ctx))
                typer.echo(
                    f"{engine_name}: '{cmd}' is not a {engine_name} command. "
                    f"See '{engine_name} --help'.",
                    err=True,
                )
                close = difflib.get_close_matches(cmd, available, n=3, cutoff=0.6)
                if close:
                    label = "The most similar command is:" if len(close) == 1 else "The most similar commands are:"
                    typer.echo(f"\n{label}", err=True)
                    for c in close:
                        typer.echo(f"        {c}", err=True)
                typer.echo("", err=True)
                sys.exit(1)

        def main(self, *args, prog_name: str | None = None, **kwargs):
            return super().main(*args, prog_name=prog_name or engine_name, **kwargs)

    return VelociteeGroup


def make_app(engine_name: str, **kwargs) -> typer.Typer:
    """Create a Typer app configured for a velocitee engine.

    Adds -h as a help alias, disables shell completion noise, and attaches
    the custom group class that handles unknown commands cleanly.
    """
    return typer.Typer(
        name=engine_name,
        cls=_make_group(engine_name),
        context_settings={"help_option_names": ["-h", "--help"]},
        add_completion=False,
        **kwargs,
    )


def fatal(msg: str, hint: str | None = None) -> None:
    """Print 'error: <msg>' to stderr and exit 1."""
    typer.echo(f"error: {msg}", err=True)
    if hint:
        typer.echo(f"hint:  {hint}", err=True)
    raise typer.Exit(1)


def warn(msg: str) -> None:
    """Print 'warning: <msg>' to stderr."""
    typer.echo(f"warning: {msg}", err=True)


def run_app(app: typer.Typer) -> None:
    """Run *app* with clean exception handling — no raw Python tracebacks."""
    try:
        app(standalone_mode=False)
    except typer.Exit as exc:
        sys.exit(exc.exit_code)
    except typer.Abort:
        typer.echo("\nAborted.", err=True)
        sys.exit(1)
    except KeyboardInterrupt:
        typer.echo("\nInterrupted.", err=True)
        sys.exit(130)
    except click.UsageError as exc:
        ctx = exc.ctx
        scope = ctx.command_path if ctx else app.info.name or "vme"
        typer.echo(f"error: {exc.format_message()}", err=True)
        typer.echo(f"Try '{scope} --help' for more information.", err=True)
        sys.exit(2)
    except click.exceptions.Exit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        sys.exit(1)
