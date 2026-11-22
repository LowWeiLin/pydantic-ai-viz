# Changelog

All notable changes to pydantic-ai-viz are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, a minor release may contain breaking changes; they are listed under "Changed" or "Removed".

The wire format has its own version number (`pviz`, currently `1`), recorded in the first line of every recording. A change to it is always called out here.

## [Unreleased]

### Added

- Capabilities are recognised and grouped like the Pydantic AI capabilities docs (harnesses, execution environments, tools, integrations, web, reasoning, context, knowledge, safety, self-extension, runtime, loop, and custom for the rest), with what each was given, the toolsets it gives the agent and its provider-side tools, from source and from live agents.
- Capabilities are drawn on the canvas, in a box under the agent card in the docs' category order, with a dashed line to each toolset they give. A capability can be picked on the canvas and has its own panel; the capabilities in an agent's panel and in a toolset's panel are links. A pick is in the page's address (`?part=capability:<index>`).
- A live `Coder` or `Researcher` of `pydantic-ai-harness` is listed as itself, with category `harness`; its private helper capabilities are left out. A `Researcher` with an overflow store of its own cannot be recognised and is not listed.
- Static analysis opens `CombinedCapability([...])` into the capabilities in it (except one with a literal `defer_loading=True`), gives `WebSearch` and `WebFetch` the toolset of their `local=` function, and gives each harness capability that has a toolset one opaque toolset.
- The capabilities demo (`pydantic-ai-viz demo capabilities`): one agent that uses most categories of capability, offline. It needs `pip install 'pydantic-ai-viz[demos]'`, which installs `pydantic-ai-harness` with its `skills` extra.
- `BenchmarkInstructions`, `BaseDurabilityCapability` and the deprecated harness aliases `InputGuard`, `OutputGuard` and `BubblewrapSandbox` are categorised.
- Wire format: `CapabilityInfo` gains `category`, `package`, `deferred`, `details`, `expressions`, `toolsets` and `native_tools`. The change is additive within `pviz` 1. The viewer still opens recordings made by 0.1.0, treating their capabilities as custom with nothing more known; the Python models and the JSON Schema require the new fields.

- Skills are inspectable. Every skill of a harness `Skills` capability shows by name with its description, when to use it, whether the model may load it, its `SKILL.md` and its body, from a live agent (read with `Skills.load()` from host workspaces, without running a model), from source (`SKILL.md` files under literal `directories` inside the analysed folder) and in runs, where a `load_capability` call is linked to the skill or capability it loads. Skills are rows under the `Skills` capability on the canvas and have their own panel. A GitHub target keeps its `SKILL.md` files.
- A "Pydantic AI defaults" toggle in the agent and run views. The default view shows what you wrote; the capabilities, tools, toolsets and settings Pydantic AI or the harness adds on its own are collapsed into counts, rolled up on the capability rows and lit by runs, and shown again by the toggle. The choice is remembered in the browser.
- `SubAgents` of `pydantic-ai-harness` draws delegation edges, from live agents and from source.
- Static analysis gives toolsets to `SubAgents`, `AskUser`, `Advisor` and `Skills(read_files=True)`, and reads the positional arguments of harness capabilities (`Skills`, `SubAgents`, `Advisor`, `CapabilityCreation`, `RepoContext`, `Peers`, `Memory`).
- The capabilities demo bundles a second skill, `fact-check`, which its run does not load.
- Wire format: `CapabilityInfo` gains `capability_id`, `stack` and `skills`, with the new `SkillInfo` type, and `AgentSettings` gains `defaults`. The change is additive within `pviz` 1. The viewer opens older recordings with `null`, `null`, `[]` and `[]`; the Python models and the JSON Schema require the fields.

- Hooks. The functions a `Hooks` capability registers are listed by hook point, with the tools a tool hook is limited to and its timeout: from a live agent's registry, and from source (the keywords of `Hooks(...)` and each `@hooks.on.<point>` decorator). They are rows under the capability on the canvas and a section of its panel.
- A literal `SubAgents(agents=[SubAgent(...)])` read from source shows its entries in `details`, as a live one does.
- Approvals and deferred tool calls. A tool given `requires_approval=True` is tagged "needs approval" on the canvas, and an external one "runs outside". A run that ends with `DeferredToolRequests` is marked "waiting for approval" with the calls it waits for, a tool that raised `ApprovalRequired` or `CallDeferred` says so, and the run that answers them (same conversation) is linked to it with each call's outcome: "resumes oncall 12:05:09: 1 approved, 1 denied". Without content capture only tool names and call ids are kept.
- Agents made from a spec are read from source: `Agent.from_file(...)` with a literal path and `Agent.from_spec({...})` with a written-out dict, with keyword overrides applied as `from_spec` applies them. A `.json`, `.yaml` or `.yml` spec can be given to `view` as a target.
- `pydantic_ai_viz.CaptureEvents`, an opt-in capability that records an agent's `CapabilityEvent`s and `CustomEvent`s on its runs. They are diamonds on the timeline, an Events tab in the span panel and a count on the capability that emitted them. An event's fields are content, recorded only when content is captured. It is left out of blueprints.
- Output validators. The `@agent.output_validator` functions of an agent are listed under its output, live and from source, and the retries of a text output function (`pydantic_ai.output.retry.*`) are shown as retries.
- Durable-execution wrappers. `blueprint()`, `serve`, `record`, `export_html` and `view` accept a `TemporalAgent`, `DBOSAgent` or `PrefectAgent`, show the agent it wraps and badge it with the runtime; static analysis marks an agent the source hands to one of them.
- The operations demo (`pydantic-ai-viz demo operations`): an on-call agent delegates through `SubAgents`, is watched by hooks, waits for approval to restart a service and is resumed with one call approved and one denied, with an output validator and an event recorded by `CaptureEvents`. Like the capabilities demo, it needs `pip install 'pydantic-ai-viz[demos]'`.
- Wire format: `CapabilityInfo` gains `hooks`, with the new `HookInfo` type; `AgentRunData` gains `deferred`, with the new `DeferredCall` type; `ToolCallData` gains `deferral`; every span gains `events`, with the new `RunEvent` type; the agent's `output` gains `validators`; and `AgentBlueprint` gains `wrapper`. The change is additive within `pviz` 1. The viewer opens older recordings with `[]`, `[]`, `null`, `[]`, `[]` and `null`; the Python models and the JSON Schema require the fields.

