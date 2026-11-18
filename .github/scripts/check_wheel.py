"""Assert that a built wheel ships the real viewer.

Usage::

    python .github/scripts/check_wheel.py dist

Looks for exactly one ``pydantic_ai_viz-*.whl`` in the directory and fails
unless it contains ``pydantic_ai_viz/_static/viewer.html`` and that file is
larger than 100 KB. A smaller file means the placeholder page was packaged, or
the web build was skipped.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

VIEWER = "pydantic_ai_viz/_static/viewer.html"
MIN_BYTES = 100_000


def main(argv: list[str]) -> int:
    """Check the wheel in the directory named by ``argv[1]`` and return an exit code."""
    if len(argv) != 2:
        print("usage: check_wheel.py DIST_DIR", file=sys.stderr)
        return 2

    wheels = sorted(Path(argv[1]).glob("pydantic_ai_viz-*.whl"))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in {argv[1]}, found {len(wheels)}", file=sys.stderr)
        return 1

    wheel = wheels[0]
    with zipfile.ZipFile(wheel) as archive:
        try:
            size = archive.getinfo(VIEWER).file_size
        except KeyError:
            print(f"{wheel.name}: {VIEWER} is missing", file=sys.stderr)
            return 1

    if size <= MIN_BYTES:
        print(f"{wheel.name}: {VIEWER} is {size} bytes, expected more than {MIN_BYTES}", file=sys.stderr)
        return 1

    print(f"{wheel.name}: {VIEWER} present, {size} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
