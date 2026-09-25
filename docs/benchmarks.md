# Benchmarks

## Per-call overhead (measured 2026-09-25)

Script: [`benchmarks/overhead.py`](../benchmarks/overhead.py). It calls `chat.completions.create` 3,000 times
against an in-process mock transport (no network), with and without aiwatch, and compares medians.

Environment: Python 3.12.14, x86_64 Linux laptop, openai 3.19.2, single process.

| Configuration | Median per call |
|---|---|
| Uninstrumented | 655.3 µs |
| Instrumented, MemorySink | 716.5 µs |
| Instrumented, SQLiteSink (WAL) | 763.3 µs |
| **Overhead: MemorySink** | **≈ 61 µs** |
| **Overhead: SQLiteSink** | **≈ 108 µs** |

What this means: real LLM calls take hundreds of milliseconds to seconds, so ~0.1 ms of bookkeeping
is noise. The numbers come from one laptop run. Re-run the script on your own machine before quoting them.

## Streaming overhead (measured 2026-09-25)

Script: [`benchmarks/streaming.py`](../benchmarks/streaming.py). Each call streams an OpenAI chat completion of
**50 content chunks + 1 usage chunk** from an in-process mock transport (no network) and consumes it fully. It's measured
sync and async, uninstrumented vs instrumented (MemorySink, SQLiteSink). The three configurations are **interleaved in 20
rounds** (A B C A B C ...), 100 calls each per round, so machine-load drift hits all of them equally. The script compares
medians of 2,000 samples per configuration.

Environment: 12th Gen Intel Core i5-1240P (16 threads), Linux, Python 3.12.14, openai 3.19.2, single process.
**The laptop was busy** (load average ≈ 4–6, other builds running). Two back-to-back runs:

| Mode | Sink | Run 1 overhead | Run 2 overhead | Per chunk (run 1 / run 2) |
|---|---|---|---|---|
| sync | MemorySink | 65.3 µs | 65.8 µs | 1.31 / 1.32 µs |
| sync | SQLiteSink | 181.4 µs | 166.8 µs | 3.63 / 3.34 µs |
| async | MemorySink | 178.8 µs | 166.0 µs | 3.58 / 3.32 µs |
| async | SQLiteSink | 253.0 µs | 339.4 µs | 5.06 / 6.79 µs |

Baseline (uninstrumented) medians were 3.8–4.2 ms (sync) and 4.2–4.4 ms (async) per streamed call. Almost all of that is the
SDK parsing each SSE chunk into pydantic models. So aiwatch adds roughly **2–8%** on top of a mock stream, and far less next to
a real model that produces 50 tokens in about a second.

A cProfile run agrees: `StreamProxy.__next__` spends about 1 µs of its own time per chunk. The rest is the SDK's `__stream__`
parsing, which happens with or without aiwatch.

### A measurement mistake worth remembering
The first version of the script measured the configurations **one after another** (all uninstrumented calls, then all
MemorySink calls, then SQLite). It reported about 59 µs per chunk for sync and slightly *negative* overhead for async. Both were
artefacts: load on the machine changed between blocks, and one MemorySink kept growing for the whole block. Interleaving the
configurations (plus a fresh sink per block) removed the artefact, and the result then matched the profiler. Treat any benchmark
whose result disagrees with a profile as suspect.

Not measured yet (TBD): throughput with many threads writing to one SQLite file, and results on a quiet machine with error bars.
