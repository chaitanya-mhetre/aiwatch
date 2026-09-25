"""Helpers to build Server-Sent-Events bodies for mocked streaming responses."""

from __future__ import annotations

import json
from typing import Any


def sse(events: list[tuple[str | None, Any]], done: bool = False) -> bytes:
    out = []
    for name, data in events:
        if name:
            out.append(f"event: {name}")
        out.append(f"data: {json.dumps(data)}")
        out.append("")
    if done:
        out += ["data: [DONE]", ""]
    return ("\n".join(out) + "\n").encode()
