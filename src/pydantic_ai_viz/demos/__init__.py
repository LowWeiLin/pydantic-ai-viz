"""Bundled demos: offline, deterministic scenarios that need no API key.

`DEMOS` maps each demo name to its `Demo`; insertion order is display order and the first
entry is the default for `pydantic-ai-viz demo`.
"""

from __future__ import annotations

from . import pipeline, research, support
from ._base import Demo

DEMOS: dict[str, Demo] = {demo.name: demo for demo in (support.demo, research.demo, pipeline.demo)}

__all__ = ["DEMOS", "Demo"]
