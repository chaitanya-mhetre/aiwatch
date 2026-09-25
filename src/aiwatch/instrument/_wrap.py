"""Generic wrapping machinery shared by every provider module.

A provider module describes *how to read* its SDK (a :class:`CallSpec`); this module does the
timing, stream proxying, error capture and patch bookkeeping once, for all providers.
"""

from __future__ import annotations

import functools
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any, NamedTuple, Protocol

from aiwatch.recorder import get_recorder, log


class Usage(NamedTuple):
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None


class StreamAccumulator(Protocol):
    def feed(self, chunk: Any) -> None: ...
    def usage(self) -> Usage: ...


@dataclass(frozen=True)
class CallSpec:
    """Everything aiwatch needs to know about one SDK method."""

    operation: str
    provider: Callable[[Any], str]  # receives the SDK resource object (``self``)
    model: Callable[[dict[str, Any]], str]  # receives the call's kwargs
    usage: Callable[[Any], Usage]  # receives a non-streaming response
    accumulator: Callable[[], StreamAccumulator]
    is_stream: Callable[[dict[str, Any]], bool] = lambda kwargs: bool(kwargs.get("stream"))


def _ms(start: float, end: float | None = None) -> int:
    return int(((end if end is not None else time.perf_counter()) - start) * 1000)


def _safe(fn: Callable[[], Any], default: Any = None) -> Any:
    try:
        return fn()
    except Exception:
        log.debug("aiwatch: extractor failed", exc_info=True)
        return default


class _Call:
    """State for one in-flight call; records exactly once."""

    def __init__(self, spec: CallSpec, sdk_self: Any, kwargs: dict[str, Any], stream: bool) -> None:
        self.spec = spec
        self.provider = _safe(lambda: spec.provider(sdk_self), "unknown")
        self.model = _safe(lambda: spec.model(kwargs), "unknown")
        self.stream = stream
        self.start = time.perf_counter()
        self.ttft_ms: int | None = None
        self.done = False

    def finish(self, usage: Usage, error: BaseException | None = None) -> None:
        if self.done:
            return
        self.done = True
        get_recorder().record(
            provider=self.provider,
            model=self.model,
            operation=self.spec.operation,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            cached_tokens=usage.cached_tokens,
            latency_ms=_ms(self.start),
            ttft_ms=self.ttft_ms,
            stream=self.stream,
            status="error" if error is not None else "ok",
            error_type=type(error).__name__ if error is not None else None,
        )


class StreamProxy:
    """Wraps a sync stream: passes every chunk through untouched and records when it ends.

    "Ends" means exhausted, raised, closed, or garbage-collected after an early ``break``.
    Unknown attributes are forwarded, so ``stream.response`` etc. keep working.
    """

    def __init__(self, inner: Any, call: _Call) -> None:
        self._inner = inner
        self._it: Iterator[Any] = iter(inner)
        self._call = call
        self._acc = call.spec.accumulator()

    def __iter__(self) -> StreamProxy:
        return self

    def __next__(self) -> Any:
        try:
            chunk = next(self._it)
        except StopIteration:
            self._finish()
            raise
        except BaseException as exc:
            self._finish(exc)
            raise
        if self._call.ttft_ms is None:
            self._call.ttft_ms = _ms(self._call.start)
        _safe(lambda: self._acc.feed(chunk))
        return chunk

    def _finish(self, error: BaseException | None = None) -> None:
        self._call.finish(_safe(self._acc.usage, Usage()), error)

    def close(self) -> None:
        self._finish()
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> StreamProxy:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:
        if not getattr(self, "_call", None) or self._call.done:
            return
        _safe(self._finish)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class AsyncStreamProxy:
    """The async twin of :class:`StreamProxy`."""

    def __init__(self, inner: Any, call: _Call) -> None:
        self._inner = inner
        self._it: AsyncIterator[Any] = inner.__aiter__()
        self._call = call
        self._acc = call.spec.accumulator()

    def __aiter__(self) -> AsyncStreamProxy:
        return self

    async def __anext__(self) -> Any:
        try:
            chunk = await self._it.__anext__()
        except StopAsyncIteration:
            self._finish()
            raise
        except BaseException as exc:
            self._finish(exc)
            raise
        if self._call.ttft_ms is None:
            self._call.ttft_ms = _ms(self._call.start)
        _safe(lambda: self._acc.feed(chunk))
        return chunk

    def _finish(self, error: BaseException | None = None) -> None:
        self._call.finish(_safe(self._acc.usage, Usage()), error)

    async def close(self) -> None:
        self._finish()
        close = getattr(self._inner, "close", None) or getattr(self._inner, "aclose", None)
        if callable(close):
            await close()

    async def __aenter__(self) -> AsyncStreamProxy:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def __del__(self) -> None:
        if not getattr(self, "_call", None) or self._call.done:
            return
        _safe(self._finish)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def wrap_sync(original: Callable[..., Any], spec: CallSpec) -> Callable[..., Any]:
    @functools.wraps(original)
    def wrapper(sdk_self: Any, *args: Any, **kwargs: Any) -> Any:
        stream = bool(_safe(lambda: spec.is_stream(kwargs), False))
        call = _Call(spec, sdk_self, kwargs, stream)
        try:
            result = original(sdk_self, *args, **kwargs)
        except BaseException as exc:
            call.finish(Usage(), exc)
            raise
        if stream:
            return StreamProxy(result, call)
        call.finish(_safe(lambda: spec.usage(result), Usage()))
        return result

    wrapper.__aiwatch_original__ = original  # type: ignore[attr-defined]
    return wrapper


def wrap_async(original: Callable[..., Any], spec: CallSpec) -> Callable[..., Any]:
    @functools.wraps(original)
    async def wrapper(sdk_self: Any, *args: Any, **kwargs: Any) -> Any:
        stream = bool(_safe(lambda: spec.is_stream(kwargs), False))
        call = _Call(spec, sdk_self, kwargs, stream)
        try:
            result = await original(sdk_self, *args, **kwargs)
        except BaseException as exc:
            call.finish(Usage(), exc)
            raise
        if stream:
            return AsyncStreamProxy(result, call)
        call.finish(_safe(lambda: spec.usage(result), Usage()))
        return result

    wrapper.__aiwatch_original__ = original  # type: ignore[attr-defined]
    return wrapper


@dataclass(frozen=True)
class Patch:
    owner: type
    attr: str
    spec: CallSpec
    is_async: bool


_applied: list[tuple[type, str, Any]] = []


def apply(patches: list[Patch]) -> int:
    """Install wrappers; skips methods that are already wrapped. Returns how many were applied."""
    count = 0
    for p in patches:
        current = p.owner.__dict__.get(p.attr)
        if current is None or hasattr(current, "__aiwatch_original__"):
            continue
        wrapped = wrap_async(current, p.spec) if p.is_async else wrap_sync(current, p.spec)
        setattr(p.owner, p.attr, wrapped)
        _applied.append((p.owner, p.attr, current))
        count += 1
    return count


def revert() -> None:
    while _applied:
        owner, attr, original = _applied.pop()
        setattr(owner, attr, original)


def int_or_none(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
