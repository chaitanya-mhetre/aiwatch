# aiwatch
> A lightweight, local-first LLM usage and cost tracker: a Python SDK, a CLI and a small dashboard, all in one `pip install`.

## 1. Problem & why it exists
Developers who call several LLM providers (OpenAI, Anthropic, Gemini, OpenRouter, local models) rarely know which model, feature or script is costing money, how slow it is, or how often it fails.

**Existing tools in this space (be honest about them):**
- **Langfuse**: open-source LLM tracing, evals and prompt management. Powerful, but it's a full platform (a server plus a database).
- **Helicone**: a proxy-based observability platform, hosted or self-hosted.
- **OpenLLMetry (Traceloop)**: OpenTelemetry instrumentation for LLM SDKs. It needs an OTel backend to be useful.
- **LiteLLM**: a proxy and SDK with cost tracking built into its gateway.

**Differentiator:** aiwatch is deliberately small and **local-first**. There's no server and no account, and it stores to SQLite by default. Wrapping a script (`aiwatch run app.py`) or adding one decorator gives you a per-model cost/latency/error report in the terminal or a local dashboard. It is **not** a Langfuse competitor.

**Primary goal:** it's mainly a **learning project**, covering SDK instrumentation, monkey-patching and wrapping, token/cost accounting, packaging and publishing to PyPI. It's only worth publishing if it turns out genuinely useful for small scripts. Don't claim novelty beyond "zero-infrastructure".

## 2. What this proves to an employer
| Skill | Target requirement it maps to |
|---|---|
| Python packaging, typed public API, semantic versioning | Python backend roles (Zeko, EaseOps) |
| LLM provider APIs, token accounting, cost modelling | "LLM cost/performance" (EaseOps), AI Platform (Razorpay) |
| Instrumentation, observability and OpenTelemetry export | Observability (Razorpay, Microsoft) |
| CLI design and developer experience | General SWE / OSS signal |

## 3. Scope
### In scope (v1)
- Python SDK that wraps the **official** clients: `openai`, `anthropic`, `google-genai`, plus OpenRouter (OpenAI-compatible) and Ollama (OpenAI-compatible endpoint).
- For each call it records provider, model, input/output tokens, latency, streaming/non-streaming, error type, estimated cost, plus user tags (`feature="summarise"`).
- Storage: SQLite (default) or a JSONL file.
- CLI commands: `aiwatch run <script>`, `aiwatch report`, `aiwatch export --csv`, `aiwatch prices update`.
- Local dashboard: `aiwatch ui`, a single-page app served by a tiny FastAPI app.
- A versioned price table (a YAML file in the repo, with the source URL and date for each price).

### Out of scope (explicitly)
- Hosted service, multi-user auth, prompt management, evals.
- A proxy/gateway mode (that belongs to `projects/ai-gateway`).
- JavaScript SDK.

## 4. Architecture
```
user code ──► provider client (openai/anthropic/genai)
                 │  (patched / wrapped by aiwatch.instrument())
                 ▼
          aiwatch.Recorder ──► CostCalculator (price table)
                 │
                 ▼
          Sink interface ──► SQLiteSink | JSONLSink | OTelSink (optional)
                 │
       CLI (report/export)  ◄──  Dashboard (FastAPI + static HTML)
```
- **Instrumentation layer:** wraps the client methods (`chat.completions.create`, `messages.create`, `models.generate_content`), including streaming iterators, so tokens can be counted when the stream finishes. *Why:* users shouldn't have to change their call sites.
- **Recorder:** normalises each provider's usage fields into one `CallRecord`. *Why:* every provider reports tokens differently.
- **CostCalculator:** looks up prices by (provider, model, date). *Why:* prices change; costs must be reproducible and are always labelled "estimated".
- **Sinks:** a pluggable interface. *Why:* SQLite for local use, JSONL for CI artefacts, OTel for people who already run a collector.
- **CLI/dashboard:** read-only views over the sink.

## 5. Tech stack & justification
Python 3.12, `uv`, Typer (CLI), FastAPI + plain HTML/Chart.js (dashboard), SQLite (stdlib `sqlite3`), pydantic (records), optional `opentelemetry-sdk`.
Alternative considered: exporting only to OTel, as OpenLLMetry does. It was rejected as the default because it needs extra infrastructure.

