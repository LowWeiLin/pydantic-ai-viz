# Changelog

All notable changes to pydantic-ai-viz are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may contain breaking changes; they are listed under "Changed" or "Removed".

The wire format has its own version number (`pviz`, currently `1`), recorded in the first line of every recording. A change to it is always called out here.

## [Unreleased]

### Changed

- Wire format (still `pviz` 1): the rule that at most one blueprint has any given `name` now holds for `"capture"` sessions only. A `"blueprint"` session, such as `view`, has no runs to match by name, so two agents named alike in different files both keep their name and get no warning.
- Live transport: `GET /api/v1/events` starts with one event named `backlog`, before any envelope, that gives the `seq` of the newest event logged when the connection was opened. The envelopes and their framing are unchanged; a reader that wants only envelopes skips events with a name.

### Fixed

- Static analysis never reads outside the project: imports are not followed through links or junctions, and the home, temp or drive-root directory is never taken for a project root. Files of mixed targets are never named by an absolute path.
- A function that makes agents gives one agent per call, each with that call's arguments; calls that end in the same blueprint are shown once.
- An agent is still found when its class name is bound in more than one way (`try: from pydantic_ai import Agent` / `except ImportError: Agent = None`), with a warning; a registration method held in a name is followed.
- An agent passed to a call is a delegate only where the call runs it, not when it is logged or compared.
- The output mode follows Pydantic AI's own rule (`[str, Answer]` is `tool`, `str | None` is `text`), `ToolOutput(type_=...)` is read, and `allows_text` is filled in.
- A pydantic model's `model_config` (`title`, `extra`, `use_attribute_docstrings`, `json_schema_extra`) shapes its schema; a computed configuration gives a warning.
- `str.format` with written-out arguments is read like any other value; a prompt template filled in at run time, from an f-string or `.format`, gives a warning. A computed instruction reads the same whether written inline or through a name.
- A tool's `docstring_format` is used; a decorator applied before a tool is registered, a deps type held by a variable, and a skipped `build` or `dist` directory that holds Python are each named.
- The viewer shows a warning count on the agent card, keeps the ellipsis on a cut path's folder, and no longer shows the placeholder id `<agent>`.
- A live viewer waiting on a blueprint, or watching a run, follows the next run even when that run starts and ends between two of its frames (a fast agent, or a busy page); before, such a run was taken for history and the viewer stayed where it was.

## [0.1.0] - 2026-11-18

The first release. It needs Python 3.11 or newer and Pydantic AI 2.60 or newer (`pydantic-ai-slim>=2.60,<3`, `opentelemetry-sdk>=1.30`). The test suite was run against Pydantic AI 2.60.0 and 2.70.0; the [compatibility page](https://lowweilin.github.io/pydantic-ai-viz/reference/compatibility/) lists every combination that was tested and explains why 2.59 and older are not supported.

pydantic-ai-viz is an unofficial community project. It is not affiliated with or endorsed by Pydantic.

### Added

- Agent blueprints: model, instructions and system prompts, toolsets, tools with JSON schemas, output mode and schema, retries, capabilities and delegation edges, read from source code or from an `Agent` object without running it.
- Graph blueprints for `pydantic-graph` graphs: nodes, edges, decision labels and notes, and copyable Mermaid.
- Static analysis: `pydantic-ai-viz view TARGET` and `pydantic_ai_viz.analyze(...)` read agents and graphs out of Python source with the standard `ast` module. The code is never imported or executed, so its dependencies need not be installed and code that has not been reviewed can be looked at safely. Values are followed by one evaluator of the language (names, imports, attributes, the project's own functions, written-out containers, `**kwargs`, loops over written-out lists, inheritance); the only library knowledge is that of Pydantic AI, pydantic-graph, pydantic and typing. A value the source computes at run time, such as an environment variable or what another library returns, is shown as the expression that computes it, with one plain warning and no default taken from it; nothing is guessed. Everything found carries its file and line. Graphs made with `GraphBuilder` are read, including `BaseNode` classes whose edges come from return annotations, and so is the older `Graph(nodes=[...])` form.
- Targets for `view` and `analyze`: a file, a directory (every `.py` file below it; virtual environments, `node_modules`, build output and hidden directories are passed over), a module name (found on disk without importing anything) and a GitHub repository (`https://github.com/owner/repo[/tree/REF[/SUBDIR]]` or `gh:owner/repo[@REF][/SUBDIR]`, fetched with git at a branch, a tag or a full commit SHA into a cache directory, and only read; `--import` is refused for it). Imports between a project's own files are followed, so tools, output models, toolsets and delegation link up across files. Installed packages are never opened.
- Static analysis follows the ways larger projects put an agent together: arguments passed as `**kwargs` from a dict the source writes out, tools registered in a loop, by a function that is handed the agent or by a class derived from `FunctionToolset`, an agent kept on `self` in a base class, and toolset classes that wrap a toolset. What can be counted but not placed, such as tools registered on an agent that no call shows, is said in a notice.
- A real-world test set: twelve public projects that use Pydantic AI, each pinned to a commit in `tests/realworld/manifest.toml`, on which the static analyser is checked once a week in CI and on request with `uv run pytest -m network tests/realworld`. None of their code is kept in this repository or run.
- Live capture of agent runs in the current process through an OpenTelemetry span processor, with no changes to the code that calls the agent.
- Observed blueprints for agents that were never registered, built from their runs, and delegation edges confirmed by runs.
- Opt-in token streaming overlay (`stream=True`).
- Live graph runs through `pydantic_ai_viz.run_graph`.
- Local viewer server: standard library only, bound to loopback, Server-Sent Events with resume. An open viewer starts over by itself when the program it watches is restarted on the same port.
- One-file viewer: blueprint canvas with a run overlay, span timeline, inspector, playback with scrubbing, light and dark themes, and keyboard navigation. A selected span, or a part picked on a blueprint, is in the page's link, so the link opens on it.
- Recordings (`.pviz.jsonl`) and single-file HTML exports.
- Command line: `demo`, `view`, `run`, `open` and `export`. `view` reads source code; `view --import` imports the targets and reads the live objects instead, which executes their code. `run --record` and `run --export` also write their files when the script is stopped with Ctrl+C.
- Three offline demos (`support`, `research`, `pipeline`) that need no API key.
- Python API: `serve`, `record`, `analyze`, `blueprint`, `export_html`, `run_graph` and `Session`.
- Wire format `pviz` version 1, with a JSON Schema in `schema/pviz.schema.json`. Every blueprint fact says whether it was read from code, read from a live object or observed in a run, and a session says whether runs can appear in it.
- Privacy controls: `include_content=False` (`run --no-content`) keeps prompts, tool arguments and tool results out of the log, the stream, recordings and exports. Token streaming is switched off in such a session, because token deltas are content.
- The server refuses requests whose `Host` header is not `localhost` or a loopback address, and sends no CORS headers.
- Blueprint fields that depend on private Pydantic AI attributes degrade to a warning in the viewer when an attribute is missing.
- Documentation site, with a page on how `view` works, one on the real-world set and its results, and a roadmap of what was left for later. A hosted demo replays recordings of the three demos in the browser, and shows the demos' source code as `view` reads it.

[Unreleased]: https://github.com/LowWeiLin/pydantic-ai-viz/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LowWeiLin/pydantic-ai-viz/releases/tag/v0.1.0
