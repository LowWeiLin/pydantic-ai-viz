from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before anything imports pydantic_ai.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_session_outlives_its_test() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Sessions and ``Agent.instrument_all`` are process-wide; a failing test must not leak them."""
    yield
    from pydantic_ai import Agent

    from pydantic_ai_viz._session import active_session

    while (session := active_session()) is not None:
        session.close()
    Agent.instrument_all(False)


@pytest.fixture
def placeholder_viewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Behave like a source checkout without a web build, whether or not this one has been built."""
    from pydantic_ai_viz import _export

    monkeypatch.setattr(_export, "VIEWER_PATH", tmp_path / "no-such-dir" / "viewer.html")
