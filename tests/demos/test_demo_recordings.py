"""What a recording of each demo holds: nothing that reads as a crash, and no path of the machine it was made on."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pydantic_ai_viz import record
from pydantic_ai_viz.demos import DEMOS

ROOT = Path(__file__).resolve().parents[2]
ABSOLUTE_PATH = re.compile(r"(?<![A-Za-z])[A-Za-z]:(\\\\|/)|/home/|/Users/")


@pytest.mark.anyio
@pytest.mark.parametrize("name", list(DEMOS))
async def test_a_recording_of_the_demo_has_no_traceback_and_no_absolute_path(name: str, tmp_path: Path) -> None:
    demo = DEMOS[name]
    with record(*demo.targets, stream=True, title=demo.title) as session:
        await demo.run(0)
        text = session.save(tmp_path / "demo.pviz.jsonl").read_text(encoding="utf-8")
    assert "Traceback" not in text
    for form in {str(ROOT), ROOT.as_posix(), json.dumps(str(ROOT))[1:-1]}:
        assert form not in text
    assert ABSOLUTE_PATH.search(text) is None
