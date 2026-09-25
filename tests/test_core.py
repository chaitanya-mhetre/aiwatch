from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import aiwatch
from aiwatch import CallRecord, JSONLSink, MemorySink, PriceTable, SQLiteSink
from aiwatch.recorder import Recorder

TABLE = PriceTable.from_yaml(
    """
prices:
  - {provider: p, model: "m-*", effective_from: 2026-01-01, input_per_mtok: 1, output_per_mtok: 2,
     source: FAKE}
  - {provider: p, model: "m-*", effective_from: 2026-06-01, input_per_mtok: 10, output_per_mtok: 20,
     source: FAKE}
  - {provider: p, model: "m-exact", effective_from: 2026-01-01, input_per_mtok: 5,
     output_per_mtok: 5, source: FAKE}
"""
)


def test_price_versioning_uses_entry_in_force_on_the_date() -> None:
    old, v_old = TABLE.cost("p", "m-1", 1_000_000, 0, on=date(2026, 3, 1))
    new, v_new = TABLE.cost("p", "m-1", 1_000_000, 0, on=date(2026, 7, 1))
    assert (old, new) == (Decimal(1), Decimal(10))
    assert v_old != v_new


def test_exact_model_beats_wildcard() -> None:
    cost, version = TABLE.cost("p", "m-exact", 1_000_000, 0, on=date(2026, 7, 1))
    assert cost == Decimal(5) and version == "p/m-exact@2026-01-01"


def test_unknown_model_or_missing_tokens_is_none_not_zero() -> None:
    assert TABLE.cost("p", "other", 10, 10) == (None, None)
    assert TABLE.cost("p", "m-1", None, 10) == (None, None)


def test_bundled_table_loads_and_has_no_real_prices() -> None:
    table = PriceTable.load()
    assert {e.source for e in table.entries} == {"FAKE"}


@pytest.mark.parametrize("prompt", [0, 1, 999, 10**7])
def test_cost_never_negative(prompt: int) -> None:
    cost, _ = TABLE.cost("p", "m-1", prompt, prompt // 2, cached_tokens=prompt * 2)
    assert cost is not None and cost >= 0


def test_tags_nest_and_reset() -> None:
    with aiwatch.tags(a="1"):
        with aiwatch.tags(b="2", a="override"):
            assert aiwatch.current_tags() == {"a": "override", "b": "2"}
        assert aiwatch.current_tags() == {"a": "1"}
    assert aiwatch.current_tags() == {}


def test_track_decorator_sync_and_async() -> None:
    @aiwatch.track(feature="x")
    def sync() -> dict[str, str]:
        return aiwatch.current_tags()

    @aiwatch.track(feature="y")
    async def coro() -> dict[str, str]:
        return aiwatch.current_tags()

    assert sync() == {"feature": "x"}
    assert asyncio.run(coro()) == {"feature": "y"}


def test_tags_do_not_leak_between_asyncio_tasks() -> None:
    async def worker(name: str) -> dict[str, str]:
        with aiwatch.tags(worker=name):
            await asyncio.sleep(0)
            return aiwatch.current_tags()

    async def main() -> list[dict[str, str]]:
        return await asyncio.gather(worker("a"), worker("b"))

    assert asyncio.run(main()) == [{"worker": "a"}, {"worker": "b"}]


class BrokenSink:
    def write(self, record: CallRecord) -> None:
        raise OSError("disk full")

    def close(self) -> None:
        pass


def test_recorder_never_raises() -> None:
    rec = Recorder(sink=BrokenSink(), prices=TABLE)
    assert rec.record(provider="p", model="m-1", prompt_tokens=1, completion_tokens=1) is None


def test_sqlite_roundtrip(tmp_path: Path) -> None:
    sink = SQLiteSink(tmp_path / "db.sqlite")
    rec = Recorder(sink=sink, prices=TABLE, run_id="r1")
    with aiwatch.tags(feature="f"):
        written = rec.record(provider="p", model="m-1", prompt_tokens=10, completion_tokens=5)
    assert written is not None
    [read] = list(sink.iter_records(run_id="r1"))
    assert read == written
    sink.close()


def test_jsonl_sink(tmp_path: Path) -> None:
    path = tmp_path / "out" / "calls.jsonl"
    Recorder(sink=JSONLSink(path), prices=TABLE).record(provider="p", model="m-1")
    Recorder(sink=JSONLSink(path), prices=TABLE).record(provider="p", model="m-2")
    lines = path.read_text().splitlines()
    assert [CallRecord.model_validate_json(x).model for x in lines] == ["m-1", "m-2"]


def test_memory_sink_default_config_is_lazy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    Recorder()  # constructing must not create files
    assert not (tmp_path / ".aiwatch").exists()
    memory = MemorySink()
    aiwatch.configure(sink=memory)
    assert aiwatch.get_recorder().sink is memory
