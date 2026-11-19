# Changelog

All notable changes to pydantic-ai-viz are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may contain breaking changes; they are listed under "Changed" or "Removed".

The wire format has its own version number (`pviz`, currently `1`), recorded in the first line of every recording. A change to it is always called out here.

## [Unreleased]

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
