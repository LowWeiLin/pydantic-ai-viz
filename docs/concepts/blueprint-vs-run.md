# Blueprint vs run

pydantic-ai-viz shows two different kinds of information on one canvas, and it is careful to tell you which is which.

| | Blueprint | Run |
|---|---|---|
| Answers | What *is* this agent? | What did it *do*? |
| Source | The `Agent` or `Graph` object | OpenTelemetry spans emitted while it executes |
| Needs a model call | No | Yes (a `TestModel` counts) |
| Available | As soon as the object exists | While and after it runs |
| Covers | Everything the agent could do | The one path this run took |

A blueprint with no runs is an architecture diagram. A run with no blueprint is a trace. Put together, the run is drawn *on* the blueprint: you see which of the available tools were called, which were not, and where the time went.

## Two ways to get a blueprint

### Static: read from the object

When you pass an agent to `serve`, `record`, `view` or `blueprint`, it is introspected. Nothing is executed. The result has `source: "object"` and is as complete as introspection allows: every tool with its schemas, static instructions, the names of dynamic instruction functions, output type and schema, retry limits, capabilities.

### Observed: pieced together from runs

Capture is process-wide, so the viewer also sees runs of agents nobody told it about: one created inside a function, one built by a framework, one you forgot to pass. For those there is no object to read, so a blueprint is assembled from what the runs reveal. It has `source: "observed"` and the viewer marks it "Observed from runs".

| When this happens | The observed blueprint gains |
|---|---|
| The agent's first run starts | Name, description and model name |
| A model request ends | The tools the model was offered, with parameter schemas, grouped by toolset; the output tools; the resolved instructions of the first request. Each later request adds the tools not seen before. |

An observed blueprint is accurate, but it only knows what runs have shown. It cannot know which instructions are dynamic, which function implements a tool, or about tools that no request has offered yet. Passing the agent as a target replaces the observed blueprint with a static one, in place.

### The two also fill each other in

A static blueprint is not frozen either. Runs add what introspection could not reach:

- **Tools of opaque toolsets.** An MCP server or a toolset built at run time is one placeholder node until a run shows which tools it provides. Those tools are then added, each marked `origin: "observed"`.
- **The agent's name.** An agent created without `name=` has none until its first run, when Pydantic AI infers it from the variable name. The blueprint keeps its identity and gains the name. Inference needs the agent to be called through a plain variable; when it is not, see [Runs do not show up on an agent I registered](../troubleshooting.md#runs-do-not-show-up-on-an-agent-i-registered).
- **Output details** that could not be read statically.

## Evidence on delegation edges

An edge from a tool to another agent says "this tool runs that agent". Because that claim can come from two very different sources, every edge carries its evidence.

| Evidence | Drawn as | How it was found | How far to trust it |
|---|---|---|---|
| `object` | Dashed | The tool function's code refers to an `Agent` object: a module-level variable or one from an enclosing scope | A good guess. The reference might be on a branch that never runs. |
| `observed` | Solid | During a run, an agent run started inside that tool call | A fact about at least one run. |

A dashed edge turns solid the first time the delegation actually happens. An edge that static analysis missed, because the agent is reached through `ctx.deps`, an attribute or a helper function, simply appears, solid, when it first happens.

Reading a canvas after a few runs:

- **Solid edge:** this delegation happens.
- **Dashed edge that stays dashed:** the code allows it, but none of the runs you have looked at took that path. That is often worth knowing.
- **No edge:** nothing in the tool's own code refers to another agent, and no run has delegated from it.

## Identity

Every blueprint has an opaque `id` (`a1`, `a2`, ... for agents, `g1`, ... for graphs) that is stable for the life of a session, and separate from the agent's `name`. That separation is what lets a blueprint be updated in place:

- an unnamed agent gets its name on first run and keeps its `id`,
- a static blueprint registered after an observed one of the same name takes over that `id`,
- every update is an upsert: the viewer replaces the blueprint with that `id` and keeps your selection.

Runs are matched to blueprints by agent name. At most one blueprint in a session has any given name. If two different agents in your program share a name, their runs are drawn on the same blueprint; give agents distinct names to keep them apart.

## Runs

A run is a trace: all the spans that share a trace id. Its root is the top-level agent run, or the graph run when you use [`run_graph`](../guides/graphs.md). Runs of delegated agents are part of their parent's run, nested under the tool call that started them, which is why one run can involve several blueprints and the canvas has a switcher between them.

Spans come in five kinds: `agent_run`, `model_request`, `tool_call`, `graph_run` and `graph_node`. Anything else that reaches the session (for example spans from other instrumentation when you [share a tracer provider with Logfire](../guides/logfire.md)) is kept as `other` and shown as a plain row.

What a run can show depends on when you look: a span that has started but not ended has its name, its place in the tree and, for a tool call, its arguments, but not yet a result, usage or messages. [How capture works](how-capture-works.md) explains why.

## Warnings

When part of a blueprint could not be read, the blueprint says so. Its `warnings` list holds a plain sentence per problem, the agent card shows a badge, and the inspector lists them. The usual cause is a Pydantic AI release that renamed an internal attribute; the affected field is left empty and everything else works. See [Compatibility](../reference/compatibility.md).
