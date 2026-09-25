"""Measure aiwatch's overhead on *streaming* calls.

Method: stream an OpenAI chat completion of C content chunks (plus a final usage chunk) from an
in-process mock transport, consume it fully, and time the whole call + iteration. Do that N times
uninstrumented, instrumented with MemorySink, and instrumented with SQLiteSink; compare medians.
Sync and async are measured separately because they go through different proxies.

For streams the extra work is: the proxy's ``__next__`` per chunk (TTFT check + accumulator
``feed``), then one record when the stream ends. So the script also reports overhead per chunk.

    uv run python benchmarks/streaming.py [N] [CHUNKS] [ROUNDS]
"""

from __future__ import annotations

import asyncio
import json
import platform
import statistics
import sys
import tempfile
import time
from pathlib import Path

import httpx2
import openai

import aiwatch


def sse_body(chunks: int) -> bytes:
    def event(payload: dict[str, object]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    base = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "gpt-bench"}
    parts = [
        event({**base, "choices": [{"index": 0, "delta": {"content": "tok "}}]})
        for _ in range(chunks)
    ]
    usage = {"prompt_tokens": 10, "completion_tokens": chunks, "total_tokens": 10 + chunks}
    parts.append(event({**base, "choices": [], "usage": usage}))
    parts.append("data: [DONE]\n\n")
    return "".join(parts).encode()


def _handler(body: bytes) -> httpx2.MockTransport:
    return httpx2.MockTransport(
        lambda req: httpx2.Response(
            200, content=body, headers={"content-type": "text/event-stream"}
        )
    )


KWARGS: dict[str, object] = {
    "model": "gpt-bench",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
    "stream_options": {"include_usage": True},
}


def _sync_client(body: bytes) -> openai.OpenAI:
    return openai.OpenAI(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=_handler(body))
    )


def _async_client(body: bytes) -> openai.AsyncOpenAI:
    return openai.AsyncOpenAI(
        api_key="x", max_retries=0, http_client=httpx2.AsyncClient(transport=_handler(body))
    )


def sync_samples(client: openai.OpenAI, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        for _chunk in client.chat.completions.create(**KWARGS):  # type: ignore[call-overload]
            pass
        samples.append((time.perf_counter() - start) * 1e6)
    return samples


async def _async_samples(client: openai.AsyncOpenAI, n: int) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        stream = await client.chat.completions.create(**KWARGS)  # type: ignore[call-overload]
        async for _chunk in stream:
            pass
        samples.append((time.perf_counter() - start) * 1e6)
    return samples


CONFIGS = ("uninstrumented", "memory", "sqlite")


def _apply(config: str, tmp: Path) -> None:
    aiwatch.uninstrument()
    if config == "memory":
        # A fresh sink per block, so a growing list doesn't add GC pressure to later blocks.
        aiwatch.configure(sink=aiwatch.MemorySink())
        aiwatch.instrument()
    elif config == "sqlite":
        aiwatch.configure(sink=aiwatch.SQLiteSink(tmp / "bench.db"))
        aiwatch.instrument()


def measure(mode: str, n: int, rounds: int, body: bytes, chunks: int) -> None:
    """Interleave the three configurations in ``rounds`` blocks (A B C A B C ...) so that machine
    load drifting over time hits all of them equally, then compare medians of all samples."""
    samples: dict[str, list[float]] = {c: [] for c in CONFIGS}
    per_block = max(1, n // rounds)
    with tempfile.TemporaryDirectory() as tmp:
        sync_client, async_client = _sync_client(body), _async_client(body)
        loop = asyncio.new_event_loop()

        def run(k: int) -> list[float]:
            if mode == "sync":
                return sync_samples(sync_client, k)
            return loop.run_until_complete(_async_samples(async_client, k))

        run(max(50, per_block))  # warm-up
        for _ in range(rounds):
            for config in CONFIGS:
                _apply(config, Path(tmp))
                samples[config] += run(per_block)
        aiwatch.uninstrument()
        aiwatch.get_recorder().close()
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()
    med = {c: statistics.median(v) for c, v in samples.items()}
    base = med["uninstrumented"]
    print(
        f"[{mode}] median per streamed call ({chunks} chunks + usage chunk), "
        f"{rounds} interleaved rounds x {per_block} calls per config:"
    )
    for c in CONFIGS:
        print(f"  {c:<15} {med[c]:9.1f} us")
    for c in ("memory", "sqlite"):
        d = med[c] - base
        print(f"  overhead {c:<7} {d:9.1f} us  ({d / chunks:.2f} us/chunk)")


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    chunks = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 20
    body = sse_body(chunks)
    print(
        f"python {platform.python_version()} on {platform.machine()} ({platform.system()}), "
        f"openai {openai.__version__}, n={n}, chunks={chunks}"
    )
    measure("sync", n, rounds, body, chunks)
    measure("async", n, rounds, body, chunks)


if __name__ == "__main__":
    main()
