from __future__ import annotations

from importlib.metadata import entry_points, version

import pytest

import pydantic_ai_viz
from pydantic_ai_viz import cli


def test_version_matches_distribution_metadata() -> None:
    assert version("pydantic-ai-viz") == pydantic_ai_viz.__version__


def test_console_script_resolves_to_cli_main() -> None:
    (script,) = entry_points(group="console_scripts", name="pydantic-ai-viz")
    assert script.value == "pydantic_ai_viz.cli:main"
    assert script.load() is cli.main


def test_cli_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert out.strip() == f"pydantic-ai-viz {pydantic_ai_viz.__version__}"
    assert out.isascii()


def test_pydantic_ai_is_a_supported_major() -> None:
    assert version("pydantic-ai-slim").split(".")[0] == "2"


@pytest.mark.anyio
async def test_anyio_plugin_runs_on_asyncio(anyio_backend: str) -> None:
    assert anyio_backend == "asyncio"
