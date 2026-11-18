"""MkDocs hook: tolerate the docs screenshots until they have been generated.

TEMPORARY. The PNG files under ``docs/assets/screenshots/`` are produced by
``web/e2e/screenshots.spec.ts`` from real demo recordings, which needs the
finished viewer. The pages reference them from day one, so until the files are
committed a ``mkdocs build --strict`` would fail on six "target not found"
warnings.

This hook drops that one warning, and only for a screenshot that is really
missing. Every other broken link still fails the build, and once a PNG exists
the hook does nothing for it. Delete this file and the ``hooks:`` entry in
``mkdocs.yml`` when all screenshots are committed.
"""

from __future__ import annotations

import logging
from pathlib import Path

_SCREENSHOTS = Path(__file__).resolve().parent.parent / "assets" / "screenshots"
_NAMES = (
    "hero-dark.png",
    "hero-light.png",
    "blueprint.png",
    "run-timeline.png",
    "inspector.png",
    "graph.png",
)


class _PendingScreenshots(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if "not found among documentation files" not in message:
            return True
        return not any(
            f"assets/screenshots/{name}" in message and not (_SCREENSHOTS / name).exists() for name in _NAMES
        )


def on_startup(**_: object) -> None:
    """Install the filter on the logger that reports broken links."""
    logger = logging.getLogger("mkdocs.structure.pages")
    if not any(isinstance(f, _PendingScreenshots) for f in logger.filters):
        logger.addFilter(_PendingScreenshots())
