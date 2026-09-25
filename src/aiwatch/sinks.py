"""Where records go. A sink is anything with ``write(record)`` and ``close()``."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from aiwatch.record import CallRecord

log = logging.getLogger("aiwatch")

SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (
    id TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    operation TEXT NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    cached_tokens INTEGER,
    tokens_estimated INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER NOT NULL,
    ttft_ms INTEGER,
    stream INTEGER NOT NULL,
    status TEXT NOT NULL,
    error_type TEXT,
    est_cost_usd TEXT,
    price_version TEXT,
    tags TEXT NOT NULL DEFAULT '{}',
    run_id TEXT
);
CREATE INDEX IF NOT EXISTS ix_calls_ts ON calls (ts);
CREATE INDEX IF NOT EXISTS ix_calls_provider_model ON calls (provider, model);
CREATE INDEX IF NOT EXISTS ix_calls_run ON calls (run_id);
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    command TEXT NOT NULL,
    git_sha TEXT
);
"""


class Sink(Protocol):
    def write(self, record: CallRecord) -> None: ...
    def close(self) -> None: ...


class MemorySink:
    """Keeps records in a list. Handy in tests and notebooks."""

    def __init__(self) -> None:
        self.records: list[CallRecord] = []

    def write(self, record: CallRecord) -> None:
        self.records.append(record)

    def close(self) -> None:
        pass


class TeeSink:
    """Fan one record out to several sinks, e.g. SQLite for reports plus OTel for traces.

    A failing sink doesn't stop the others; the error is logged and swallowed, in line with
    aiwatch's rule that recording must never break the caller.
    """

    def __init__(self, *sinks: Sink) -> None:
        self.sinks = sinks

    def write(self, record: CallRecord) -> None:
        for sink in self.sinks:
            try:
                sink.write(record)
            except Exception:
                log.debug("aiwatch: sink %r failed", sink, exc_info=True)

    def close(self) -> None:
        for sink in self.sinks:
            try:
                sink.close()
            except Exception:
                log.debug("aiwatch: closing sink %r failed", sink, exc_info=True)


class JSONLSink:
    """Appends one JSON object per line. Good for CI artefacts."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: CallRecord) -> None:
        line = record.model_dump_json()
        with self._lock, self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def close(self) -> None:
        pass


def _to_row(r: CallRecord) -> tuple[object, ...]:
    return (
        r.id,
        r.ts.isoformat(),
        r.provider,
        r.model,
        r.operation,
        r.prompt_tokens,
        r.completion_tokens,
        r.cached_tokens,
        int(r.tokens_estimated),
        r.latency_ms,
        r.ttft_ms,
        int(r.stream),
        r.status,
        r.error_type,
        None if r.est_cost_usd is None else str(r.est_cost_usd),
        r.price_version,
        json.dumps(r.tags, sort_keys=True),
        r.run_id,
    )


def _from_row(row: sqlite3.Row) -> CallRecord:
    return CallRecord(
        id=row["id"],
        ts=datetime.fromisoformat(row["ts"]),
        provider=row["provider"],
        model=row["model"],
        operation=row["operation"],
        prompt_tokens=row["prompt_tokens"],
        completion_tokens=row["completion_tokens"],
        cached_tokens=row["cached_tokens"],
        tokens_estimated=bool(row["tokens_estimated"]),
        latency_ms=row["latency_ms"],
        ttft_ms=row["ttft_ms"],
        stream=bool(row["stream"]),
        status=row["status"],
        error_type=row["error_type"],
        est_cost_usd=None if row["est_cost_usd"] is None else Decimal(row["est_cost_usd"]),
        price_version=row["price_version"],
        tags=json.loads(row["tags"]),
        run_id=row["run_id"],
    )


class SQLiteSink:
    """SQLite in WAL mode. One connection, guarded by a lock, so threads can share the sink."""

    def __init__(self, path: Path) -> None:
        self.path = path
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def write(self, record: CallRecord) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                _to_row(record),
            )

    def start_run(
        self, run_id: str, started_at: datetime, command: str, git_sha: str | None
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                (run_id, started_at.isoformat(), command, git_sha),
            )

    def iter_records(
        self, since: datetime | None = None, run_id: str | None = None
    ) -> Iterator[CallRecord]:
        sql = "SELECT * FROM calls WHERE 1=1"
        params: list[object] = []
        if since is not None:
            sql += " AND ts >= ?"
            params.append(since.isoformat())
        if run_id is not None:
            sql += " AND run_id = ?"
            params.append(run_id)
        sql += " ORDER BY ts"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        for row in rows:
            yield _from_row(row)

    def close(self) -> None:
        with self._lock:
            self._conn.close()
