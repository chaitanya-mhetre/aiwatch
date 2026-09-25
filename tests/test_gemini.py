from __future__ import annotations

from decimal import Decimal

from google import genai
from google.genai import types

from aiwatch import MemorySink
from tests.mock_http import Router
from tests.sse import sse


def response(text: str, usage: dict[str, int] | None) -> dict[str, object]:
    body: dict[str, object] = {
        "candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "index": 0}],
        "modelVersion": "gemini-test",
    }
    if usage is not None:
        body["usageMetadata"] = usage
    return body


USAGE = {
    "promptTokenCount": 1000,
    "candidatesTokenCount": 100,
    "thoughtsTokenCount": 50,
    "totalTokenCount": 1150,
}


def client(router: Router) -> genai.Client:
    return genai.Client(
        api_key="k",
        http_options=types.HttpOptions(
            httpx_client=router.httpx_client(), httpx_async_client=router.httpx_async_client()
        ),
    )


def test_generate_content_counts_thinking_as_output(sink: MemorySink) -> None:
    router = Router()
    router.add(":generateContent", body=response("hi", USAGE))
    resp = client(router).models.generate_content(model="gemini-test", contents="hello")
    assert resp.text == "hi"
    [rec] = sink.records
    assert (rec.provider, rec.model) == ("gemini", "gemini-test")
    assert (rec.prompt_tokens, rec.completion_tokens) == (1000, 150)
    assert rec.est_cost_usd == Decimal("0.0016")  # 1000 * $1 + 150 * $4, per 1M


def test_generate_content_stream_uses_last_cumulative_usage(sink: MemorySink) -> None:
    router = Router()
    router.add(
        ":streamGenerateContent",
        sse=sse(
            [
                (None, response("he", {"promptTokenCount": 10, "totalTokenCount": 10})),
                (
                    None,
                    response(
                        "llo",
                        {"promptTokenCount": 10, "candidatesTokenCount": 4, "totalTokenCount": 14},
                    ),
                ),
            ]
        ),
    )
    chunks = list(
        client(router).models.generate_content_stream(model="models/gemini-test", contents="x")
    )
    assert "".join(c.text or "" for c in chunks) == "hello"
    [rec] = sink.records
    assert rec.model == "gemini-test"  # "models/" prefix stripped
    assert rec.stream and (rec.prompt_tokens, rec.completion_tokens) == (10, 4)


async def test_async_generate_and_stream(sink: MemorySink) -> None:
    router = Router()
    router.add(":generateContent", body=response("hi", USAGE))
    router.add(":streamGenerateContent", sse=sse([(None, response("x", USAGE))]))
    aio = client(router).aio
    await aio.models.generate_content(model="gemini-test", contents="hello")
    async for _ in await aio.models.generate_content_stream(model="gemini-test", contents="x"):
        pass
    assert [r.stream for r in sink.records] == [False, True]
    assert all(r.completion_tokens == 150 for r in sink.records)
