"""Runs with no API key: an OpenAI client pointed at a mock transport, fully instrumented.

uv run aiwatch run examples/offline_demo.py --tag env=demo
uv run aiwatch report --group-by model,feature
"""

import json

import httpx2
import openai

import aiwatch


def fake_openai(req: httpx2.Request) -> httpx2.Response:
    model = json.loads(req.content)["model"]
    body = {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": model,
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
        ],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500},
    }
    return httpx2.Response(200, json=body)


client = openai.OpenAI(
    api_key="not-needed",
    base_url="https://api.openai.com/v1",
    http_client=httpx2.Client(transport=httpx2.MockTransport(fake_openai)),
)

aiwatch.instrument()  # a no-op under `aiwatch run`, which already instrumented everything

for feature, model, n in [("summarise", "demo-small", 5), ("classify", "demo-large", 2)]:
    with aiwatch.tags(feature=feature):
        for _ in range(n):
            client.chat.completions.create(
                model=model, messages=[{"role": "user", "content": "hi"}]
            )
print("done")
