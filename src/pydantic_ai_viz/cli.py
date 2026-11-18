"""Command line interface for pydantic-ai-viz.

Five commands: ``demo``, ``view``, ``run``, ``open`` and ``export``. Everything
printed is plain ASCII, because a Windows console may still be cp1252.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import os
import runpy
import sys
import tempfile
import threading
import traceback
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, TextIO

from ._export import export_html
from ._server import DEFAULT_PORT, open_in_browser, serve, server_of, wait_for_client
from ._session import Session, record
from ._stream import attach_on_creation
from ._targets import TargetError, namespace_module, resolve_targets, script_environment
from ._version import __version__

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

RECORDING_SUFFIX = ".pviz.jsonl"


class _Demo(Protocol):
    """The part of ``pydantic_ai_viz.demos.Demo`` the CLI relies on."""

    @property
    def name(self) -> str: ...
    @property
    def title(self) -> str: ...
    @property
    def description(self) -> str: ...
    @property
    def targets(self) -> tuple[Any, ...]: ...
    @property
    def run(self) -> Callable[[float], Awaitable[object]]: ...


class _UsageError(Exception):
    """The command line asks for something that does not exist; exit code 2."""


def _say(text: str, stream: TextIO | None = None) -> None:
    """Print as ASCII, at once: the output may be a pipe that another program is waiting on."""
    print(text.encode("ascii", "backslashreplace").decode("ascii"), file=stream or sys.stdout, flush=True)


def _error(text: str) -> None:
    _say(f"pydantic-ai-viz: error: {text}", sys.stderr)


def _load_demos() -> Mapping[str, _Demo]:
    """The bundled demos, imported only when the ``demo`` command needs them."""
    from .demos import DEMOS

    return DEMOS


# ----------------------------------------------------------------- helpers


def _existing_file(value: str, what: str) -> Path:
    path = Path(value)
    if not path.is_file():
        raise _UsageError(f"{what} not found: {value}")
    return path


def _write_outputs(session: Session, args: argparse.Namespace) -> None:
    """Honour ``--record`` and ``--export``."""
    if args.record:
        _say(f"Wrote recording {session.save(args.record)}")
    if args.export:
        _say(f"Wrote HTML export {session.export_html(args.export)}")


def _announce(session: Session, *, open_browser: bool) -> None:
    _say(f"pydantic-ai-viz {__version__} - serving {session.url} (Ctrl+C to stop)")
    if open_browser and session.url is not None:
        open_in_browser(session.url)


class _Scenario:
    """Plays a demo on a worker thread, one run at a time."""

    def __init__(self, demo: _Demo, speed: float) -> None:
        self._demo = demo
        self._speed = speed
        self._lock = threading.Lock()
        self._running = False

    def start(self) -> bool:
        """Start a run unless one is in flight; says which happened."""
        with self._lock:
            if self._running:
                return False
            self._running = True
        threading.Thread(target=self._play, name=f"pydantic-ai-viz-demo-{self._demo.name}", daemon=True).start()
        return True

    def _play(self) -> None:
        try:
            asyncio.run(_scenario(self._demo, self._speed))
        except Exception:
            _say(traceback.format_exc(), sys.stderr)
        finally:
            self._running = False


async def _scenario(demo: _Demo, speed: float) -> object:
    return await demo.run(speed)


# ---------------------------------------------------------------- commands


def _demo(args: argparse.Namespace) -> int:
    demos = _load_demos()
    if args.list:
        for demo in demos.values():
            _say(f"{demo.name} - {demo.description}")
        return EXIT_OK
    if not demos:
        raise _UsageError("no demos are available")
    name = args.name or next(iter(demos))
    if name not in demos:
        raise _UsageError(f"unknown demo {name!r} (available: {', '.join(demos)})")
    if args.speed < 0:
        raise _UsageError("--speed must be 0 or more")
    demo = demos[name]

    if args.record or args.export:
        with record(*demo.targets, stream=True, title=demo.title) as session:
            asyncio.run(_scenario(demo, args.speed))
            _write_outputs(session, args)
        return EXIT_OK

    with serve(*demo.targets, stream=True, port=args.port, title=demo.title) as session:
        scenario = _Scenario(demo, args.speed)
        server = server_of(session)
        assert server is not None
        # Set before the browser is opened: the page learns from its boot data whether it can rerun.
        server.rerun = scenario.start

        def play_for_first_viewer() -> None:
            # Started when a viewer connects, so the run is watched live, not found finished.
            if wait_for_client(session):
                scenario.start()

        threading.Thread(target=play_for_first_viewer, name="pydantic-ai-viz-demo-start", daemon=True).start()
        _announce(session, open_browser=not args.no_open)
        session.wait()
    return EXIT_OK


def _view(args: argparse.Namespace) -> int:
    targets = resolve_targets(args.targets)
    # Nothing is run here, so nothing needs instrumenting.
    if args.export:
        with record(*targets, instrument=False) as session:
            _say(f"Wrote HTML export {session.export_html(args.export)}")
        return EXIT_OK
    with serve(*targets, instrument=False, port=args.port) as session:
        _announce(session, open_browser=not args.no_open)
        session.wait()
    return EXIT_OK


def _script_traceback(exc: BaseException, script: Path) -> str:
    """The traceback as ``python script.py`` would print it: without our own frames above the script's."""
    tb = exc.__traceback__
    while tb is not None and tb.tb_frame.f_code.co_filename != str(script):
        tb = tb.tb_next
    return "".join(traceback.format_exception(type(exc), exc, tb or exc.__traceback__)).rstrip()


