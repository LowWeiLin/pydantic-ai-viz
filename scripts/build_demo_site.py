"""Build the hosted static demo: real recordings of the bundled demos next to the viewer.

    uv run python scripts/build_demo_site.py --out site/demo

writes

    <out>/index.html                      the built viewer, boot slot untouched (static mode)
    <out>/recordings/<name>.pviz.jsonl    one recording per demo, token streaming on
    <out>/recordings/index.json           the gallery the viewer fetches
    <out>/recordings/source.pviz.jsonl    the demos' source code as `pydantic-ai-viz view`
                                          reads it: blueprints only, listed last in the
                                          gallery with kind "blueprint"

The recordings are made here on every build and are never committed. The viewer
must have been built first (``pnpm --dir web build``): a demo site made of the
placeholder page would be useless, so that case is an error, not a fallback.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import pydantic_ai_viz as pviz
from pydantic_ai_viz import demos
from pydantic_ai_viz._export import BOOT_TOKEN, VIEWER_PATH
from pydantic_ai_viz._session import blueprint_session
from pydantic_ai_viz.demos import DEMOS
from pydantic_ai_viz.demos._base import Demo

SOURCE_TITLE = "The demos, read from source"
"""Title of the blueprint-only session made from the demos' source code."""


def built_viewer() -> str:
    """The built viewer page, or exit with the command that builds it."""
    try:
        page = VIEWER_PATH.read_text(encoding="utf-8")
    except OSError:
        page = ""
    if BOOT_TOKEN not in page:
        sys.exit(f"build_demo_site: {VIEWER_PATH} is missing or not a viewer. Run: pnpm --dir web build")
    return page


async def play(demo: Demo, speed: float) -> object:
    return await demo.run(speed)


def build(out: Path, speed: float) -> list[Path]:
    """Write the demo site into ``out`` and return the files written."""
    page = built_viewer()
    recordings = out / "recordings"
    recordings.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    index: list[dict[str, str]] = []
    for demo in DEMOS.values():
        with pviz.record(*demo.targets, stream=True, title=demo.title) as session:
            asyncio.run(play(demo, speed))
            written.append(session.save(recordings / f"{demo.name}.pviz.jsonl"))
        index.append(
            {
                "id": demo.name,
                "title": demo.title,
                "description": demo.description,
                # Resolved by the viewer against the recordings directory.
                "src": f"{demo.name}.pviz.jsonl",
            }
        )
    index.append(
        {
            "id": "source",
            "title": SOURCE_TITLE,
            "description": "Ten agents and a graph, drawn from the demos' code by pydantic-ai-viz view "
            "without running any of it.",
            "src": "source.pviz.jsonl",
            "kind": "blueprint",
        }
    )
    index_file = recordings / "index.json"
    index_file.write_text(json.dumps({"recordings": index}, indent=2) + "\n", encoding="utf-8", newline="\n")
    # What `pydantic-ai-viz view` shows of the same code: read, never imported or run.
    with blueprint_session(pviz.analyze(Path(demos.__file__).parent), title=SOURCE_TITLE) as session:
        written.append(session.save(recordings / "source.pviz.jsonl"))
    home = out / "index.html"
    home.write_bytes(page.encode("utf-8"))
    return [*written, index_file, home]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the hosted static demo from the bundled demos.")
    parser.add_argument("--out", required=True, type=Path, metavar="DIR", help="output directory, e.g. site/demo")
    parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        metavar="X",
        help="demo playback speed (default 1.0, the pace a visitor watches; 0 = no delays)",
    )
    args = parser.parse_args()
    if args.speed < 0:
        parser.error("--speed must be 0 or more")
    for path in build(args.out, args.speed):
        print(f"wrote {path} ({path.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
