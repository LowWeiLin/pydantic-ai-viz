"""MkDocs hook: preview the docs with a hosted demo built on this machine.

    uv run python scripts/build_demo_site.py --out build/demo
    PVIZ_DOCS_DEMO=build/demo uv run --group docs mkdocs serve

With ``PVIZ_DOCS_DEMO`` set to a directory that ``build_demo_site.py`` wrote, that directory is
served at ``demo/`` next to the docs, and every link to the published hosted demo
(``PUBLISHED``) points at it instead, so the docs open the demos of the checkout,
not the ones last deployed. Without it, the hook does nothing.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from mkdocs.exceptions import PluginError
from mkdocs.utils import get_relative_url

ENV = "PVIZ_DOCS_DEMO"

PUBLISHED = "https://lowweilin.github.io/pydantic-ai-viz/demo/"
"""Where the docs link to the hosted demo: ``site_url`` plus ``demo/``. Not read from the config,
because ``mkdocs serve`` replaces ``site_url`` with its own address."""


def _demo_dir(config) -> Path | None:
    value = os.environ.get(ENV)
    if not value:
        return None
    return Path(config.config_file_path).parent / value


def on_config(config):
    demo = _demo_dir(config)
    if demo is not None and not (demo / "index.html").is_file():
        raise PluginError(
            f"{ENV}={os.environ[ENV]}: no demo site there. "
            f"Run: uv run python scripts/build_demo_site.py --out {os.environ[ENV]}"
        )
    return config


def on_page_content(html, page, config, files):
    if _demo_dir(config) is None:
        return html
    return html.replace(PUBLISHED, get_relative_url("demo/", page.url))


def on_post_build(config):
    demo = _demo_dir(config)
    if demo is not None:
        shutil.copytree(demo, Path(config.site_dir) / "demo", dirs_exist_ok=True)
