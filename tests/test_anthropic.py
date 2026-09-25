from __future__ import annotations

from decimal import Decimal

import anthropic
import pytest

from aiwatch import MemorySink
from tests.mock_http import Router
from tests.sse import sse

MESSAGE_JSON = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "claude-test",
    "content": [{"type": "text", "text": "hi"}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 0,
    },
}

STREAM_EVENTS: list[tuple[str | None, object]] = [
    (
        "message_start",
        {
            "type": "message_start",
            "message": {
                **MESSAGE_JSON,
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 20, "output_tokens": 1},
            },
        },
    ),
    (
        "content_block_start",
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    ),
    (
        "content_block_delta",
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}},
    ),
    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
    (
        "message_delta",
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 7},
        },
    ),
    ("message_stop", {"type": "message_stop"}),
]


def client(router: Router) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key="k", max_retries=0, http_client=router.httpx2_client())


def test_messages_normalises_cache_tokens(sink: MemorySink) -> None:
    router = Router()
    router.add("/v1/messages", body=MESSAGE_JSON)
    client(router).messages.create(
        model="claude-test", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    [rec] = sink.records
    assert rec.provider == "anthropic"
    # prompt = input 100 + cache_read 900; cached = 900
    assert (rec.prompt_tokens, rec.completion_tokens, rec.cached_tokens) == (1000, 50, 900)
    # 100 * $3 + 900 * $0.30 + 50 * $15, per 1M
    assert rec.est_cost_usd == Decimal("0.00132")


def test_messages_stream(sink: MemorySink) -> None:
    router = Router()
    router.add("/v1/messages", sse=sse(STREAM_EVENTS))
    stream = client(router).messages.create(
        model="claude-test",
        max_tokens=10,
        messages=[{"role": "user", "content": "hi"}],
        stream=True,
    )
    kinds = [event.type for event in stream]
    assert kinds[0] == "message_start" and kinds[-1] == "message_stop"
    [rec] = sink.records
    assert rec.stream and (rec.prompt_tokens, rec.completion_tokens) == (20, 7)


async def test_async_messages(sink: MemorySink) -> None:
    router = Router()
    router.add("/v1/messages", body=MESSAGE_JSON)
    aclient = anthropic.AsyncAnthropic(
        api_key="k", max_retries=0, http_client=router.httpx2_async_client()
    )
    await aclient.messages.create(
        model="claude-test", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
    )
    assert sink.records[0].completion_tokens == 50


def test_error(sink: MemorySink) -> None:
    router = Router()
    router.add(
        "/v1/messages",
        status=400,
        body={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
    )
    with pytest.raises(anthropic.BadRequestError):
        client(router).messages.create(
            model="claude-test", max_tokens=10, messages=[{"role": "user", "content": "hi"}]
        )
    assert sink.records[0].error_type == "BadRequestError"
