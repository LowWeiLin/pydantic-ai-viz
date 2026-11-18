# Changelog

All notable changes to pydantic-ai-viz are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may contain breaking changes; they are listed under "Changed" or "Removed".

The wire format has its own version number (`pviz`, currently `1`), recorded in the first line of every recording. A change to it is always called out here.

## [Unreleased]

## [0.1.0] - 2026-11-18

The first release. It needs Python 3.11 or newer and Pydantic AI 2.60 or newer (`pydantic-ai-slim>=2.60,<3`, `opentelemetry-sdk>=1.30`). The test suite was run against Pydantic AI 2.60.0 and 2.70.0; the [compatibility page](https://lowweilin.github.io/pydantic-ai-viz/reference/compatibility/) lists every combination that was tested and explains why 2.59 and older are not supported.

pydantic-ai-viz is an unofficial community project. It is not affiliated with or endorsed by Pydantic.

### Added

- Agent blueprints: model, instructions and system prompts, toolsets, tools with JSON schemas, output mode and schema, retries, capabilities and delegation edges, read from an `Agent` without running it.
- Graph blueprints for `pydantic-graph` graphs: nodes, edges, decision labels and notes, and copyable Mermaid.
- Live capture of agent runs in the current process through an OpenTelemetry span processor, with no changes to the code that calls the agent.
- Observed blueprints for agents that were never registered, built from their runs, and delegation edges confirmed by runs.
- Opt-in token streaming overlay (`stream=True`).
- Live graph runs through `pydantic_ai_viz.run_graph`.
- Local viewer server: standard library only, bound to loopback, Server-Sent Events with resume.
- One-file viewer: blueprint canvas with a run overlay, span timeline, inspector, playback with scrubbing, light and dark themes, and keyboard navigation.
- Recordings (`.pviz.jsonl`) and single-file HTML exports.
- Command line: `demo`, `view`, `run`, `open` and `export`.
- Three offline demos (`support`, `research`, `pipeline`) that need no API key.
- Python API: `serve`, `record`, `blueprint`, `export_html`, `run_graph` and `Session`.
- Wire format `pviz` version 1, with a JSON Schema in `schema/pviz.schema.json`.
- Privacy controls: `include_content=False` (`run --no-content`) keeps prompts, tool arguments and tool results out of the log, the stream, recordings and exports. Token streaming is switched off in such a session, because token deltas are content.
- The server refuses requests whose `Host` header is not a loopback name, and sends no CORS headers.
- Blueprint fields that depend on private Pydantic AI attributes degrade to a warning in the viewer when an attribute is missing.
- Documentation site, and a hosted demo that replays recordings of the three demos in the browser.

[Unreleased]: https://github.com/LowWeiLin/pydantic-ai-viz/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/LowWeiLin/pydantic-ai-viz/releases/tag/v0.1.0
