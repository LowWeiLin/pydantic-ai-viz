from __future__ import annotations

import os

import pytest

# Must be set before anything imports pydantic_ai.
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")


@pytest.fixture
def anyio_backend() -> str:
    """Run ``@pytest.mark.anyio`` tests on asyncio only."""
    return "asyncio"
