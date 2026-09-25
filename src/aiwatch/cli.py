"""The ``aiwatch`` command line."""

from __future__ import annotations

import csv
import io
import os
import runpy
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from aiwatch import instrument, recorder
from aiwatch.pricing import PriceTable
from aiwatch.report import aggregate, format_table, parse_since
from aiwatch.sinks import SQLiteSink

app = typer.Typer(help="Local-first LLM usage and cost tracking.", no_args_is_help=True)
prices_app = typer.Typer(help="Inspect the price table.", no_args_is_help=True)
app.add_typer(prices_app, name="prices")

DbOption = Annotated[
    Path | None,
    typer.Option("--db", help="SQLite file (default: $AIWATCH_DB or .aiwatch/aiwatch.db)"),
]
SinceOption = Annotated[str | None, typer.Option("--since", help="e.g. 30m, 24h, 7d")]


def _db(path: Path | None) -> Path:
    return path or recorder.default_db_path()


def _parse_tags(values: list[str]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for item in values:
        key, sep, value = item.partition("=")
        if not sep or not key:
            raise typer.BadParameter(f"tag must look like key=value, got {item!r}")
        tags[key] = value
    return tags


def _git_sha() -> str | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=2
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def run(
    ctx: typer.Context,
    script: Annotated[Path, typer.Argument(exists=True, dir_okay=False, help="Python script")],
    tag: Annotated[list[str], typer.Option("--tag", "-t", help="key=value, repeatable")] = [],  # noqa: B006
    db: DbOption = None,
) -> None:
    """Run a Python script with every supported LLM SDK instrumented."""
    run_id = uuid.uuid4().hex[:12]
    sink = SQLiteSink(_db(db))
    sink.start_run(run_id, datetime.now(UTC), " ".join([str(script), *ctx.args]), _git_sha())
    recorder.configure(sink=sink, run_id=run_id)
    instrument()

    saved_argv, saved_path = sys.argv, list(sys.path)
    sys.argv = [str(script), *ctx.args]
    sys.path.insert(0, str(script.resolve().parent))
    exit_code = 0
    try:
        with recorder.tags(**_parse_tags(tag)):
            runpy.run_path(str(script), run_name="__main__")
    except SystemExit as exc:
        exit_code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.argv, sys.path[:] = saved_argv, saved_path
        rows = aggregate(sink.iter_records(run_id=run_id), ["provider", "model"])
        typer.echo(f"\naiwatch run {run_id}", err=True)
        typer.echo(format_table(rows, ["provider", "model"]) if rows else "no LLM calls", err=True)
        sink.close()
    raise typer.Exit(exit_code)


@app.command()
def report(
    since: SinceOption = None,
    group_by: Annotated[
        str, typer.Option("--group-by", "-g", help="comma-separated: provider,model,<tag>")
    ] = "provider,model",
    run_id: Annotated[str | None, typer.Option("--run", help="only this run id")] = None,
    db: DbOption = None,
) -> None:
    """Cost, tokens, latency and errors, grouped by any field or tag."""
    path = _db(db)
    if not path.exists():
        typer.echo(f"no data yet ({path} does not exist)")
        raise typer.Exit(0)
    dims = [d.strip() for d in group_by.split(",") if d.strip()]
    sink = SQLiteSink(path)
    try:
        rows = aggregate(sink.iter_records(since=parse_since(since), run_id=run_id), dims)
    finally:
        sink.close()
    typer.echo(format_table(rows, dims) if rows else "no calls in range")


@app.command()
def export(
    fmt: Annotated[str, typer.Option("--format", "-f", help="csv or jsonl")] = "csv",
    since: SinceOption = None,
    db: DbOption = None,
) -> None:
    """Write raw call records to stdout."""
    if fmt not in ("csv", "jsonl"):
        raise typer.BadParameter("format must be csv or jsonl")
    sink = SQLiteSink(_db(db))
    records = list(sink.iter_records(since=parse_since(since)))
    sink.close()
    if fmt == "jsonl":
        for r in records:
            typer.echo(r.model_dump_json())
        return
    buf = io.StringIO()
    writer: csv.DictWriter[str] | None = None
    for r in records:
        row = r.model_dump(mode="json")
        row["tags"] = ";".join(f"{k}={v}" for k, v in sorted(r.tags.items()))
        if writer is None:
            writer = csv.DictWriter(buf, fieldnames=list(row))
            writer.writeheader()
        writer.writerow(row)
    typer.echo(buf.getvalue(), nl=False)


@app.command()
def ui(
    port: Annotated[int, typer.Option(help="port on 127.0.0.1")] = 8765,
    db: DbOption = None,
) -> None:
    """Open the local dashboard (binds to 127.0.0.1 only)."""
    import uvicorn

    from aiwatch.dashboard import create_app

    typer.echo(f"aiwatch dashboard on http://127.0.0.1:{port}")
    uvicorn.run(create_app(_db(db)), host="127.0.0.1", port=port, log_level="warning")


def _load_prices(path: Path | None) -> PriceTable:
    env = os.environ.get("AIWATCH_PRICES")
    return PriceTable.load(path or (Path(env) if env else None))


@prices_app.command("show")
def prices_show(
    file: Annotated[Path | None, typer.Option("--file", help="price YAML")] = None,
) -> None:
    """List price entries (USD per 1M tokens)."""
    for e in _load_prices(file).entries:
        typer.echo(
            f"{e.provider:<12} {e.model:<28} from {e.effective_from}  "
            f"in {e.input_per_mtok}  out {e.output_per_mtok}  src {e.source}"
        )


@prices_app.command("validate")
def prices_validate(file: Annotated[Path, typer.Argument(exists=True, dir_okay=False)]) -> None:
    """Check a price YAML file: schema, and a source + checked_on date on every real entry."""
    table = PriceTable.load(file)
    problems = [
        f"{e.version}: real entry needs checked_on"
        for e in table.entries
        if e.source != "FAKE" and e.checked_on is None
    ]
    for p in problems:
        typer.echo(p, err=True)
    typer.echo(f"{len(table.entries)} entries, {len(problems)} problems")
    raise typer.Exit(1 if problems else 0)
