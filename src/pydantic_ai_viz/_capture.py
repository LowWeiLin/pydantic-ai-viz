"""The OpenTelemetry ``SpanProcessor`` that feeds a session.

``on_start`` is what makes the view live: exporters only see a span when it ends,
a processor also sees it begin. Both callbacks run inside the user's agent run, so
nothing here may raise: a failure is counted, reported once on stderr, and the run
goes on.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Callable
from typing import Any

from opentelemetry.sdk.trace import ReadableSpan, Span, SpanProcessor

from ._normalize import Normalized, normalize

SpanCallback = Callable[[Normalized], None]


class CaptureProcessor(SpanProcessor):
    """Normalises each span at start and at end and hands it to two callbacks."""

    def __init__(self, on_started: SpanCallback, on_ended: SpanCallback) -> None:
        self._on_started = on_started
        self._on_ended = on_ended
        self.errors = 0
        """How many callbacks failed. The first failure is also printed to stderr."""

    def on_start(self, span: Span, parent_context: Any = None) -> None:
        self._forward(span, self._on_started, ended=False)

    def on_end(self, span: ReadableSpan) -> None:
        self._forward(span, self._on_ended, ended=True)

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True  # nothing is buffered

    def _forward(self, span: ReadableSpan, callback: SpanCallback, *, ended: bool) -> None:
        try:
            callback(normalize(span, ended=ended))
        except Exception as exc:
            self.errors += 1
            if self.errors == 1:
                _report(exc)


def _report(exc: Exception) -> None:
    # A closed or broken stderr must not reach the user's run either.
    with contextlib.suppress(Exception):
        print(
            f"pydantic-ai-viz: span capture failed ({type(exc).__name__}: {exc}). "
            "Your run is not affected; further capture errors are counted, not printed.",
            file=sys.stderr,
        )
