from __future__ import annotations

from decimal import Decimal

import openai
import pytest

from aiwatch import MemorySink, tags
from tests.mock_http import Router
from tests.sse import sse

CHAT_JSON = {
    "id": "chatcmpl-1",
    "object": "chat.completion",
    "created": 1,
    "model": "gpt-test-1",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}
    ],
    "usage": {
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "total_tokens": 1500,
        "prompt_tokens_details": {"cached_tokens": 200},
    },
}


def chunk(content: str | None, usage: dict[str, int] | None = None) -> dict[str, object]:
    choices = [] if content is None else [{"index": 0, "delta": {"content": content}}]
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "gpt-test-1",
        "choices": choices,
        "usage": usage,
    }


def client(router: Router, base_url: str | None = None) -> openai.OpenAI:
    return openai.OpenAI(
        api_key="sk-test", base_url=base_url, max_retries=0, http_client=router.httpx2_client()
    )


def aclient(router: Router) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="sk-test", max_retries=0, http_client=router.httpx2_async_client()
    )


def test_chat_non_streaming_records_tokens_and_cost(sink: MemorySink) -> None:
    router = Router()
    router.add("/chat/completions", body=CHAT_JSON)
    with tags(feature="summarise"):
        resp = client(router).chat.completions.create(
            model="gpt-test-1", messages=[{"role": "user", "content": "hello"}]
        )
    assert resp.choices[0].message.content == "hi"  # response untouched

    [rec] = sink.records
    assert (rec.provider, rec.model, rec.operation) == ("openai", "gpt-test-1", "chat")
    assert (rec.prompt_tokens, rec.completion_tokens, rec.cached_tokens) == (1000, 500, 200)
    # 800 uncached * $2 + 200 cached * $0.50 + 500 out * $8, per 1M tokens
    assert rec.est_cost_usd == Decimal("0.0057")
    assert rec.price_version == "openai/gpt-test*@2026-01-01"
    assert rec.tags == {"feature": "summarise"}
    assert rec.run_id == "test-run"
    assert rec.status == "ok" and not rec.stream


def test_chat_stream_with_usage(sink: MemorySink) -> None:
    body = sse(
        [
            (None, chunk("he")),
            (None, chunk("llo")),
            (None, chunk(None, {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12})),
        ],
        done=True,
    )
    router = Router()
    router.add("/chat/completions", sse=body)
    stream = client(router).chat.completions.create(
        model="gpt-test-1",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    text = "".join(c.choices[0].delta.content or "" for c in stream if c.choices)
    assert text == "hello"

    [rec] = sink.records
    assert rec.stream
    assert (rec.prompt_tokens, rec.completion_tokens) == (10, 2)
    assert rec.ttft_ms is not None


def test_chat_stream_without_usage_records_none_not_a_guess(sink: MemorySink) -> None:
    router = Router()
    router.add(
        "/chat/completions",
        sse=sse([(None, chunk("x"))], done=True),
    )
    for _ in client(router).chat.completions.create(
        model="gpt-test-1", messages=[{"role": "user", "content": "hi"}], stream=True
    ):
        pass
    [rec] = sink.records
    assert rec.prompt_tokens is None and rec.est_cost_usd is None


def test_early_break_still_records_once(sink: MemorySink) -> None:
    router = Router()
    router.add(
        "/chat/completions",
        sse=sse([(None, chunk("a")), (None, chunk("b"))], done=True),
    )
    with client(router).chat.completions.create(
        model="gpt-test-1", messages=[{"role": "user", "content": "hi"}], stream=True
    ) as stream:
        for _ in stream:
            break
    assert len(sink.records) == 1


def test_api_error_is_recorded_and_reraised(sink: MemorySink) -> None:
    router = Router()
    router.add(
        "/chat/completions",
        status=429,
        body={"error": {"message": "slow down", "type": "rate_limit"}},
    )
    with pytest.raises(openai.RateLimitError):
        client(router).chat.completions.create(
            model="gpt-test-1", messages=[{"role": "user", "content": "hi"}]
        )
    [rec] = sink.records
    assert rec.status == "error" and rec.error_type == "RateLimitError"


async def test_async_chat(sink: MemorySink) -> None:
    router = Router()
    router.add("/chat/completions", body=CHAT_JSON)
    await aclient(router).chat.completions.create(
        model="gpt-test-1", messages=[{"role": "user", "content": "hi"}]
    )
    [rec] = sink.records
    assert rec.prompt_tokens == 1000


async def test_async_chat_stream(sink: MemorySink) -> None:
    router = Router()
    router.add(
        "/chat/completions",
        sse=sse(
            [
                (None, chunk("a")),
                (
                    None,
                    chunk(None, {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}),
                ),
            ],
            done=True,
        ),
    )
    stream = await aclient(router).chat.completions.create(
        model="gpt-test-1",
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
        stream_options={"include_usage": True},
    )
    async for _ in stream:
        pass
    [rec] = sink.records
    assert rec.stream and rec.completion_tokens == 1


RESPONSE_JSON = {
    "id": "resp_1",
    "object": "response",
    "created_at": 1,
    "model": "gpt-test-1",
    "status": "completed",
    "output": [],
    "parallel_tool_calls": True,
    "tool_choice": "auto",
    "tools": [],
    "usage": {
        "input_tokens": 40,
        "output_tokens": 10,
        "total_tokens": 50,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens_details": {"reasoning_tokens": 0},
    },
}


def test_responses_api(sink: MemorySink) -> None:
    router = Router()
    router.add("/responses", body=RESPONSE_JSON)
    client(router).responses.create(model="gpt-test-1", input="hi")
    [rec] = sink.records
    assert rec.operation == "responses"
    assert (rec.prompt_tokens, rec.completion_tokens, rec.cached_tokens) == (40, 10, 0)


@pytest.mark.parametrize(
    ("base_url", "provider"),
    [
        ("https://openrouter.ai/api/v1", "openrouter"),
        ("http://localhost:11434/v1", "ollama"),
        ("https://example.internal/v1", "openai-compatible"),
    ],
)
def test_openai_compatible_providers_detected_from_base_url(
    sink: MemorySink, base_url: str, provider: str
) -> None:
    router = Router()
    router.add("/chat/completions", body=CHAT_JSON)
    client(router, base_url).chat.completions.create(
        model="some-model", messages=[{"role": "user", "content": "hi"}]
    )
    [rec] = sink.records
    assert rec.provider == provider
    assert rec.est_cost_usd is None  # no price entry -> unknown, not free


def test_uninstrument_restores_original() -> None:
    from openai.resources.chat.completions import Completions

    import aiwatch

    aiwatch.instrument()
    assert hasattr(Completions.create, "__aiwatch_original__")
    aiwatch.instrument()  # idempotent: no double wrapping
    assert not hasattr(Completions.create.__aiwatch_original__, "__aiwatch_original__")
    aiwatch.uninstrument()
    assert not hasattr(Completions.create, "__aiwatch_original__")
