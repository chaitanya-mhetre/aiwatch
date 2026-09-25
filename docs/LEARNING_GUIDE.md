# aiwatch: learning guide

This guide is for studying the codebase until you can explain every design decision in an interview.
Read the files in the order below and keep the tests open next to the code.

## 1. The big idea in one paragraph
aiwatch replaces SDK methods such as `openai.resources.chat.completions.Completions.create` with a wrapper.
The wrapper calls the original, measures the time, reads token usage from the response (or from the stream as it
flows past), turns that into a `CallRecord`, prices it, and writes it to a sink. The user's code gets back exactly
what it would have got without aiwatch. That's the contract: **observe, never change, never break.**

## 2. File tour (reading order)
| # | File | What to learn there |
|---|---|---|
| 1 | `src/aiwatch/record.py` | The data model. Why token fields are `int \| None`: unknown is different from zero. |
| 2 | `src/aiwatch/pricing.py` | Price versioning with `effective_from`, exact-vs-wildcard lookup order, and `Decimal` for money. |
| 3 | `src/aiwatch/sinks.py` | The `Protocol`-based plugin interface, SQLite WAL mode, and a lock around one shared connection. |
| 4 | `src/aiwatch/recorder.py` | The global recorder, lazy sink creation, `ContextVar` tags, and the `track` decorator (sync and async). |
| 5 | `src/aiwatch/instrument/_wrap.py` | **The core.** `CallSpec`, `wrap_sync`/`wrap_async`, `StreamProxy`, and patch/revert bookkeeping. |
| 6 | `src/aiwatch/instrument/openai.py` | A provider described as data: `provider`, `model`, `usage`, `accumulator`. |
| 7 | `src/aiwatch/instrument/anthropic.py`, `gemini.py` | How provider token semantics differ, and how they're normalised. |
| 8 | `src/aiwatch/instrument/__init__.py` | Optional dependencies: import only if installed, and fail soft. |
| 9 | `src/aiwatch/report.py` | Grouping, nearest-rank percentiles, and "unknown cost" handling in totals. |
| 10 | `src/aiwatch/cli.py` | Typer, `runpy.run_path` to run a script "as `__main__`", and exit-code propagation. |
| 11 | `src/aiwatch/dashboard/` | A minimal FastAPI read API and a static page. |
| 12 | `tests/mock_http.py` + `tests/test_*.py` | Testing third-party SDKs offline with an injected `MockTransport`. |

## 3. Key concepts, with pointers into the code

### Monkey-patching a class method
`_wrap.apply()` reads `owner.__dict__[attr]` (the plain function), wraps it, and `setattr`s the wrapper back onto the
**class**. Every instance, old or new, now calls the wrapper, because Python looks methods up on the class at call time.
The wrapper receives `sdk_self` first, like any method.
- *Idempotency:* the wrapper carries `__aiwatch_original__`. `apply()` skips anything that already has it, so calling
  `instrument()` twice doesn't double-wrap. `revert()` restores the originals from `_applied`.
- *Why `__dict__` and not `getattr`:* `getattr` could return an inherited method; you want the function defined on
  exactly that class.

### Describing providers as data (strategy pattern)
`CallSpec` holds four small functions. The generic wrapper handles timing, errors and streams once. Adding a provider means writing
extractors, not copying control flow. That's the same "rules as data" idea as a permission table.

### Wrapping a stream correctly
The hard part. A stream is an iterator the user consumes *later*, so you can't record when `create()` returns. `StreamProxy`:
- implements `__iter__`/`__next__` and feeds every chunk to an accumulator;
- records on `StopIteration` (finished), on any exception (error), on `close()`/`__exit__` (context manager), and in
  `__del__` (the user did `break` and dropped the object);
- `_Call.finish()` has a `done` flag, so all those paths record **exactly once**;
- `__getattr__` forwards unknown attributes (`stream.response`), so the proxy stays transparent.
`AsyncStreamProxy` is the same with `__aiter__`/`__anext__`/`__aexit__`.

### Token semantics differ by provider
- OpenAI chat: `prompt_tokens` includes cached tokens; the cached share is in `prompt_tokens_details.cached_tokens`.
  Streams only include usage in the last chunk, and only with `include_usage`.
- Anthropic: `input_tokens` **excludes** cache reads and writes, so aiwatch adds them back to make `prompt_tokens` mean the same
  thing everywhere. The stream has input on `message_start` and a running output count on `message_delta`.
- Gemini: thinking tokens are billed as output, so completion = candidates + thoughts. Stream usage is cumulative, so the last value wins.
The lesson: **normalise at the edge**, so everything downstream (pricing, reports) works with one meaning.

### ContextVar vs. thread-local
Tags use `contextvars.ContextVar`. Each asyncio task gets a copy of the context, so tags set in one task never show up in
another (`test_tags_do_not_leak_between_asyncio_tasks`). A `threading.local` would be shared by every task on the event-loop
thread. The default is an immutable `MappingProxyType` because a mutable default would be shared (ruff rule B039).

### Money: Decimal, and unknown vs. zero
Costs use `Decimal`, because binary floats can't represent 0.1 exactly. A missing price returns `None`, not `0`, so reports can
say "this total excludes N calls" instead of silently under-reporting.

### Never break the host program
Every extractor runs through `_safe()`, and `Recorder.record` catches everything. Errors are only logged at debug level.
Instrumentation that crashes production is worse than no instrumentation.

### SQLite for a local tool
WAL mode lets readers (the dashboard) and a writer work at the same time. One connection is shared across threads (`check_same_thread=False`)
and guarded by a `threading.Lock`. `INSERT OR IGNORE` on a primary key makes writes idempotent.

