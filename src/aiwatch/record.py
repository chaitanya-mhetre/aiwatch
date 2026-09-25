"""The normalised record of one LLM call.

Field names match the usage-event schema of the companion ``ai-gateway`` project
(``prompt_tokens``, ``completion_tokens``, ``cached_tokens``, ``est_cost_usd`` ...), so the same
reporting code can read both.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

Status = Literal["ok", "error"]


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class CallRecord(BaseModel):
    """One provider call, after aiwatch has normalised the provider's usage fields.

    Token counts are ``None`` when the provider did not report them (for example an OpenAI stream
    without ``stream_options={"include_usage": True}``). aiwatch never guesses a count silently.
    """

    id: str = Field(default_factory=_new_id)
    ts: datetime = Field(default_factory=_now)
    provider: str
    model: str
    operation: str = "chat"
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cached_tokens: int | None = None
    tokens_estimated: bool = False
    latency_ms: int = 0
    ttft_ms: int | None = None
    stream: bool = False
    status: Status = "ok"
    error_type: str | None = None
    est_cost_usd: Decimal | None = None  # None = unknown (no price entry or no token counts)
    price_version: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    run_id: str | None = None
