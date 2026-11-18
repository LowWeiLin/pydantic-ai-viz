"""Recordings (``.pviz.jsonl``) and single-file HTML exports (spec section 3.7).

A recording is the session log, one envelope per line. An HTML export is the
viewer with that log embedded in its boot slot. Both are plain serialisations of
the same list of envelopes, so everything here works on such a list and knows
nothing about how it was captured.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_graph import Graph

from ._analyze import Analysis

if TYPE_CHECKING:
    from ._session import Session

Envelope = dict[str, Any]

VIEWER_PATH = Path(__file__).parent / "_static" / "viewer.html"
"""The built viewer, copied here by ``pnpm --dir web build`` and bundled into the wheel."""

BOOT_TOKEN = '"__PVIZ_BOOT__"'
"""The viewer's boot slot: this token, quotes included, is replaced with boot JSON."""

PLACEHOLDER_VIEWER = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>pydantic-ai-viz</title>
<style>
body { font: 14px/1.45 ui-sans-serif, system-ui, "Segoe UI", sans-serif; margin: 3rem auto; max-width: 40rem;
  padding: 0 1rem; color: #161a22; background: #f7f8fa; }
code { font-family: ui-monospace, Consolas, monospace; background: #e1e5eb; padding: 0.1rem 0.3rem;
  border-radius: 4px; }
</style>
</head>
<body>
<h1>pydantic-ai-viz</h1>
<p>UI not built. Run: <code>pnpm --dir web install &amp;&amp; pnpm --dir web build</code></p>
<p>The captured events are embedded in this page and are not lost: once the UI is built, export again.</p>
<script id="pviz-boot" type="application/json">"__PVIZ_BOOT__"</script>
</body>
</html>
"""
"""Stands in for the viewer in a source checkout without a web build."""

# Inside <script>, "</script" and "<!--" end or derail the element whatever the JSON says, and
# U+2028/U+2029 are line breaks to older JavaScript parsers. All five are legal JSON escapes.
_HTML_UNSAFE = str.maketrans({"<": "\\u003c", ">": "\\u003e", "&": "\\u0026", "\u2028": "\\u2028", "\u2029": "\\u2029"})


def dumps(value: Any) -> str:
    """Compact JSON. Non-ASCII text is kept as is; it is escaped at encoding time only if it must be."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def encode(text: str) -> bytes:
    """UTF-8 bytes. A lone surrogate (broken model output) becomes ``?``; it never fails the export."""
    return text.encode("utf-8", "replace")


# ------------------------------------------------------------------- viewer


def viewer_template() -> str:
    """The viewer page with its boot slot unfilled: the built one, else the placeholder."""
    try:
        template = VIEWER_PATH.read_text(encoding="utf-8")
    except OSError:
        return PLACEHOLDER_VIEWER
    # A viewer without the slot could not show anything we embed; the placeholder at least says so.
    return template if BOOT_TOKEN in template else PLACEHOLDER_VIEWER


def boot_json(boot: Mapping[str, Any]) -> str:
    """Boot data as JSON that is safe inside a ``<script>`` element."""
    return dumps(boot).translate(_HTML_UNSAFE)


def render_viewer(boot: Mapping[str, Any]) -> str:
    """The viewer page with ``boot`` in its boot slot."""
    return viewer_template().replace(BOOT_TOKEN, boot_json(boot), 1)


def render_export(events: Iterable[Envelope]) -> str:
    """A self-contained page that replays ``events``."""
    return render_viewer({"mode": "embedded", "events": list(events)})


# --------------------------------------------------------------- recordings


def to_jsonl(events: Iterable[Envelope]) -> str:
    """One envelope per line, each line ending in a newline."""
    return "".join(dumps(envelope) + "\n" for envelope in events)


def read_jsonl(path: str | os.PathLike[str]) -> list[Envelope]:
    """Load a recording.

    Only the framing is checked: every line is a JSON object with a ``type``, and
    the first is ``session.started``. Unknown fields and event types pass, as the
    wire contract asks of every consumer.

    Raises:
        OSError: If the file cannot be read.
        ValueError: If the file is not a recording; the message names the line.
    """
    source = Path(path)
    events: list[Envelope] = []
    with source.open(encoding="utf-8-sig") as lines:
        for number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                envelope = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{source}: line {number} is not valid JSON ({exc})") from None
            if not isinstance(envelope, dict) or not isinstance(envelope.get("type"), str):  # pyright: ignore[reportUnknownMemberType]
                raise ValueError(f"{source}: line {number} is not a pviz event (a JSON object with a 'type')")
            events.append(envelope)  # pyright: ignore[reportUnknownArgumentType]
    if not events or events[0]["type"] != "session.started":
        raise ValueError(f"{source}: not a pviz recording (the first line must be a 'session.started' event)")
    return events


def _write(path: str | os.PathLike[str], text: str) -> Path:
    target = Path(path)
    target.write_bytes(encode(text))
    return target


def write_jsonl(events: Iterable[Envelope], path: str | os.PathLike[str]) -> Path:
    return _write(path, to_jsonl(events))


def write_html(events: Iterable[Envelope], path: str | os.PathLike[str]) -> Path:
    return _write(path, render_export(events))


_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._ -]+")


def download_name(title: str, extension: str) -> str:
    """A file name for a download of the session called ``title``: plain ASCII, safe in a header."""
    stem = _UNSAFE_FILENAME.sub("_", title).strip(" ._")
    return f"{stem or 'session'}{extension}"


# ------------------------------------------------------------------- public


def blueprint_events(target: Agent[Any, Any] | Graph[Any, Any, Any, Any] | Analysis) -> list[Envelope]:
    """The log of a session that only ever registered ``target``: blueprints, no runs."""
    from ._session import blueprint_session

    with blueprint_session(target) as session:
        return session.events


def export_html(
    source: Session | Agent[Any, Any] | Graph[Any, Any, Any, Any] | Analysis | str | os.PathLike[str],
    path: str | os.PathLike[str],
) -> Path:
    """Write a self-contained HTML file that opens in any browser, with no server.

    The file holds the viewer and the events, so it can be attached to an issue or
    sent to a colleague. It contains every captured prompt and tool result; treat
    it like the data it shows.

    ```python
    import pydantic_ai_viz as pviz

    pviz.export_html("run.pviz.jsonl", "run.html")  # a recording
    pviz.export_html(agent, "blueprint.html")  # structure only, nothing is run
    pviz.export_html(pviz.analyze("agents.py"), "blueprint.html")  # read from source, not imported
    ```

    Args:
        source: What to export. A `Session` gives everything it has captured so
            far; a path gives that `.pviz.jsonl` recording; an `Agent`, a `Graph`
            or an `Analysis` gives blueprints only, with no runs.
        path: Where to write the HTML file. An existing file is replaced.

    Returns:
        The path written.

    Raises:
        OSError: If a recording cannot be read or the file cannot be written.
        ValueError: If `source` is a path to something that is not a recording.
        TypeError: If `source` is none of the accepted kinds.
    """
    from ._session import Session

    if isinstance(source, Session):
        events = source.events
    elif isinstance(source, Agent | Graph | Analysis):
        events = blueprint_events(source)  # pyright: ignore[reportUnknownArgumentType]
    elif isinstance(source, str | os.PathLike):  # pyright: ignore[reportUnnecessaryIsInstance]
        events = read_jsonl(source)
    else:
        raise TypeError(
            "export_html() expects a Session, a recording path, a pydantic_ai.Agent, a pydantic_graph.Graph "
            "or an Analysis, "
            f"got {type(source).__name__}"
        )
    return write_html(events, path)
