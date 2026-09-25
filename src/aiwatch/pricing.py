"""Versioned price table -> estimated cost.

Same YAML format as ``ai-gateway``'s ``config/prices.yaml``: USD per 1M tokens, each entry carrying
the pricing-page ``source`` and the date it was ``checked_on``. Costs are always *estimates*.
A model with no entry has an unknown cost (``None``), never zero.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from importlib import resources
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

MTOK = Decimal(1_000_000)


class PriceEntry(BaseModel):
    provider: str
    model: str  # exact model name, or a prefix ending in "*"
    effective_from: date
    input_per_mtok: Decimal = Field(ge=0)
    output_per_mtok: Decimal = Field(ge=0)
    cached_input_per_mtok: Decimal | None = Field(default=None, ge=0)
    source: str
    checked_on: date | None = None

    @property
    def version(self) -> str:
        """A stable id for this price, stored with each record so costs are reproducible."""
        return f"{self.provider}/{self.model}@{self.effective_from.isoformat()}"

    def matches(self, provider: str, model: str) -> bool:
        if provider != self.provider:
            return False
        if self.model.endswith("*"):
            return model.startswith(self.model[:-1])
        return model == self.model


class PriceTable:
    def __init__(self, entries: list[PriceEntry]) -> None:
        # Exact names beat wildcards; newer effective dates beat older ones.
        self.entries = sorted(
            entries, key=lambda e: (e.model.endswith("*"), -e.effective_from.toordinal())
        )

    @classmethod
    def from_yaml(cls, text: str) -> PriceTable:
        raw = yaml.safe_load(text) or {}
        return cls([PriceEntry.model_validate(e) for e in raw.get("prices", [])])

    @classmethod
    def load(cls, path: Path | None = None) -> PriceTable:
        """Load ``path``, or the table bundled with the package when ``path`` is None."""
        if path is not None:
            return cls.from_yaml(path.read_text())
        bundled = resources.files("aiwatch.data").joinpath("prices.yaml")
        return cls.from_yaml(bundled.read_text())

    def lookup(self, provider: str, model: str, on: date | None = None) -> PriceEntry | None:
        on = on or date.today()
        for entry in self.entries:
            if entry.matches(provider, model) and entry.effective_from <= on:
                return entry
        return None

    def cost(
        self,
        provider: str,
        model: str,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        cached_tokens: int | None = None,
        on: date | None = None,
    ) -> tuple[Decimal | None, str | None]:
        """Return ``(estimated_cost_usd, price_version)``; ``(None, None)`` when unknown."""
        entry = self.lookup(provider, model, on)
        if entry is None or prompt_tokens is None or completion_tokens is None:
            return None, None
        cached = min(cached_tokens or 0, prompt_tokens)
        cached_rate = (
            entry.cached_input_per_mtok
            if entry.cached_input_per_mtok is not None
            else entry.input_per_mtok
        )
        total = (
            Decimal(prompt_tokens - cached) * entry.input_per_mtok
            + Decimal(cached) * cached_rate
            + Decimal(completion_tokens) * entry.output_per_mtok
        ) / MTOK
        return total.quantize(Decimal("0.00000001")), entry.version