## 6. Data model
`calls(id TEXT PK, ts TIMESTAMP, provider TEXT, model TEXT, input_tokens INT, output_tokens INT, cached_tokens INT NULL, latency_ms INT, ttft_ms INT NULL, streamed BOOL, status TEXT, error_type TEXT NULL, est_cost_usd REAL, price_version TEXT, tags JSON, run_id TEXT)`
Indexes: `(ts)`, `(provider, model)`, `(run_id)`.
`runs(id TEXT PK, started_at, command TEXT, git_sha TEXT NULL)`.

## 7. API / interface design
```python
import aiwatch
aiwatch.instrument()                       # patch all installed providers
with aiwatch.tags(feature="summarise"):
    client.chat.completions.create(...)

@aiwatch.track(feature="rerank")
def rerank(...): ...
```
```
aiwatch run app.py --tag env=dev
aiwatch report --since 7d --group-by model,feature
aiwatch export --format csv > usage.csv
aiwatch ui --port 8765
aiwatch prices show | update
```

## 8. Key engineering problems
- **Streaming:** count tokens once the stream is exhausted, and handle early `break` or exceptions inside the stream (wrap the iterator in a context manager).
- **Async clients:** support both sync and `Async*` clients.
- **Robustness:** instrumentation must never break the user's call. Every aiwatch error is caught and logged, never raised.
- **Version drift:** provider SDKs change method paths. Pin the supported versions and add contract tests.
- **Missing usage data:** some providers or streams omit token counts. Record `NULL` rather than guessing (an optional `tiktoken` estimate is explicitly flagged as estimated).
- **Thread and process safety** when writing to SQLite (WAL mode, one writer).

## 9. Milestones
- **M1: Core record + OpenAI.** Deliverables: `CallRecord`, SQLite sink, OpenAI sync wrapper, `aiwatch report`. What I learn: monkey-patching, context managers. Accept when: a demo script produces a correct report; unit tests use recorded fixtures.
- **M2: Streaming + async + Anthropic/Gemini.** Accept when: fixture tests cover streaming for all 3 providers.
- **M3: Cost table + tags + `aiwatch run`.** Accept when: prices carry source URLs; costs reproduce from a stored price version.
- **M4: Dashboard.** Accept when: `aiwatch ui` shows cost per day and model, p50/p95 latency, and error rate.
- **M5: Packaging + CI + PyPI release (0.1.0).** Accept when: a trusted-publishing GitHub workflow runs and a matrix test passes on 3.11–3.13.
- **M6 (optional): OTel sink.**

## 10. Testing strategy
Unit tests use recorded provider responses (no live calls in CI) and `respx` to mock HTTP. Contract tests pin the SDK versions. A property test checks that cost is never negative and that tokens sum correctly. An optional live smoke test runs manually with real keys.

## 11. Observability
aiwatch's own debug logging (`AIWATCH_DEBUG=1`), plus an optional OTel export following the GenAI semantic conventions.

## 12. Security
- Never store prompts or completions by default (opt-in only, with a warning).
- Never log API keys; scrub headers.
- Dashboard binds to 127.0.0.1 only.

## 13. Deployment
Published to PyPI via GitHub Actions trusted publishing. No hosted deployment.

## 14. Evaluation / measurements to collect
- Instrumentation overhead per call: TBD, measure with `benchmarks/overhead.py`.
- Accuracy of the cost estimate against a provider invoice for a test account: TBD.

## 15. Prerequisite learning
`learning/python-04-decorators-context-managers`, `learning/python-08-async-concurrency`, `learning/python-10-packaging`, `learning/ai-01-llm-fundamentals`, `learning/ai-02-tokens-embeddings`.

## 16. Interview talking points
- How do you instrument a third-party SDK without changing call sites?
- How do you count tokens for streaming responses?
- How do you design a plugin/sink interface?
- How would you estimate and control LLM cost in production?

## 17. Resume bullet templates
- "Built and published aiwatch, an open-source Python SDK/CLI tracking LLM token usage, latency and estimated cost across OpenAI, Anthropic and Gemini with [MEASURED_VALUE] ms per-call overhead."
- Mention adoption only if it's real (downloads/stars: never estimated).

## 18. Open questions / uncertainties
- Is the name `aiwatch` available on PyPI? Check before M5.
- Should we use LiteLLM's model price map rather than maintaining our own? Evaluate its licence and freshness.
