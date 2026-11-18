"""Command line interface for pydantic-ai-viz."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from ._version import __version__


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pydantic-ai-viz`` command line and return its exit code."""
    parser = argparse.ArgumentParser(
        prog="pydantic-ai-viz",
        description="Visualize Pydantic AI agents: blueprints, live runs and shareable replays.",
    )
    parser.add_argument("--version", action="version", version=f"pydantic-ai-viz {__version__}")
    parser.parse_args(argv)
    parser.print_help()
    return 0
