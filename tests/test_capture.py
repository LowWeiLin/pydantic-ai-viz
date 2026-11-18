from __future__ import annotations

import pytest
from opentelemetry.sdk.trace import TracerProvider
from pydantic_ai import Agent, InstrumentationSettings
from pydantic_ai.models.test import TestModel

from pydantic_ai_viz import _capture, record
from pydantic_ai_viz._capture import CaptureProcessor
from pydantic_ai_viz._normalize import Normalized


def _instrumented(processor: CaptureProcessor) -> Agent[None, str]:
    provider = TracerProvider()
    provider.add_span_processor(processor)
    agent = Agent(TestModel(custom_output_text="fine"), name="steady")
    agent.instrument = InstrumentationSettings(tracer_provider=provider)
    return agent


def test_processor_forwards_normalised_spans_at_start_and_at_end() -> None:
    seen: list[tuple[str, str, str]] = []
    processor = CaptureProcessor(
        lambda n: seen.append(("start", n.span.kind, n.span.status)),
        lambda n: seen.append(("end", n.span.kind, n.span.status)),
    )
    assert _instrumented(processor).run_sync("hi").output == "fine"
    assert seen == [
        ("start", "agent_run", "running"),
        ("start", "model_request", "running"),
        ("end", "model_request", "ok"),
        ("end", "agent_run", "ok"),
    ]
    assert processor.errors == 0
    assert processor.force_flush() is True
    processor.shutdown()


def test_failing_callbacks_never_reach_the_run_and_are_reported_once(capsys: pytest.CaptureFixture[str]) -> None:
    def explode(normalized: Normalized) -> None:
        raise RuntimeError("viewer bug")

    processor = CaptureProcessor(explode, explode)
    agent = _instrumented(processor)
    assert agent.run_sync("hi").output == "fine"
    assert agent.run_sync("again").output == "fine"
    assert processor.errors == 8  # two runs, two spans each, start and end
    err = capsys.readouterr().err
    assert err.count("pydantic-ai-viz: span capture failed") == 1
    assert "RuntimeError: viewer bug" in err
    assert err.isascii()


def test_a_normaliser_failure_is_contained_too(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(span: object, *, ended: bool) -> Normalized:
        raise KeyError("attribute moved")

    monkeypatch.setattr(_capture, "normalize", broken)
    with record() as session:
        assert Agent(TestModel(custom_output_text="fine"), name="steady").run_sync("hi").output == "fine"
        assert [envelope["type"] for envelope in session.events] == ["session.started"]
    assert "KeyError" in capsys.readouterr().err


def test_a_broken_stderr_is_contained_too(monkeypatch: pytest.MonkeyPatch) -> None:
    class Closed:
        def write(self, text: str) -> int:
            raise ValueError("I/O operation on closed file")

    def explode(normalized: Normalized) -> None:
        raise RuntimeError("viewer bug")

    monkeypatch.setattr("sys.stderr", Closed())
    processor = CaptureProcessor(explode, explode)
    assert _instrumented(processor).run_sync("hi").output == "fine"
    assert processor.errors == 4


def test_a_failing_session_log_does_not_reach_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    with record() as session:

        def full_disk(body: object) -> None:
            raise OSError("no space left")

        monkeypatch.setattr(session._log, "append", full_disk)  # pyright: ignore[reportPrivateUsage]
        assert Agent(TestModel(custom_output_text="fine"), name="steady").run_sync("hi").output == "fine"
        assert session._processor.errors == 4  # pyright: ignore[reportPrivateUsage]