### Fixed

- The details of a live capability no longer list values the class fills in itself (the `denied_commands` of a `Shell()`, the method of an `Advisor`), and a plain `Capability(instructions=...)` shows its instructions.
- Harness capabilities given arguments by position (`Skills("skills")`) are read from source instead of dropped.
- The fixture generator's `Skills` is no longer marked deferred, and its `Memory` and `Planning` toolsets have no invented expression, as in real output.
- `WebSearch` and `WebFetch` given native-only constraints (`allowed_domains`, `blocked_domains`, `max_uses`) no longer show a local toolset from source, as on a live agent.
- Capabilities that Pydantic AI merges into one (the same literal `id=`, or a core class with a default id, such as two `WebSearch()`) are one capability from source, as on a live agent.
- A delegation arc back to an agent above its own card is drawn behind the sub-agent card and its capabilities box instead of through them.
- A live agent's `native_tools` no longer lists the function `CombinedCapability` puts in place of the native tool of a capability loaded on demand; it lists the tool's kind.
- Static analysis no longer lists `pydantic_ai_slim` among the libraries it knows by name; it is not importable.
- A live `Skills(include=...)` or `Skills(exclude=...)` shows its selection; it was dropped because the harness keeps it as a set. From source the names are sorted and de-duplicated the same way.
- An expected retry (a `ModelRetry` from a tool or an output function) keeps its error type and message but no stack trace; real errors keep theirs.
- Static analysis leaves out `CaptureEvents` when it follows the import into this package's own source, as in the bundled demos.

## [0.1.0] - 2026-11-19

The first release. It needs Python 3.11 or newer and Pydantic AI 2.60 or newer (`pydantic-ai-slim>=2.60,<3`, `opentelemetry-sdk>=1.30`). The test suite was run against Pydantic AI 2.60.0 and 2.70.0; the [compatibility page](https://lowweilin.github.io/pydantic-ai-viz/reference/compatibility/) lists every combination that was tested and explains why 2.59 and older are not supported.

pydantic-ai-viz is an unofficial community project. It is not affiliated with or endorsed by Pydantic.

### Added

- Agent blueprints: model, instructions and system prompts, toolsets, tools with JSON schemas, output mode and schema, retries, capabilities and delegation edges, read from source code or from an `Agent` object without running it.
- Graph blueprints for `pydantic-graph` graphs: nodes, edges, decision labels and notes, and copyable Mermaid.
- Static analysis: `pydantic-ai-viz view TARGET` and `pydantic_ai_viz.analyze(...)` read agents and graphs out of Python source with the standard `ast` module. The code is never imported or executed, so its dependencies need not be installed and code that has not been reviewed can be looked at safely. Values are followed by one evaluator of the language (names, imports, attributes, the project's own functions, written-out containers, `**kwargs`, loops over written-out lists, inheritance); the only library knowledge is that of Pydantic AI, pydantic-graph, pydantic, typing and a closed list of standard-library calls. A value the source computes at run time, such as what another library returns, is shown as the expression that computes it, with one plain warning; nothing is guessed. Everything found carries its file and line. Graphs made with `GraphBuilder` are read, including `BaseNode` classes whose edges come from return annotations, and so is the older `Graph(nodes=[...])` form.
- `view` understands a closed list of standard-library calls: a model read with `os.getenv("X", default)`, `os.environ.get(...)` or `os.environ["X"]`, directly or through the project's own helpers, is shown as `env X`, with the model class and the source's default labelled as a default, never as the value. `textwrap.dedent` and `inspect.cleandoc` on a literal string give the text Python would.
- Instruction and system-prompt templates (f-strings and `.format`) are shown whole, with the parts filled in at run time marked.
- Tools that Pydantic AI defines, such as `duckduckgo_search_tool()`, get their name, description and parameters from the source of the installed Pydantic AI, read without importing it; the panel names the version read.
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

[0.1.0]: https://github.com/LowWeiLin/pydantic-ai-viz/releases/tag/v0.1.0
