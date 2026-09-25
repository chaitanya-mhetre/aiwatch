"""Send aiwatch spans to a local Jaeger over OTLP/HTTP. Fully offline: the LLM is a mock transport.

    docker run --rm -d --name aiwatch-jaeger -p 16686:16686 -p 4318:4318 \
        jaegertracing/jaeger:latest
    uv run python examples/otel_jaeger.py

Then open http://localhost:16686 and pick the service "aiwatch-demo".

Override the collector with OTEL_EXPORTER_OTLP_ENDPOINT (default http://localhost:4318).
"""

from __future__ import annotations

import json

import httpx2
import openai
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

import aiwatch
from aiwatch.otel import OTelSink

BODY = json.dumps(
    {
        "id": "c",
        "object": "chat.completion",
        "created": 1,
        "model": "gpt-demo",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}
        ],
        "usage": {"prompt_tokens": 42, "completion_tokens": 7, "total_tokens": 49},
    }
).encode()


def main() -> None:
    provider = TracerProvider(resource=Resource.create({"service.name": "aiwatch-demo"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)

    aiwatch.configure(sink=aiwatch.TeeSink(aiwatch.MemorySink(), OTelSink()))
    aiwatch.instrument()
    transport = httpx2.MockTransport(
        lambda req: httpx2.Response(200, content=BODY, headers={"content-type": "application/json"})
    )
    client = openai.OpenAI(
        api_key="x", max_retries=0, http_client=httpx2.Client(transport=transport)
    )
    with aiwatch.tags(feature="jaeger-demo"):
        for _ in range(3):
            client.chat.completions.create(
                model="gpt-demo", messages=[{"role": "user", "content": "hi"}]
            )
    aiwatch.uninstrument()
    provider.shutdown()  # flushes the batch processor
    print("sent 3 spans to", "service aiwatch-demo")


if __name__ == "__main__":
    main()
