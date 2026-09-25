# aiwatch

Local-first LLM usage and cost tracking for Python. It wraps the official OpenAI, Anthropic and Gemini SDKs and records
tokens, latency, errors and estimated cost for every call into a local SQLite file. There's no server and no account.

```bash
aiwatch run app.py --tag env=dev      # run any script with tracking switched on
aiwatch report --since 7d --group-by model,feature
```

> Status: 0.1.0, not yet published to PyPI. **The name `aiwatch` must be checked for availability on PyPI before publishing.**

## Problem
If your code calls several LLM providers, it's hard to answer simple questions. Which model or feature is costing money?
How slow is it? How often does it fail? The provider dashboards each show one provider and have no idea what your
"summarise" feature is.

## Why it exists
Tools like **Langfuse**, **Helicone**, **OpenLLMetry** and **LiteLLM** already do LLM observability well, but they are platforms:
a server, a proxy or an OpenTelemetry backend. aiwatch is deliberately small. It's `pip install` plus one line,
stores to SQLite, and prints a report in your terminal. It's aimed at scripts, notebooks, CI jobs and small services.
It is not a Langfuse competitor. It's also a learning project in SDK instrumentation and token/cost accounting.

## Architecture
```
your code ──► OpenAI / Anthropic / Gemini SDK method
                 │   (patched by aiwatch.instrument(): same call, same return value)
                 ▼
          CallSpec (per provider: read model, usage, stream events)
                 │
                 ▼
          Recorder ──► PriceTable (versioned, per provider/model/date)
                 │
                 ▼
          Sink: SQLiteSink (WAL) | JSONLSink | MemorySink
                 │
     CLI: run / report / export           Dashboard: FastAPI + Chart.js on 127.0.0.1
```
- **Instrumentation** (`instrument/_wrap.py`) patches SDK *class* methods, so it covers every client instance, including
  ones created before `instrument()` ran. Streams are wrapped in a proxy that passes each chunk through unchanged and
  records once when the stream ends, whether it's exhausted, fails, gets closed, or is abandoned after `break`.
- **Provider modules** (`instrument/openai.py`, `anthropic.py`, `gemini.py`) only describe how to *read* each SDK.
  All the timing, error capture and patching logic lives in one place.
- **Recorder** never raises. If writing fails, the user's call still returns normally.

## Features
- OpenAI `chat.completions.create` and `responses.create`; Anthropic `messages.create`; Gemini `models.generate_content` and
  `generate_content_stream`. Each is covered sync and async, streaming and non-streaming.
- OpenRouter and Ollama are recognised from the OpenAI client's `base_url`.
- Normalised tokens: `prompt_tokens`, `completion_tokens`, `cached_tokens`. Anthropic cache tokens and Gemini thinking tokens are
  folded in consistently (see the module docstrings).
- A missing token count is stored as `NULL`, never guessed. A model with no price entry has an **unknown** cost, never $0.
- Tags via `with aiwatch.tags(feature="x")` or `@aiwatch.track(feature="x")`. They're backed by `ContextVar`, so they stay correct across asyncio tasks.
- Every record stores the `price_version` used, so historical costs can be reproduced after prices change.
- Optional **OpenTelemetry export** (`pip install aiwatch[otel]`): one CLIENT span per call, named `{operation} {model}` with
  `gen_ai.*` attributes from the GenAI semantic conventions, plus cost, TTFT and tags under `aiwatch.*`. Works with Jaeger,
  Tempo or any OTLP backend. Checked against a local Jaeger v2 (see `examples/otel_jaeger.py`).

## Tech stack
Python 3.12+, Typer (CLI), FastAPI + uvicorn + Chart.js (dashboard), SQLite (stdlib), Pydantic v2, PyYAML.
The provider SDKs are *not* dependencies; aiwatch patches whichever ones are installed.

## Quick start
```bash
pip install aiwatch                # after publishing; for now: uv sync in this repo
aiwatch run examples/offline_demo.py --tag env=demo     # no API key needed
aiwatch report --group-by model,feature,env
aiwatch ui                                               # http://127.0.0.1:8765
```

