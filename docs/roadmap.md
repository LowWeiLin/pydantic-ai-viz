# Roadmap

What has been considered and left for later. Nothing here is promised or scheduled; it is written down so that a request for one of these finds the reasoning, and so that nobody mistakes a gap for an oversight. To argue for one, [open an issue](https://github.com/LowWeiLin/pydantic-ai-viz/issues).

## Viewer

- **Languages other than English.** The viewer's text is English only. Translating it means moving every string into a catalogue and keeping the backend's warnings, which the viewer shows word for word, translatable too. Deferred until the wording has settled.
- **Cost estimates, run diffing and search.** Out of scope for the first release; see the limits in the [README](https://github.com/LowWeiLin/pydantic-ai-viz#limits-and-non-goals).
- **A link to a toolset on a run.** On a blueprint every pick is in the page's address. On a run only spans are, and a toolset has no span of its own.
- **The tools a run saw, in the panel of an MCP or dynamic toolset.** They are drawn on the canvas, in the "seen in run" group, because which toolset they came from is not recorded.
- **Capability events without opting in.** `CapabilityEvent`s and `CustomEvent`s are shown on runs only for an agent given the `CaptureEvents` capability. Pydantic AI does not put them in its telemetry, and the viewer does not add a listener to an agent behind your back.
- **The tools of an MCP server, before a run.** Listing them means connecting to the server, or starting it from its config file, which runs code that `view` and `blueprint` never run. A run shows the tools it was offered, but its tool spans do not say which toolset a tool came from, hence the "seen in run" group above.
- **The files `RepoContext` injects.** The harness has no public way to read them, and which `AGENTS.md` and `CLAUDE.md` files it finds depends on the run's workspace and its walk-up and nested-folder rules, so reproducing them would be guesswork. The run's instructions already show the text it injected.
- **Delegation edges for `Peers`.** Peers join a network (`InMemoryPeerNetwork`, a `PeerConnection`) when a run starts and are addressed by a handle, not held as agent objects, so there is no agent to draw an edge to.
- **What a hook did in a run.** The functions of a `Hooks` capability are listed, but a hook that changes or blocks something leaves no span or attribute in Pydantic AI's telemetry, so a run cannot mark it.
- **Usage limits.** `UsageLimits` are passed to each run call, not held by the agent, and are not recorded in spans. A limit that was exceeded already shows as the run's `UsageLimitExceeded` error.
- **Approval decisions made inside a run.** `ToolCallJudge`, `ApprovalReviewer` and `HandleDeferredToolCalls` answer approvals without telemetry, so their decisions are not on the timeline, unless they emit capability events, which `CaptureEvents` shows.
- **Retries asked for by an output validator.** An `@agent.output_validator` that raises `ModelRetry` leaves no span or attribute; the extra model request is all a run shows.
- **Durable activity boundaries.** An agent wrapped in `TemporalAgent`, `DBOSAgent` or `PrefectAgent` is recognised and badged, but which spans of a run ran as one activity or step is not shown. It was not checked against a real Temporal, DBOS or Prefect runtime.
- **How a run was started.** Runs started through A2A, AG-UI, the Vercel AI adapter, ACP or the web chat (`to_a2a()`, `to_web()`, `AGUIApp`) look like any other run. A live agent keeps no trace of these, so only its source could show them, and a live and a source blueprint would disagree; runs do not record the interface either.
- **Evals.** Showing a `Dataset` of `Case`s, its `Evaluator`s and a report over the runs needs a data model of its own, a new kind of blueprint and new screens. A large piece of work, left for later.
- **Realtime sessions.** Speech-to-speech sessions have audio and transcript turns and interruptions, which need new span kinds and a timeline mode of their own. A large piece of work, left for later.
- **A `Researcher` with a store of its own, from a live object.** A live `Coder` or `Researcher` is listed as itself, found by a marker that Pydantic AI's harness leaves among its capabilities. A `Researcher` whose overflow store is not the workspace's (for example `Researcher(instructions=None, store=LocalFileOverflowStore())`) leaves no marker, so only the capabilities it is made of are listed.

## Reading source code

These patterns are not read by `view` today; see [How view works](concepts/how-view-works.md) and the [real-world examples](real-world.md) for how often they occur.

- **Following arguments into any function.** They are followed into a function that makes an agent, one agent per call. Elsewhere a function's parameters are unknown inside it, so `make_model("gpt-5.2")` stays an expression even when `make_model` only joins strings.
- **Tools registered on an agent that is passed in as an argument.** They are counted in a notice and placed on no agent.
- **Toolsets that a capability class of the project builds at run time** and returns from `get_toolset`.
- **The toolset of an `MCP(...)` capability.** Its URL is read, the toolset is not.
- **The tools of harness capabilities, from source.** A harness capability that gives a toolset (`FileSystem`, `Shell`, `Memory`, `Planning`, `ToolOutputLimits`, `PydanticAIDocs`, `PruneToolResults`, `CapabilityCreation`) is drawn from source as one toolset marked "decided at run time", because the tools it offers depend on its arguments and the harness is never imported. Harness capabilities have no provider-side tools from source. Read the live agent for the tools.
- **Agents not constructed in the source**: `Agent` subclasses and agents returned by library functions. (`Agent.from_file(...)` with a literal path and `Agent.from_spec({...})` with a written-out dict are read.)
- **Types from installed packages.** Their schema could be read from the package's source without importing it, but installed packages are not opened today, on purpose; the one exception is the installed Pydantic AI, for the tools it defines.

Until then, `view --import` reads the live objects instead, on code you trust: whatever exists once the module has been imported is shown, computed or not.
