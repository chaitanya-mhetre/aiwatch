"""``aiwatch.instrument()`` patches every supported SDK that is installed."""

from __future__ import annotations

import importlib

from aiwatch.instrument import _wrap
from aiwatch.recorder import log

PROVIDERS = ("openai", "anthropic", "gemini")
_SDK_MODULE = {"openai": "openai", "anthropic": "anthropic", "gemini": "google.genai"}


def instrument(providers: tuple[str, ...] | list[str] | None = None) -> list[str]:
    """Patch the given providers (default: all installed). Idempotent.

    Returns the providers that were instrumented. A missing SDK is skipped silently; an SDK whose
    layout changed is skipped with a debug log rather than crashing the user's program.
    """
    done: list[str] = []
    for name in providers or PROVIDERS:
        if name not in _SDK_MODULE:
            raise ValueError(f"unknown provider {name!r}; choose from {PROVIDERS}")
        try:
            importlib.import_module(_SDK_MODULE[name])
        except ImportError:
            continue
        try:
            module = importlib.import_module(f"aiwatch.instrument.{name}")
            _wrap.apply(module.patches())
            done.append(name)
        except Exception:
            log.debug("aiwatch: could not instrument %s", name, exc_info=True)
    return done


def uninstrument() -> None:
    """Restore every original SDK method."""
    _wrap.revert()


__all__ = ["instrument", "uninstrument", "PROVIDERS"]
