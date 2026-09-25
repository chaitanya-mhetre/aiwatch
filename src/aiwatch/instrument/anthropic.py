"""Anthropic SDK: ``messages.create``, sync and async, streaming and non-streaming.

Anthropic reports ``input_tokens`` *excluding* cache reads/writes. aiwatch normalises to
``prompt_tokens = input + cache_read + cache_creation`` and ``cached_tokens = cache_read`` so the
numbers mean the same thing as for the other providers.

Not wrapped: the ``messages.stream(...)`` helper, which bypasses ``create``. Use
``messages.create(stream=True)`` if you want those calls tracked.
"""

from __future__ import annotations

from typing import Any

from aiwatch.instrument._wrap import CallSpec, Patch, Usage, int_or_none


def _prompt_side(usage: Any) -> tuple[int | None, int | None]:
    base = int_or_none(getattr(usage, "input_tokens", None))
    if base is None:
        return None, None
    read = int_or_none(getattr(usage, "cache_read_input_tokens", None)) or 0
    created = int_or_none(getattr(usage, "cache_creation_input_tokens", None)) or 0
    return base + read + created, read


def _usage(resp: Any) -> Usage:
    usage = getattr(resp, "usage", None)
    if usage is None:
        return Usage()
    prompt, cached = _prompt_side(usage)
    return Usage(prompt, int_or_none(getattr(usage, "output_tokens", None)), cached)


class StreamAccumulator:
    """``message_start`` carries the input side; ``message_delta`` the running output count."""

    def __init__(self) -> None:
        self._prompt: int | None = None
        self._cached: int | None = None
        self._output: int | None = None

    def feed(self, event: Any) -> None:
        kind = getattr(event, "type", None)
        if kind == "message_start":
            usage = event.message.usage
            self._prompt, self._cached = _prompt_side(usage)
            self._output = int_or_none(getattr(usage, "output_tokens", None))
        elif kind == "message_delta" and getattr(event, "usage", None) is not None:
            self._output = int_or_none(getattr(event.usage, "output_tokens", None))

    def usage(self) -> Usage:
        return Usage(self._prompt, self._output, self._cached)


MESSAGES = CallSpec(
    operation="chat",
    provider=lambda _resource: "anthropic",
    model=lambda kwargs: str(kwargs.get("model", "unknown")),
    usage=_usage,
    accumulator=StreamAccumulator,
)


def patches() -> list[Patch]:
    from anthropic.resources import messages as m

    return [
        Patch(m.Messages, "create", MESSAGES, is_async=False),
        Patch(m.AsyncMessages, "create", MESSAGES, is_async=True),
    ]
