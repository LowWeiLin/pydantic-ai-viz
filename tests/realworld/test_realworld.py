"""The static analyser, tried on real projects from GitHub.

These tests need the network, so they are not part of the default run:

    uv run pytest -m network tests/realworld

Each project in ``manifest.toml`` is fetched at its pinned commit, once, into the
cache directory (``PYDANTIC_AI_VIZ_CACHE_DIR``, or the user's cache) and then only
read. None of the fetched code is imported or run.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from realworld_report import Project, load_manifest, measure

from pydantic_ai_viz import export_html

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]


@pytest.mark.parametrize("project", load_manifest(), ids=lambda project: project.id)
def test_a_pinned_project_is_read_and_holds_what_its_entry_expects(project: Project, tmp_path: Path) -> None:
    result, found = measure(project)

    # Fetched, analysed within the limits, and nothing the entry expects is missing.
    assert result.problems == []
    assert found is not None
    assert found.notices[0] == f"read {project.repo} from GitHub at commit {project.sha}; none of its code was run"

    # Everything found says where it was found, in one of the files that were read.
    for blueprint in found.blueprints:
        assert blueprint.source == "code"
        assert blueprint.location is not None
        assert blueprint.location.line >= 1
        assert blueprint.location.file in found.files

    # What was found is a valid session: it can be written out as a page.
    page = export_html(found, tmp_path / "blueprint.html")
    assert page.stat().st_size > 0
