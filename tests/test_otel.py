from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import openai
import pytest
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

import aiwatch
from aiwatch import CallRecord, MemorySink, TeeSink
from aiwatch.otel import OTelSink, span_attributes
from aiwatch.recorder import default_sink
from tests.conftest import PRICES
from tests.mock_http import Router
from tests.test_openai import CHAT_JSON


@pytest.fixture
def exporter() -> InMemorySpanExporter:
    return InMemorySpanExporter()


@pytest.fixture
def provider(exporter: InMemorySpanExporter) -> TracerProvider:
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    return tp


def only_span(exporter: InMemorySpanExporter) -> ReadableSpan:
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    return spans[0]


def make_record(**overrides: object) -> CallRecord:
    fields: dict[str, object] = {
        "ts": datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC),
        "provider": "openai",
        "model": "gpt-test-1",
        "prompt_tokens": 1000,
        "completion_tokens": 500,
        "cached_tokens": 200,
        "latency_ms": 750,
        "ttft_ms": 120,
        "stream": True,
        "est_cost_usd": Decimal("0.0057"),
        "price_version": "2026-01-01",
        "tags": {"feature": "summarise"},
        "run_id": "r1",
    }
    fields.update(overrides)
    return CallRecord.model_validate(fields)


def test_attributes_follow_genai_conventions() -> None:
    attrs = span_attributes(make_record())
    assert attrs["gen_ai.provider.name"] == "openai"
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.request.model"] == "gpt-test-1"
    assert attrs["gen_ai.usage.input_tokens"] == 1000
    assert attrs["gen_ai.usage.output_tokens"] == 500
    assert attrs["gen_ai.usage.cache_read.input_tokens"] == 200
    assert attrs["aiwatch.est_cost_usd"] == "0.0057"
    assert attrs["aiwatch.ttft_ms"] == 120
    assert attrs["aiwatch.tag.feature"] == "summarise"
    assert "error.type" not in attrs


def test_unknown_tokens_are_omitted_not_zero() -> None:
    attrs = span_attributes(
        make_record(prompt_tokens=None, completion_tokens=None, cached_tokens=None)
    )
    assert "gen_ai.usage.input_tokens" not in attrs
    assert "gen_ai.usage.output_tokens" not in attrs


def test_gemini_maps_to_well_known_provider_name() -> None:
    assert span_attributes(make_record(provider="gemini"))["gen_ai.provider.name"] == "gcp.gemini"


def test_sink_emits_client_span_with_call_timing(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    OTelSink(provider).write(make_record())
    span = only_span(exporter)
    assert span.name == "chat gpt-test-1"
    assert span.kind is SpanKind.CLIENT
    assert span.instrumentation_scope is not None
    assert span.instrumentation_scope.name == "aiwatch"
    assert span.start_time is not None and span.end_time is not None
    assert span.end_time - span.start_time == 750 * 1_000_000
    assert span.status.status_code is StatusCode.UNSET


def test_error_call_marks_span_status(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    OTelSink(provider).write(make_record(status="error", error_type="RateLimitError"))
    span = only_span(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes["error.type"] == "RateLimitError"


def test_end_to_end_instrumented_call_produces_record_and_span(
    provider: TracerProvider, exporter: InMemorySpanExporter
) -> None:
    memory = MemorySink()
    aiwatch.configure(sink=TeeSink(memory, OTelSink(provider)), prices=PRICES)
    aiwatch.instrument()
    try:
        router = Router()
        router.add("/chat/completions", body=CHAT_JSON)
        client = openai.OpenAI(api_key="sk-test", max_retries=0, http_client=router.httpx2_client())
        client.chat.completions.create(
            model="gpt-test-1", messages=[{"role": "user", "content": "hello"}]
        )
    finally:
        aiwatch.uninstrument()
    assert len(memory.records) == 1
    span = only_span(exporter)
    assert span.attributes is not None
    assert span.attributes["gen_ai.usage.input_tokens"] == 1000
    assert span.attributes["aiwatch.est_cost_usd"] == str(memory.records[0].est_cost_usd)


class _Boom:
    def write(self, record: CallRecord) -> None:
        raise RuntimeError("sink down")

    def close(self) -> None:
        raise RuntimeError("sink down")


def test_tee_keeps_going_when_one_sink_fails() -> None:
    memory = MemorySink()
    tee = TeeSink(_Boom(), memory)
    tee.write(make_record())
    tee.close()
    assert len(memory.records) == 1


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    monkeypatch.setenv("AIWATCH_DB", str(tmp_path / "a.db"))
    yield monkeypatch


def test_default_sink_is_sqlite_only_without_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.delenv("AIWATCH_OTEL", raising=False)
    sink = default_sink()
    try:
        assert isinstance(sink, aiwatch.SQLiteSink)
    finally:
        sink.close()


def test_default_sink_adds_otel_with_env(clean_env: pytest.MonkeyPatch) -> None:
    clean_env.setenv("AIWATCH_OTEL", "1")
    sink = default_sink()
    try:
        assert isinstance(sink, TeeSink)
        assert any(isinstance(s, OTelSink) for s in sink.sinks)
    finally:
        sink.close()