def _run(args: argparse.Namespace) -> int:
    script = _existing_file(args.script, "script")
    with serve(stream=args.stream, include_content=not args.no_content, port=args.port, title=script.stem) as session:
        _announce(session, open_browser=not args.no_open)
        namespace: dict[str, Any] = {}
        # The script's agents are not known until it has run, so with --stream the token
        # listener goes on each agent as the script creates it.
        listening = attach_on_creation() if args.stream else contextlib.nullcontext()
        try:
            with script_environment(script, [str(script), *args.args]), listening:
                namespace = runpy.run_path(str(script), run_name="__main__")
        except SystemExit as exc:
            if exc.code not in (None, 0):
                _say(f"{script.name} exited with code {exc.code}; still serving what was captured.", sys.stderr)
        except Exception as exc:
            _say(_script_traceback(exc, script), sys.stderr)
            _say(f"{script.name} raised {type(exc).__name__}; still serving what was captured.", sys.stderr)
        # Full blueprints for whatever the script left behind; the runs themselves were captured live.
        session.register(namespace_module(script.stem, namespace))
        _write_outputs(session, args)
        session.wait()
    return EXIT_OK


def _open(args: argparse.Namespace) -> int:
    recording = _existing_file(args.file, "recording")
    stem = recording.name.removesuffix(RECORDING_SUFFIX).removesuffix(".jsonl") or "recording"
    page = Path(tempfile.mkdtemp(prefix="pydantic-ai-viz-")) / f"{stem}.html"
    export_html(recording, page)
    _say(f"Opening {page}")
    open_in_browser(page.as_uri())
    return EXIT_OK


def _export_command(args: argparse.Namespace) -> int:
    recording = _existing_file(args.file, "recording")
    _say(f"Wrote HTML export {export_html(recording, args.output)}")
    return EXIT_OK


# ------------------------------------------------------------------ parser


def _port(value: str) -> int:
    port = int(value)
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("must be between 0 and 65535")
    return port


