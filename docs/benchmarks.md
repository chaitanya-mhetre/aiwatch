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

Not measured yet (TBD): overhead for streaming calls, and throughput with many threads writing to one SQLite file.
