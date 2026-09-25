# Changelog

## 0.1.0 — 2026-09-25 (unreleased, not on PyPI)
- Instrumentation for OpenAI (chat completions, Responses API), Anthropic (messages) and Gemini
  (`google-genai` generate_content), sync and async, streaming and non-streaming.
- OpenRouter and Ollama detected automatically from the OpenAI client's base URL.
- Versioned price table (same format as ai-gateway); cost stays unknown when there is no entry.
- SQLite (WAL), JSONL and in-memory sinks.
- CLI: `run`, `report`, `export`, `ui`, `prices show|validate`.
- Local dashboard bound to 127.0.0.1.
