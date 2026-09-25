"""Google Gen AI SDK (``google-genai``): ``models.generate_content`` and
``models.generate_content_stream``, sync (``client.models``) and async (``client.aio.models``).

Output tokens = ``candidates_token_count + thoughts_token_count``: thinking tokens are billed as
output, so leaving them out would under-count cost.
"""

from __future__ import annotations

from typing import Any

from aiwatch.instrument._wrap import CallSpec, Patch, Usage, int_or_none


def _model(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("model", "unknown")).removeprefix("models/")


def _usage_from_metadata(meta: Any) -> Usage:
    if meta is None:
        return Usage()
    prompt = int_or_none(getattr(meta, "prompt_token_count", None))
    candidates = int_or_none(getattr(meta, "candidates_token_count", None))
    thoughts = int_or_none(getattr(meta, "thoughts_token_count", None)) or 0
    completion = None if candidates is None else candidates + thoughts
    return Usage(prompt, completion, int_or_none(getattr(meta, "cached_content_token_count", None)))


def _usage(resp: Any) -> Usage:
    return _usage_from_metadata(getattr(resp, "usage_metadata", None))


class StreamAccumulator:
    """Each chunk's ``usage_metadata`` is cumulative, so the last one wins."""

    def __init__(self) -> None:
        self._usage = Usage()

    def feed(self, chunk: Any) -> None:
        meta = getattr(chunk, "usage_metadata", None)
        if meta is not None:
            self._usage = _usage_from_metadata(meta)

    def usage(self) -> Usage:
        return self._usage


def _spec(stream: bool) -> CallSpec:
    return CallSpec(
        operation="chat",
        provider=lambda _resource: "gemini",
        model=_model,
        usage=_usage,
        accumulator=StreamAccumulator,
        is_stream=lambda _kwargs: stream,
    )


GENERATE = _spec(stream=False)
GENERATE_STREAM = _spec(stream=True)


def patches() -> list[Patch]:
    from google.genai import models as m

    return [
        Patch(m.Models, "generate_content", GENERATE, is_async=False),
        Patch(m.Models, "generate_content_stream", GENERATE_STREAM, is_async=False),
        Patch(m.AsyncModels, "generate_content", GENERATE, is_async=True),
        # The async stream method is ``async def`` returning an async iterator: await, then wrap.
        Patch(m.AsyncModels, "generate_content_stream", GENERATE_STREAM, is_async=True),
    ]
