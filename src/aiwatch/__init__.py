"""aiwatch: local-first LLM usage and cost tracking.

import aiwatch
aiwatch.instrument()
with aiwatch.tags(feature="summarise"):
    client.chat.completions.create(...)
"""

from aiwatch.instrument import instrument, uninstrument
from aiwatch.pricing import PriceEntry, PriceTable
from aiwatch.record import CallRecord
from aiwatch.recorder import Recorder, configure, current_tags, get_recorder, tags, track
from aiwatch.sinks import JSONLSink, MemorySink, Sink, SQLiteSink, TeeSink

__version__ = "0.1.0"

__all__ = [
    "CallRecord",
    "JSONLSink",
    "MemorySink",
    "PriceEntry",
    "PriceTable",
    "Recorder",
    "SQLiteSink",
    "Sink",
    "TeeSink",
    "configure",
    "current_tags",
    "get_recorder",
    "instrument",
    "tags",
    "track",
    "uninstrument",
]