def _add_server_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--port",
        type=_port,
        default=DEFAULT_PORT,
        metavar="N",
        help=f"port for the viewer (default {DEFAULT_PORT}); a busy port falls back to a free one",
    )
    parser.add_argument("--no-open", action="store_true", help="do not open a browser; the URL is printed")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pydantic-ai-viz",
        description="Visualize Pydantic AI agents: blueprints, live runs and shareable replays.",
    )
    parser.add_argument("--version", action="version", version=f"pydantic-ai-viz {__version__}")
    commands = parser.add_subparsers(title="commands", metavar="COMMAND")

    demo = commands.add_parser(
        "demo", help="play a bundled, offline demo", description="Play a bundled demo. No API key, no network."
    )
    demo.add_argument("name", nargs="?", metavar="NAME", help="which demo (default: the first one listed)")
    demo.add_argument("--list", action="store_true", help="list the demos and exit")
    demo.add_argument(
        "--speed", type=float, default=1.0, metavar="X", help="playback speed multiplier (default 1.0; 0 = no delays)"
    )
    _add_server_options(demo)
    demo.add_argument("--record", metavar="OUT.pviz.jsonl", help="play once, write a recording and exit (no server)")
    demo.add_argument("--export", metavar="OUT.html", help="play once, write an HTML export and exit (no server)")
    demo.set_defaults(command=_demo)

    view = commands.add_parser(
        "view",
        help="draw the blueprint of agents and graphs without running them",
        description="Draw blueprints. Targets are imported or loaded, but no agent is run and no model is called.",
    )
    view.add_argument(
        "targets",
        nargs="+",
        metavar="TARGET",
        help="pkg.module:attr, pkg.module, path/to/file.py or path/to/file.py:attr",
    )
    view.add_argument("--export", metavar="OUT.html", help="write a blueprint-only HTML file and exit (no server)")
    _add_server_options(view)
    view.set_defaults(command=_view)

    run = commands.add_parser(
        "run",
        help="run a script and watch its agent runs live",
        description="Run SCRIPT as 'python SCRIPT ARGS...' would and capture every agent run. "
        "Options go before SCRIPT; everything after it is passed to the script.",
    )
    run.add_argument("--stream", action="store_true", help="capture tokens as they arrive (forces streaming)")
    run.add_argument("--no-content", action="store_true", help="do not capture prompts, arguments or results")
    run.add_argument("--record", metavar="OUT.pviz.jsonl", help="write a recording when the script ends")
    run.add_argument("--export", metavar="OUT.html", help="write an HTML export when the script ends")
    _add_server_options(run)
    run.add_argument("script", metavar="SCRIPT", help="path to a Python file")
    run.add_argument("args", nargs=argparse.REMAINDER, metavar="ARGS", help="passed to the script unchanged")
    run.set_defaults(command=_run)

    open_ = commands.add_parser(
        "open",
        help="open a recording in the browser",
        description="Convert a recording to an HTML export in a temporary directory and open it. No server.",
    )
    open_.add_argument("file", metavar="FILE.pviz.jsonl", help="the recording to open")
    open_.set_defaults(command=_open)

    export = commands.add_parser(
        "export",
        help="convert a recording to a single HTML file",
        description="Convert a recording to a single self-contained HTML file.",
    )
    export.add_argument("file", metavar="FILE.pviz.jsonl", help="the recording to convert")
    export.add_argument("-o", "--output", required=True, metavar="OUT.html", help="where to write the export")
    export.set_defaults(command=_export_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pydantic-ai-viz`` command line and return its exit code.

    Exit codes: 0 success, 1 runtime error, 2 usage error (bad arguments, unknown
    demo, missing file, target not found). ``--help``, ``--version`` and arguments
    that do not parse leave through ``SystemExit``, as argparse does.

    Args:
        argv: Arguments without the program name. Defaults to ``sys.argv[1:]``.

    Returns:
        The exit code.
    """
    # Pydantic AI prints its banner at the first agent run, not at import, so this is early enough.
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    parser = _build_parser()
    args = parser.parse_args(argv)
    command: Callable[[argparse.Namespace], int] | None = getattr(args, "command", None)
    if command is None:
        parser.print_help()
        return EXIT_OK
    try:
        return command(args)
    except (_UsageError, TargetError) as exc:
        _error(str(exc))
        return EXIT_USAGE
    except KeyboardInterrupt:
        _error("interrupted")
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        _error(str(exc))
        return EXIT_ERROR
    except Exception as exc:
        _error(f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR
