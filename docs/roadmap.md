# Roadmap

What has been considered and left for later. Nothing here is promised or scheduled; it is written down so that a request for one of these finds the reasoning, and so that nobody mistakes a gap for an oversight. To argue for one, [open an issue](https://github.com/LowWeiLin/pydantic-ai-viz/issues).

## Viewer

- **Languages other than English.** The viewer's text is English only. Translating it means moving every string into a catalogue and keeping the backend's warnings, which the viewer shows word for word, translatable too. Deferred until the wording has settled.
- **Cost estimates, run diffing and search.** Out of scope for the first release; see the limits in the [README](https://github.com/LowWeiLin/pydantic-ai-viz#limits-and-non-goals).
- **A link to a toolset on a run.** On a blueprint every pick is in the page's address. On a run only spans are, and a toolset has no span of its own.
- **The tools a run saw, in the panel of an MCP or dynamic toolset.** They are drawn on the canvas, in the "seen in run" group, because which toolset they came from is not recorded.
- **Capability events.** A `CapabilityEvent` emitted with `ctx.emit` or `@on_event` is not shown on runs.
- **A `Researcher` with a store of its own, from a live object.** A live `Coder` or `Researcher` is listed as itself, found by a marker that Pydantic AI's harness leaves among its capabilities. A `Researcher` whose overflow store is not the workspace's (for example `Researcher(instructions=None, store=LocalFileOverflowStore())`) leaves no marker, so only the capabilities it is made of are listed.

## Reading source code

These patterns are not read by `view` today; see [How view works](concepts/how-view-works.md) and the [real-world examples](real-world.md) for how often they occur.

- **Following arguments into any function.** They are followed into a function that makes an agent, one agent per call. Elsewhere a function's parameters are unknown inside it, so `make_model("gpt-5.2")` stays an expression even when `make_model` only joins strings.
- **Tools registered on an agent that is passed in as an argument.** They are counted in a notice and placed on no agent.
- **Toolsets that a capability class of the project builds at run time** and returns from `get_toolset`.
- **The toolset of an `MCP(...)` capability.** Its URL is read, the toolset is not.
- **The tools of harness capabilities, from source.** A harness capability that gives a toolset (`FileSystem`, `Shell`, `Memory`, `Planning`, `ToolOutputLimits`, `PydanticAIDocs`, `PruneToolResults`, `CapabilityCreation`) is drawn from source as one toolset marked "decided at run time", because the tools it offers depend on its arguments and the harness is never imported. Harness capabilities have no provider-side tools from source. Read the live agent for the tools.
- **Agents not constructed in the source**: `Agent` subclasses, `Agent.from_spec(...)` and agents returned by library functions.
- **Types from installed packages.** Their schema could be read from the package's source without importing it, but installed packages are not opened today, on purpose; the one exception is the installed Pydantic AI, for the tools it defines.

Until then, `view --import` reads the live objects instead, on code you trust: whatever exists once the module has been imported is shown, computed or not.