### Testing SDKs without the network
openai ≥ 3 and anthropic ≥ 1 moved to the `httpx2` package, so `respx` (which patches `httpx`) no longer intercepted
them. An early test run actually reached the real API with a fake key. The fix is **dependency injection**: pass
`http_client=httpx2.Client(transport=MockTransport(handler))`. With no real transport configured, a test *can't* reach the network.
Gemini accepts `HttpOptions(httpx_client=...)`.

### OpenTelemetry spans and the GenAI semantic conventions
`src/aiwatch/otel.py`. A **span** is one timed operation with attributes. Semantic conventions are agreed attribute names,
so every backend knows `gen_ai.usage.input_tokens` means input tokens no matter which library emitted it. aiwatch uses
`gen_ai.*` for what the conventions cover and its own `aiwatch.*` namespace for the rest (cost, TTFT, tags), so there's no
clash. Two design points worth explaining in an interview:
- **The library depends only on `opentelemetry-api`, never the SDK.** The application owns the TracerProvider and exporter.
  Without one, the API's no-op tracer makes the sink nearly free. This is the standard rule for instrumentation libraries.
- **The span is created after the call, with explicit timestamps.** Tokens are only known at the end, so the span is opened and
  closed in one go (`start = end - latency`). The trade-off is that it isn't the active context during the request. `TeeSink`
  (`sinks.py`) fans records out to SQLite and OTel and isolates failures.

## 4. Interview questions (with short answers)
1. **How do you instrument a third-party SDK without changing call sites?** Patch the method on its class and wrap the original.
   Keep a reference so it can be restored, and make patching idempotent.
2. **Why patch the class rather than the client instance?** It covers every instance, including ones created before instrumenting,
   with one patch per method. The cost is that it's process-global.
3. **How do you count tokens for a streaming response?** Wrap the iterator, feed each chunk to a per-provider accumulator,
   and record when the stream ends. Some providers send usage only in the final event.
4. **What if the user breaks out of the stream early?** The proxy records on `close`, on `__exit__` and in `__del__`, and a `done` flag guarantees
   one record.
5. **Why is `prompt_tokens` optional?** Some calls don't report usage. Recording `None` keeps reports honest; guessing would
   silently corrupt cost data.
6. **Why `Decimal` for cost?** Exact decimal arithmetic avoids float rounding drift when summing many small amounts.
7. **How do you keep costs correct after a price change?** Price entries have an `effective_from` date, lookup uses the entry in
   force on the call date, and each record stores the `price_version` it used.
8. **ContextVar vs. threading.local?** A ContextVar is copied per asyncio task, so values don't leak between concurrent tasks on
   one thread; a thread-local would.
9. **How do you stop instrumentation from breaking the host app?** Catch every exception in the recording path, log it at
   debug level, and always return the original result or re-raise the original error.
10. **How does the sink interface work and why is it a `Protocol`?** Anything with `write` and `close` qualifies (structural typing),
    so users can plug in their own without inheriting from an aiwatch class.
11. **SQLite WAL mode: what and why?** Writes go to a log, so readers don't block the writer. That fits "dashboard reading while
    the app writes".
12. **Is SQLite OK with many processes?** For light use, yes. Heavy multi-process writing contends on the write lock; use the JSONL
    sink per process or a real database.
13. **How did you test SDK wrappers offline?** By injecting an HTTP client with a mock transport, so the real SDK parses the real response
    shapes and the network can't be reached.
14. **What's the overhead, and how did you measure it?** Compare median per-call time with and without instrumentation against
    an in-process mock (see `docs/benchmarks.md`: about 61 µs in memory and 108 µs with SQLite on one laptop).
15. **Why not use OpenTelemetry only?** OTel needs a collector or backend. Local-first means zero infrastructure; an OTel sink is the
    planned add-on.
16. **Nearest-rank percentile: how does it work?** Sort, take the value at rank ⌈p/100·n⌉. It's simple, always returns a real observed value,
    and is fine for small samples.
17. **How does `aiwatch run` work?** It configures the recorder, instruments, sets `sys.argv`, and runs the file with
    `runpy.run_path(..., run_name="__main__")`, so the script's `if __name__ == "__main__":` block runs. It also catches `SystemExit`
    to pass the exit code through.
18. **What breaks if the SDK renames a method?** That patch is skipped (with a debug log) and those calls go unrecorded. The per-method tests
    catch it when the SDK version is bumped.
19. **Why normalise Anthropic cache tokens?** So "prompt tokens" means the same thing for every provider. Otherwise cross-provider comparisons
    and pricing would be wrong.
20. **What would you add for production at scale?** Sampling, a batching async writer, a central store, and budget alerts.
    (The OTel exporter now exists: `aiwatch.otel`.)
21. **Why should an instrumentation library depend on `opentelemetry-api` and not the SDK?** The app decides the exporter,
    sampling and resource. The library only creates spans. With no SDK configured, the API is a cheap no-op.
22. **Why omit a token attribute instead of sending 0 when the count is unknown?** 0 is a measurement. Sending it would make
    dashboards sum and average a value that was never observed. A missing attribute is honest.
23. **Your span is emitted after the call. What does that cost you?** It isn't the active context, so child spans (e.g. the
    HTTP request) don't nest under it. Fixing that means starting the span when the call starts and keeping it open, which is harder
    for streams that end in `__del__`.

## 5. Try it yourself
- Add OpenAI `embeddings.create`: write a `CallSpec` with `operation="embedding"` and a test using `Router`.
- Measure streaming overhead by extending `benchmarks/overhead.py`.
- Run `examples/otel_jaeger.py` against a local Jaeger and find the `aiwatch.est_cost_usd` attribute in the UI.
