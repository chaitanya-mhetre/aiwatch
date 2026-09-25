"""The recorder: turns raw usage numbers into a ``CallRecord`` and hands it to the sink.

Everything here follows one rule: **aiwatch must never break the user's call.** Any failure while
recording is logged (``AIWATCH_DEBUG=1`` shows it) and swallowed.
"""

from __future__ import annotations

import contextvars
import functools
import inspect
import logging
import os
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import MappingProxyType
from typing import Any, ParamSpec, TypeVar, cast

from aiwatch.pricing import PriceTable
from aiwatch.record import CallRecord, Status
from aiwatch.sinks import Sink, SQLiteSink, TeeSink

log = logging.getLogger("aiwatch")
if os.environ.get("AIWATCH_DEBUG"):
    logging.basicConfig()
    log.setLevel(logging.DEBUG)

DEFAULT_DB = Path(".aiwatch") / "aiwatch.db"

# An immutable default: tags are never mutated in place, each ``tags()`` block sets a new mapping.
_tags: contextvars.ContextVar[Mapping[str, str]] = contextvars.ContextVar(
    "aiwatch_tags", default=MappingProxyType({})
)

P = ParamSpec("P")
R = TypeVar("R")


def default_db_path() -> Path:
    return Path(os.environ.get("AIWATCH_DB", str(DEFAULT_DB)))


def default_sink() -> Sink:
    """SQLite by default. With ``AIWATCH_OTEL=1``, also emit OpenTelemetry spans.

    The OTel part needs ``pip install aiwatch[otel]`` and a TracerProvider set up by the app.
    """
    sqlite = SQLiteSink(default_db_path())
    if os.environ.get("AIWATCH_OTEL", "").lower() in ("1", "true", "yes"):
        from aiwatch.otel import OTelSink

        return TeeSink(sqlite, OTelSink())
    return sqlite


class Recorder:
    def __init__(
        self,
        sink: Sink | None = None,
        prices: PriceTable | None = None,
        run_id: str | None = None,
    ) -> None:
        self._sink = sink
        self._prices = prices
        self.run_id = run_id or os.environ.get("AIWATCH_RUN_ID")

    @property
    def sink(self) -> Sink:
        # Created lazily so that importing aiwatch never touches the filesystem.
        if self._sink is None:
            self._sink = default_sink()
        return self._sink

    @property
    def prices(self) -> PriceTable:
        if self._prices is None:
            path = os.environ.get("AIWATCH_PRICES")
            self._prices = PriceTable.load(Path(path) if path else None)
        return self._prices

    def record(
        self,
        *,
        provider: str,
        model: str,
        operation: str = "chat",
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cached_tokens: int | None = None,
        latency_ms: int = 0,
        ttft_ms: int | None = None,
        stream: bool = False,
        status: Status = "ok",
        error_type: str | None = None,
    ) -> CallRecord | None:
        try:
            cost, version = self.prices.cost(
                provider, model, prompt_tokens, completion_tokens, cached_tokens
            )
            record = CallRecord(
                provider=provider,
                model=model,
                operation=operation,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
                latency_ms=latency_ms,
                ttft_ms=ttft_ms,
                stream=stream,
                status=status,
                error_type=error_type,
                est_cost_usd=cost,
                price_version=version,
                tags=dict(_tags.get()),
                run_id=self.run_id,
            )
            self.sink.write(record)
            return record
        except Exception:  # never let bookkeeping break the caller
            log.debug("aiwatch: failed to record call", exc_info=True)
            return None

    def close(self) -> None:
        if self._sink is not None:
            self._sink.close()


_recorder = Recorder()


def get_recorder() -> Recorder:
    return _recorder


def configure(
    sink: Sink | None = None,
    prices: PriceTable | None = None,
    run_id: str | None = None,
) -> Recorder:
    """Replace the global recorder (e.g. ``configure(sink=MemorySink())`` in tests)."""
    global _recorder
    _recorder = Recorder(sink=sink, prices=prices, run_id=run_id)
    return _recorder


@contextmanager
def tags(**values: str) -> Iterator[None]:
    """Attach tags to every call made inside the block. Nested blocks merge, inner wins.

    Uses a ``ContextVar``, so tags follow asyncio tasks correctly and don't leak between them.
    """
    token = _tags.set({**_tags.get(), **{k: str(v) for k, v in values.items()}})
    try:
        yield
    finally:
        _tags.reset(token)


def current_tags() -> dict[str, str]:
    return dict(_tags.get())


def track(**values: str) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator form of :func:`tags`. Works on sync and async functions."""

    def decorator(fn: Callable[P, R]) -> Callable[P, R]:
        if inspect.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with tags(**values):
                    return await fn(*args, **kwargs)

            return cast(Callable[P, R], async_wrapper)

        @functools.wraps(fn)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with tags(**values):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
