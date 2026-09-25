# Changelog

## Unreleased
- OpenTelemetry sink (`aiwatch.otel.OTelSink`, extra `aiwatch[otel]`): one CLIENT span per call with GenAI
  semantic-convention attributes (`gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*`, `error.type`) and
  `aiwatch.*` extras (cost, TTFT, tags). Verified against a local Jaeger v2.
- `TeeSink` to fan records out to several sinks; a failing sink doesn't stop the others.
- `AIWATCH_OTEL=1` makes the default sink SQLite + OTel.
- `benchmarks/streaming.py`: streaming overhead, sync and async, interleaved rounds. Results in docs/benchmarks.md.

## 0.1.0 — 2026-09-25 (unreleased, not on PyPI)
- Instrumentation for OpenAI (chat completions, Responses API), Anthropic (messages) and Gemini
  (`google-genai` generate_content), sync and async, streaming and non-streaming.
- OpenRouter and Ollama detected automatically from the OpenAI client's base URL.
- Versioned price table (same format as ai-gateway); cost stays unknown when there is no entry.
- SQLite (WAL), JSONL and in-memory sinks.
- CLI: `run`, `report`, `export`, `ui`, `prices show|validate`.
- Local dashboard bound to 127.0.0.1.
