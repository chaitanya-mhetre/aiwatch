"""OpenTelemetry sink: one CLIENT span per LLM call, named and attributed per the GenAI
semantic conventions, so the calls show up in Jaeger, Tempo, Honeycomb or any OTLP backend.

Only ``opentelemetry-api`` is needed here (``pip install aiwatch[otel]``). The application owns the
SDK setup (TracerProvider + exporter); if it hasn't configured one, the API's no-op tracer is used
and this sink costs almost nothing.

Why spans are created *after* the call: aiwatch records a call once it has finished (that's when
token counts are known). The span is therefore opened and closed in one go with explicit
timestamps (``start = end - latency``), rather than wrapped around the live request. The effect in
a trace viewer is the same bar at the same place; the difference is that the span is not the
active context during the call, so HTTP spans from other instrumentation won't nest under it.
"""

from __future__ import annotations

from datetime import datetime

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from aiwatch.record import CallRecord

# The scalar subset of OTel's AttributeValue that this sink emits.
type AttributeValue = str | bool | int | float

INSTRUMENTATION_NAME = "aiwatch"

# aiwatch provider id -> gen_ai.provider.name well-known value (others pass through unchanged).
_PROVIDER_NAMES = {"gemini": "gcp.gemini"}


def _ns(ts: datetime) -> int:
    return int(ts.timestamp() * 1_000_000_000)


def span_attributes(record: CallRecord) -> dict[str, AttributeValue]:
    """Map a CallRecord to span attributes.

    ``gen_ai.*`` keys follow the OTel GenAI semantic conventions. Values the conventions don't
    cover (cost, time to first token, tags) go under an ``aiwatch.*`` namespace so they can't clash.
    Missing token counts are *omitted*, never sent as 0: 0 would be a false measurement.
    """
    attrs: dict[str, AttributeValue] = {
        "gen_ai.provider.name": _PROVIDER_NAMES.get(record.provider, record.provider),
        "gen_ai.operation.name": record.operation,
        "gen_ai.request.model": record.model,
        "aiwatch.stream": record.stream,
        "aiwatch.latency_ms": record.latency_ms,
    }
    if record.prompt_tokens is not None:
        attrs["gen_ai.usage.input_tokens"] = record.prompt_tokens
    if record.completion_tokens is not None:
        attrs["gen_ai.usage.output_tokens"] = record.completion_tokens
    if record.cached_tokens is not None:
        attrs["gen_ai.usage.cache_read.input_tokens"] = record.cached_tokens
    if record.error_type is not None:
        attrs["error.type"] = record.error_type
    if record.ttft_ms is not None:
        attrs["aiwatch.ttft_ms"] = record.ttft_ms
    if record.est_cost_usd is not None:
        # Attributes can't hold Decimal; the string keeps full precision for exact sums later.
        attrs["aiwatch.est_cost_usd"] = str(record.est_cost_usd)
    if record.price_version is not None:
        attrs["aiwatch.price_version"] = record.price_version
    if record.run_id is not None:
        attrs["aiwatch.run_id"] = record.run_id
    for key, value in record.tags.items():
        attrs[f"aiwatch.tag.{key}"] = value
    return attrs


class OTelSink:
    """Emit each CallRecord as a finished span.

    ``tracer_provider`` defaults to the global one (whatever the app configured with
    ``trace.set_tracer_provider``). Pass one explicitly in tests.
    """

    def __init__(self, tracer_provider: trace.TracerProvider | None = None) -> None:
        from aiwatch import __version__

        self._tracer = trace.get_tracer(
            INSTRUMENTATION_NAME, __version__, tracer_provider=tracer_provider
        )

    def write(self, record: CallRecord) -> None:
        end = _ns(record.ts)
        start = end - record.latency_ms * 1_000_000
        span = self._tracer.start_span(
            f"{record.operation} {record.model}",
            kind=SpanKind.CLIENT,
            start_time=start,
            attributes=span_attributes(record),
        )
        if record.status == "error":
            span.set_status(Status(StatusCode.ERROR, record.error_type))
        span.end(end_time=end)

    def close(self) -> None:
        # The app owns the TracerProvider, so flushing/shutdown is its job, not ours.
        pass
