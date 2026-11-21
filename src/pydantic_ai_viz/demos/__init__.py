"""Bundled demos: offline, deterministic scenarios that need no API key.

`DEMOS` maps each demo name to its `Demo`; insertion order is display order and the first
entry is the default for `pydantic-ai-viz demo`. A demo whose dependencies are not installed is
left out of `DEMOS`, and `UNAVAILABLE` says what to install.
"""

from __future__ import annotations

from importlib.util import find_spec

from . import pipeline, research, support
from ._base import Demo

DEMOS: dict[str, Demo] = {demo.name: demo for demo in (support.demo, research.demo, pipeline.demo)}

UNAVAILABLE: dict[str, str] = {}
"""Demo name -> why it is missing, for each demo that needs a package that is not installed."""

try:
    from . import capabilities
except ImportError as exc:
    # A missing harness is the usual cause, checked first because the module imports pydantic_ai names the
    # harness's own pin brings (ModelFallback). Anything else (a dependency of one of its extras) is named as it is.
    missing = exc.name if isinstance(exc, ModuleNotFoundError) else None
    harness_missing = (
        find_spec("pydantic_ai_harness") is None or (missing or "").partition(".")[0] == "pydantic_ai_harness"
    )
    cause = "pydantic-ai-harness" if harness_missing else f"a package that is not installed ({exc})"
    UNAVAILABLE["capabilities"] = f"needs {cause}: pip install 'pydantic-ai-viz[demos]'"
else:
    DEMOS[capabilities.demo.name] = capabilities.demo

__all__ = ["DEMOS", "UNAVAILABLE", "Demo"]
