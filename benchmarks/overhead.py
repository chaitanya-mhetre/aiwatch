"""Measure aiwatch's per-call overhead.

Method: call ``chat.completions.create`` N times against an in-process mock transport (no network),
once without and once with instrumentation (MemorySink), and compare the median per-call time.
The difference is aiwatch's cost: wrapping, usage extraction, pricing and the sink write.

    uv run python benchmarks/overhead.py [N]
"""

from __future__ import annotations

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

BODY = json.dumps(
    {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-bench",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
    }
).encode()


def client() -> openai.OpenAI:
    transport = httpx2.MockTransport(
        lambda req: httpx2.Response(200, content=BODY, headers={"content-type": "application/json"})
    )
    return openai.OpenAI(api_key="x", max_retries=0, http_client=httpx2.Client(transport=transport))


def median_us(n: int) -> float:
    c = client()
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        c.chat.completions.create(model="gpt-bench", messages=[{"role": "user", "content": "hi"}])
        samples.append((time.perf_counter() - start) * 1e6)
    return statistics.median(samples)


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2000
    median_us(200)  # warm-up
    base = median_us(n)
    aiwatch.configure(sink=aiwatch.MemorySink())
    aiwatch.instrument()
    wrapped = median_us(n)
    with tempfile.TemporaryDirectory() as tmp:
        aiwatch.configure(sink=aiwatch.SQLiteSink(Path(tmp) / "bench.db"))
        with_sqlite = median_us(n)
        aiwatch.get_recorder().close()
    aiwatch.uninstrument()
    print(
        f"python {platform.python_version()} on {platform.machine()} ({platform.system()}), n={n}"
    )
    print(f"median per call, uninstrumented: {base:8.1f} us")
    print(f"median per call, instrumented:   {wrapped:8.1f} us")
    print(f"median per call, SQLite sink:    {with_sqlite:8.1f} us")
    print(f"overhead (MemorySink):           {wrapped - base:8.1f} us")
    print(f"overhead (SQLiteSink, WAL):      {with_sqlite - base:8.1f} us")


if __name__ == "__main__":
    main()
