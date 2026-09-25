from __future__ import annotations

from collections.abc import Iterator

import pytest

import aiwatch
from aiwatch import MemorySink, PriceTable

PRICES = PriceTable.from_yaml(
    """
prices:
  - {provider: openai, model: "gpt-test*", effective_from: 2026-01-01,
     input_per_mtok: 2.00, output_per_mtok: 8.00, cached_input_per_mtok: 0.50, source: FAKE}
  - {provider: anthropic, model: "claude-test", effective_from: 2026-01-01,
     input_per_mtok: 3.00, output_per_mtok: 15.00, cached_input_per_mtok: 0.30, source: FAKE}
  - {provider: gemini, model: "gemini-test", effective_from: 2026-01-01,
     input_per_mtok: 1.00, output_per_mtok: 4.00, source: FAKE}
"""
)


@pytest.fixture
def sink() -> Iterator[MemorySink]:
    memory = MemorySink()
    aiwatch.configure(sink=memory, prices=PRICES, run_id="test-run")
    aiwatch.instrument()
    yield memory
    aiwatch.uninstrument()
