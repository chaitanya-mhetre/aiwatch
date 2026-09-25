"""OpenAI SDK (also covers OpenRouter and Ollama, which speak the OpenAI API).

Wrapped methods: ``chat.completions.create`` and ``responses.create``, sync and async, streaming
and non-streaming.

Streaming note: chat-completion streams only carry usage if the caller passes
``stream_options={"include_usage": True}``. Without it aiwatch records the call with ``None``
tokens instead of guessing.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from aiwatch.instrument._wrap import CallSpec, Patch, Usage, int_or_none


def provider_from_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    if host.endswith("openrouter.ai"):
        return "openrouter"
    if parsed.port == 11434 or "ollama" in host:
        return "ollama"
    if host.endswith("openai.com"):
        return "openai"
    return "openai-compatible"


def _provider(resource: Any) -> str:
    return provider_from_base_url(str(resource._client.base_url))


def _model(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("model", "unknown"))


# --- chat.completions ---------------------------------------------------------------------------


def _chat_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    details = getattr(usage, "prompt_tokens_details", None)
    return Usage(
        int_or_none(getattr(usage, "prompt_tokens", None)),
        int_or_none(getattr(usage, "completion_tokens", None)),
        int_or_none(getattr(details, "cached_tokens", None)) if details else None,
    )


class ChatStreamAccumulator:
    def __init__(self) -> None:
        self._usage = Usage()

    def feed(self, chunk: Any) -> None:
        if getattr(chunk, "usage", None) is not None:  # only the final chunk carries usage
            self._usage = _chat_usage(chunk.usage)

    def usage(self) -> Usage:
        return self._usage


CHAT = CallSpec(
    operation="chat",
    provider=_provider,
    model=_model,
    usage=lambda resp: _chat_usage(getattr(resp, "usage", None)),
    accumulator=ChatStreamAccumulator,
)


# --- responses ----------------------------------------------------------------------------------


def _responses_usage(usage: Any) -> Usage:
    if usage is None:
        return Usage()
    details = getattr(usage, "input_tokens_details", None)
    return Usage(
        int_or_none(getattr(usage, "input_tokens", None)),
        int_or_none(getattr(usage, "output_tokens", None)),
        int_or_none(getattr(details, "cached_tokens", None)) if details else None,
    )


class ResponsesStreamAccumulator:
    def __init__(self) -> None:
        self._usage = Usage()

    def feed(self, event: Any) -> None:
        if getattr(event, "type", None) == "response.completed":
            self._usage = _responses_usage(getattr(event.response, "usage", None))

    def usage(self) -> Usage:
        return self._usage


RESPONSES = CallSpec(
    operation="responses",
    provider=_provider,
    model=_model,
    usage=lambda resp: _responses_usage(getattr(resp, "usage", None)),
    accumulator=ResponsesStreamAccumulator,
)


def patches() -> list[Patch]:
    from openai.resources import responses as r
    from openai.resources.chat import completions as c

    return [
        Patch(c.Completions, "create", CHAT, is_async=False),
        Patch(c.AsyncCompletions, "create", CHAT, is_async=True),
        Patch(r.Responses, "create", RESPONSES, is_async=False),
        Patch(r.AsyncResponses, "create", RESPONSES, is_async=True),
    ]