## API usage
```python
import aiwatch

aiwatch.instrument()                         # patch every installed SDK

with aiwatch.tags(feature="summarise", user_tier="free"):
    client.chat.completions.create(model="...", messages=[...])

@aiwatch.track(feature="rerank")
async def rerank(...): ...

# In tests or notebooks:
sink = aiwatch.MemorySink()
aiwatch.configure(sink=sink)

# SQLite for reports *and* OpenTelemetry spans (the app sets up its own TracerProvider/exporter):
from aiwatch.otel import OTelSink
aiwatch.configure(sink=aiwatch.TeeSink(aiwatch.SQLiteSink(path), OTelSink()))
```
Real prices are **not** bundled, because they change and stale numbers mislead. Copy them from each provider's pricing page into your own
file (the format is in `src/aiwatch/data/prices.yaml`) and set `AIWATCH_PRICES=./prices.yaml`. Then check it with `aiwatch prices validate prices.yaml`.

| Variable | Meaning |
|---|---|
| `AIWATCH_DB` | SQLite path (default `.aiwatch/aiwatch.db`) |
| `AIWATCH_PRICES` | your price YAML |
| `AIWATCH_DEBUG` | log aiwatch's own errors |
| `AIWATCH_OTEL` | `1` = also emit OpenTelemetry spans from the default sink (needs `aiwatch[otel]`) |

## Demo
```
$ aiwatch report -g model,feature,env
model       feature    env   calls  err%  in_tok  out_tok  est_cost_usd  p50_ms  p95_ms
demo-small  summarise  demo  5      0.0   6000    1500     0.009000      0       11
demo-large  classify   demo  2      0.0   2400    600      0.003600      0       0
```
(The output above is from the offline demo, using the bundled FAKE demo prices.)

## Testing
`make check` runs ruff, `mypy --strict` and 44 pytest tests. The tests run the **real** provider SDKs against an injected
`MockTransport`, so they're fully offline, and the request/response parsing is the SDK's own. openai ≥ 3 and anthropic ≥ 1 use
`httpx2`, and google-genai uses `httpx`; both support mock transports.

## Deployment
It's a library, so there's nothing to host. `.github/workflows/release.yml` publishes to PyPI with trusted publishing when a `v*`
tag is pushed (not done yet). A `Dockerfile` is included for running the dashboard over a mounted database.

## Security
- Prompts and completions are **never stored**; only metadata is.
- API keys are never read or logged by aiwatch.
- The dashboard binds to `127.0.0.1` only.

## Performance
Measured on one laptop (see [docs/benchmarks.md](docs/benchmarks.md)): ≈ 61 µs overhead per non-streaming call with the
in-memory sink, and ≈ 108 µs with SQLite. That's small next to real LLM latency. Streaming overhead hasn't been measured yet (TBD).

## Engineering trade-offs
- **Monkey-patching vs. a proxy.** Patching needs no infrastructure and keeps call sites unchanged, but it's tied to SDK internals
  (method paths). That's mitigated by one test per wrapped method and by debug-logging (not crashing) if a path moves.
- **Patching classes vs. instances.** Class-level patching covers every client, but it's global to the process.
- **SQLite by default.** It needs no setup and supports concurrent readers (WAL). A single writer lock is fine for one process, but not
  for heavy multi-process writing.
- **Unknown over wrong.** Missing tokens or prices stay NULL and are excluded from totals, and the report marks this with `*`.

## Limitations
- Not wrapped: Anthropic's `messages.stream()` helper, OpenAI embeddings/images/audio, batch APIs.
- OpenAI chat streams report usage only with `stream_options={"include_usage": True}`.
- Wrapped method paths depend on SDK versions (tested: openai 3.19, anthropic 1.8, google-genai 2.25).
- `prices update` (fetching prices automatically) isn't implemented; prices are entered by hand on purpose.
- OTel spans are emitted *after* the call finishes (with the real start/end timestamps), so they aren't the active context during
  the request: HTTP spans from other instrumentation appear next to the aiwatch span, not nested under it.

## Roadmap
- Measuring streaming overhead and multi-threaded SQLite throughput
- Embeddings calls
- Publishing 0.1.0 to PyPI

## Contributing
See [CONTRIBUTING.md](CONTRIBUTING.md). Licence: MIT.
