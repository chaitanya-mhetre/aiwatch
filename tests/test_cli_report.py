from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from aiwatch import CallRecord, SQLiteSink
from aiwatch.cli import app
from aiwatch.dashboard import create_app
from aiwatch.report import aggregate, parse_since, percentile

runner = CliRunner()


def rec(model: str, cost: str | None, latency: int, status: str = "ok", **tags: str) -> CallRecord:
    return CallRecord(
        provider="demo",
        model=model,
        prompt_tokens=10,
        completion_tokens=5,
        latency_ms=latency,
        status=status,  # type: ignore[arg-type]
        est_cost_usd=None if cost is None else Decimal(cost),
        tags=tags,
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "a.db"
    sink = SQLiteSink(path)
    for r in [
        rec("demo-a", "0.01", 100, feature="x"),
        rec("demo-a", "0.02", 300, feature="y"),
        rec("demo-b", None, 50, status="error", feature="x"),
    ]:
        sink.write(r)
    sink.close()
    return path


def test_percentile_nearest_rank() -> None:
    assert percentile([], 50) is None
    assert percentile([5], 95) == 5
    assert percentile(list(range(1, 101)), 95) == 95


def test_parse_since() -> None:
    now = datetime(2026, 9, 25, tzinfo=UTC)
    assert parse_since("7d", now) == now - timedelta(days=7)
    assert parse_since(None) is None
    with pytest.raises(ValueError):
        parse_since("7 days")


def test_aggregate_by_model_and_tag(db: Path) -> None:
    sink = SQLiteSink(db)
    rows = aggregate(sink.iter_records(), ["model"])
    by_model = {r.key[0]: r for r in rows}
    assert by_model["demo-a"].cost_usd == Decimal("0.03")
    assert by_model["demo-b"].unpriced == 1 and by_model["demo-b"].error_rate == 1.0
    assert {r.key for r in aggregate(sink.iter_records(), ["feature"])} == {("x",), ("y",)}
    sink.close()


def test_report_command(db: Path) -> None:
    result = runner.invoke(app, ["report", "--db", str(db), "--group-by", "model,feature"])
    assert result.exit_code == 0, result.output
    assert "demo-a" in result.output and "some calls have no price entry" in result.output


def test_report_without_db(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "--db", str(tmp_path / "missing.db")])
    assert result.exit_code == 0 and "no data yet" in result.output


def test_export_csv_and_jsonl(db: Path) -> None:
    csv_out = runner.invoke(app, ["export", "--db", str(db)]).output
    assert csv_out.splitlines()[0].startswith("id,ts,provider,model")
    assert len(csv_out.splitlines()) == 4
    jsonl = runner.invoke(app, ["export", "--db", str(db), "--format", "jsonl"]).output
    assert len(jsonl.splitlines()) == 3


def test_run_wraps_a_script(tmp_path: Path) -> None:
    script = tmp_path / "app.py"
    script.write_text(
        "import sys\n"
        "from aiwatch.recorder import get_recorder, current_tags\n"
        "get_recorder().record(provider='demo', model='demo-1', prompt_tokens=1_000_000,"
        " completion_tokens=0)\n"
        "assert current_tags() == {'env': 'dev'}\n"
        "assert sys.argv[1:] == ['--flag']\n"
    )
    db = tmp_path / "run.db"
    result = runner.invoke(app, ["run", str(script), "--tag", "env=dev", "--db", str(db), "--flag"])
    assert result.exit_code == 0, result.output
    [r] = list(SQLiteSink(db).iter_records())
    assert r.run_id is not None and r.tags == {"env": "dev"}
    assert r.est_cost_usd == Decimal(1)  # bundled FAKE demo price: $1 per 1M input tokens


def test_run_propagates_exit_code(tmp_path: Path) -> None:
    script = tmp_path / "fail.py"
    script.write_text("raise SystemExit(3)\n")
    result = runner.invoke(app, ["run", str(script), "--db", str(tmp_path / "x.db")])
    assert result.exit_code == 3


def test_prices_commands(tmp_path: Path) -> None:
    assert "demo" in runner.invoke(app, ["prices", "show"]).output
    bad = tmp_path / "p.yaml"
    bad.write_text(
        "prices:\n  - {provider: openai, model: x, effective_from: 2026-01-01,"
        " input_per_mtok: 1, output_per_mtok: 1, source: 'https://example.com'}\n"
    )
    result = runner.invoke(app, ["prices", "validate", str(bad)])
    assert result.exit_code == 1


def test_dashboard_endpoints(db: Path) -> None:
    client = TestClient(create_app(db))
    assert "aiwatch" in client.get("/").text
    summary = client.get("/api/summary", params={"since": "1d"}).json()
    assert {row["model"] for row in summary} == {"demo-a", "demo-b"}
    assert client.get("/api/daily").status_code == 200
    assert client.get("/api/summary", params={"since": "forever"}).status_code == 422
