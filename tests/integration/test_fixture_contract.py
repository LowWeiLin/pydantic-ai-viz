"""Cross-language contract: the frontend's hand-written fixtures against the Python models.

The viewer is developed and tested against ``web/fixtures``. Vitest checks them against the
generated JSON Schema; this checks them against the models the schema comes from, and that the
committed schema is the one those models produce. If these pass, a fixture is something the
backend could have written.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pydantic_ai_viz._models import envelope_json_schema

from .support import REPO, WEB, assert_log_guarantees, final_spans, read_recording

FIXTURES = sorted((WEB / "fixtures").glob("*.pviz.jsonl"))


def test_the_fixtures_are_there() -> None:
    # A glob that matches nothing would make every test below pass by doing nothing.
    assert {path.name for path in FIXTURES} >= {"sample.pviz.jsonl", "failed.pviz.jsonl"}


@pytest.mark.parametrize("path", FIXTURES, ids=lambda path: path.name)
def test_every_fixture_line_is_a_valid_envelope(path: Path) -> None:
    assert b"\r" not in path.read_bytes(), "fixtures are LF only"
    events = read_recording(path)  # validates each line strictly and round-trips it
    assert events[0]["type"] == "session.started"
    assert [event["seq"] for event in events] == list(range(len(events)))
    assert len(final_spans(events)) > 0


def test_the_complete_fixture_keeps_the_ordering_guarantees() -> None:
    # failed.pviz.jsonl ends mid-run on purpose, so only the sample is a finished log.
    assert_log_guarantees(read_recording(WEB / "fixtures" / "sample.pviz.jsonl"))


def test_the_fixture_index_points_at_the_fixtures() -> None:
    index = json.loads((WEB / "fixtures" / "index.json").read_text(encoding="utf-8"))
    assert set(index) == {"recordings"}
    for entry in index["recordings"]:
        assert set(entry) == {"id", "title", "description", "src"}
        assert all(isinstance(value, str) and value for value in entry.values())
    assert sorted(entry["src"] for entry in index["recordings"]) == [path.name for path in FIXTURES]


def test_the_committed_schema_is_what_the_models_generate() -> None:
    committed = json.loads((REPO / "schema" / "pviz.schema.json").read_text(encoding="utf-8"))
    assert committed == envelope_json_schema()
