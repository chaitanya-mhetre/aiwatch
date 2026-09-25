"""A tiny read-only dashboard: FastAPI JSON endpoints + one static HTML page."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from aiwatch.record import CallRecord
from aiwatch.report import aggregate, daily_cost, parse_since
from aiwatch.sinks import SQLiteSink


def create_app(db_path: Path) -> FastAPI:
    app = FastAPI(title="aiwatch", docs_url=None, redoc_url=None)

    def load(since: str | None) -> list[CallRecord]:
        if not db_path.exists():
            return []
        try:
            start = parse_since(since)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        sink = SQLiteSink(db_path)
        try:
            return list(sink.iter_records(since=start))
        finally:
            sink.close()

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return resources.files("aiwatch.dashboard").joinpath("static/index.html").read_text()

    @app.get("/api/summary")
    def summary(
        since: str | None = Query(default="7d"), group_by: str = Query(default="provider,model")
    ) -> list[dict[str, object]]:
        dims = [d for d in group_by.split(",") if d]
        return [row.as_dict(dims) for row in aggregate(load(since), dims)]

    @app.get("/api/daily")
    def daily(since: str | None = Query(default="30d")) -> list[dict[str, object]]:
        return daily_cost(load(since))

    return app
