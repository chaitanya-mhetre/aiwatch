"""Aggregations over recorded calls, shared by the CLI and the dashboard."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from aiwatch.record import CallRecord

RECORD_FIELDS = ("provider", "model", "operation", "status", "stream", "run_id")
_DURATION = re.compile(r"^(\d+)([mhdw])$")
_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_since(value: str | None, now: datetime | None = None) -> datetime | None:
    """``"7d"`` -> the datetime 7 days ago. ``None`` means no lower bound."""
    if value is None:
        return None
    match = _DURATION.match(value.strip())
    if match is None:
        raise ValueError(f"invalid duration {value!r}; use e.g. 30m, 24h, 7d, 2w")
    amount, unit = int(match.group(1)), match.group(2)
    return (now or datetime.now(UTC)) - timedelta(**{_UNITS[unit]: amount})


def percentile(values: Sequence[int], pct: float) -> int | None:
    """Nearest-rank percentile. Returns None for an empty input."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def group_key(record: CallRecord, dimension: str) -> str:
    """A dimension is a record field (``model``) or a tag (``feature`` / ``tag:feature``)."""
    if dimension in RECORD_FIELDS:
        return str(getattr(record, dimension))
    return record.tags.get(dimension.removeprefix("tag:"), "-")


@dataclass
class Row:
    key: tuple[str, ...]
    calls: int = 0
    errors: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: Decimal = Decimal(0)
    unpriced: int = 0  # calls whose cost is unknown
    latencies: list[int] = field(default_factory=list)

    @property
    def error_rate(self) -> float:
        return self.errors / self.calls if self.calls else 0.0

    @property
    def p50_ms(self) -> int | None:
        return percentile(self.latencies, 50)

    @property
    def p95_ms(self) -> int | None:
        return percentile(self.latencies, 95)

    def add(self, r: CallRecord) -> None:
        self.calls += 1
        self.errors += r.status == "error"
        self.prompt_tokens += r.prompt_tokens or 0
        self.completion_tokens += r.completion_tokens or 0
        if r.est_cost_usd is None:
            self.unpriced += 1
        else:
            self.cost_usd += r.est_cost_usd
        self.latencies.append(r.latency_ms)

    def as_dict(self, dimensions: Sequence[str]) -> dict[str, object]:
        return {
            **dict(zip(dimensions, self.key, strict=True)),
            "calls": self.calls,
            "errors": self.errors,
            "error_rate": round(self.error_rate, 4),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "est_cost_usd": str(self.cost_usd),
            "unpriced_calls": self.unpriced,
            "p50_ms": self.p50_ms,
            "p95_ms": self.p95_ms,
        }


def aggregate(records: Iterable[CallRecord], dimensions: Sequence[str]) -> list[Row]:
    rows: dict[tuple[str, ...], Row] = defaultdict(lambda: Row(key=()))
    for r in records:
        key = tuple(group_key(r, d) for d in dimensions)
        row = rows[key]
        row.key = key
        row.add(r)
    return sorted(rows.values(), key=lambda row: (-row.cost_usd, -row.calls))


def daily_cost(records: Iterable[CallRecord]) -> list[dict[str, object]]:
    """Cost and calls per UTC day and model, for the dashboard chart."""
    buckets: dict[tuple[str, str], Row] = {}
    for r in records:
        key = (r.ts.astimezone(UTC).date().isoformat(), r.model)
        buckets.setdefault(key, Row(key=key)).add(r)
    return [
        {"day": day, "model": model, "calls": row.calls, "est_cost_usd": str(row.cost_usd)}
        for (day, model), row in sorted(buckets.items())
    ]


def format_table(rows: list[Row], dimensions: Sequence[str]) -> str:
    headers = [
        *dimensions,
        "calls",
        "err%",
        "in_tok",
        "out_tok",
        "est_cost_usd",
        "p50_ms",
        "p95_ms",
    ]
    body = [
        [
            *row.key,
            str(row.calls),
            f"{row.error_rate * 100:.1f}",
            str(row.prompt_tokens),
            str(row.completion_tokens),
            f"{row.cost_usd:.6f}" + ("*" if row.unpriced else ""),
            "-" if row.p50_ms is None else str(row.p50_ms),
            "-" if row.p95_ms is None else str(row.p95_ms),
        ]
        for row in rows
    ]
    widths = [max([len(h), *(len(line[i]) for line in body)]) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True))]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(line, widths, strict=True)) for line in body]
    if any(row.unpriced for row in rows):
        lines.append("* some calls have no price entry or no token counts; their cost is excluded")
    return "\n".join(lines)
