from __future__ import annotations

import asyncio
import os
import threading
import warnings
from collections.abc import Iterator
from pathlib import Path

import pytest

# Must be set before anything imports pydantic_ai.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

# Python 3.14's argparse colours help and errors when FORCE_COLOR is set, as it is in CI,
# which puts escape codes into the output the CLI tests compare.
os.environ.pop("FORCE_COLOR", None)
os.environ["NO_COLOR"] = "1"


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


@pytest.fixture(autouse=True)
def _no_event_loop_outlives_its_test() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """``run_sync`` leaves an event loop set on the main thread; close it rather than let it leak."""
    yield
    if threading.current_thread() is not threading.main_thread():
        return
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)  # Python 3.14 deprecates the policy API
        try:
            loop = asyncio.get_event_loop_policy().get_event_loop()
        except RuntimeError:
            return
        if not loop.is_running() and not loop.is_closed():
            loop.close()
        asyncio.set_event_loop(None)


@pytest.fixture
def placeholder_viewer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Behave like a source checkout without a web build, whether or not this one has been built."""
    from pydantic_ai_viz import _export

    monkeypatch.setattr(_export, "VIEWER_PATH", tmp_path / "no-such-dir" / "viewer.html")
