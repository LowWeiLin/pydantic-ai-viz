# Blueprint vs run

pydantic-ai-viz shows two different kinds of information on one canvas, and it is careful to tell you which is which.

| | Blueprint | Run |
|---|---|---|
| Answers | What *is* this agent? | What did it *do*? |
| Source | Your source code, or the `Agent` or `Graph` object | OpenTelemetry spans emitted while it executes |
| Needs a model call | No | Yes (a `TestModel` counts) |
| Available | As soon as the code is written | While and after it runs |
| Covers | Everything the agent could do | The one path this run took |

A blueprint with no runs is an architecture diagram. A run with no blueprint is a trace. Put together, the run is drawn *on* the blueprint: you see which of the available tools were called, which were not, and where the time went.

## Three ways to get a blueprint

Every blueprint, and every tool and delegation edge in it, records how it was learnt. There are three ways, called **provenances**, and the viewer marks each one:

| Provenance | Made by | Runs the code | Knows | Marked in the viewer |
|---|---|---|---|---|
| `code` | `pydantic-ai-viz view`, `pviz.analyze(...)` | No | What the source spells out, and where each part is defined | "From source" |
| `object` | `serve`, `record` or `blueprint` with an agent, `view --import` | The import did, before | Everything introspection can reach, including Pydantic AI's defaults | "From a live object" |
| `observed` | Runs of an agent nobody registered | Yes, it is a run | What the runs have shown so far | "Observed in runs" |

The same three values appear as a blueprint's `source`, a tool's `origin` and a delegation edge's `evidence` in the [wire format](../reference/wire-format.md#provenance).

### Read from source code

`pydantic-ai-viz view` and `pviz.analyze(...)` parse a file, a directory, a module or a GitHub repository and describe the agents and graphs written in it, following imports between the project's files. Nothing is imported or executed. The result has `source: "code"`, says where each thing is defined, and shows a value the code computes when it runs as the expression that computes it. See [How view works](how-view-works.md).

### Read from the object

When you pass an agent to `serve`, `record` or `blueprint` (or use `view --import`), the live object is introspected. No agent is run. The result has `source: "object"` and is as complete as introspection allows: every tool with its schemas, static instructions, the names of dynamic instruction functions, output type and schema, retry limits, capabilities. A harness `Skills` capability lists its skills, read with `Skills.load()` from a host workspace without running a model.

In a run, a call of `load_capability` is shown with the skill or capability it loaded.

### Observed: pieced together from runs

Capture is process-wide, so the viewer also sees runs of agents nobody told it about: one created inside a function, one built by a framework, one you forgot to pass. For those there is no object to read, so a blueprint is assembled from what the runs reveal. It has `source: "observed"` and the viewer marks it "Observed in runs".

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
| `code` | Dashed, "in code" | Reading the source: the tool runs an agent the analyser can trace, through a variable, an import, a written-out dict or list, or `ctx.deps` when the source shows which agent is passed as that dependency | A good guess. The call might be on a branch that never runs. |
| `object` | Dashed, "in code" | Introspecting the live tool function: its code refers to an `Agent` object in a module-level variable or an enclosing scope | A good guess, for the same reason. |
| `observed` | Solid, "in a run" | During a run, an agent run started inside that tool call | A fact about at least one run. |

A dashed edge turns solid the first time the delegation actually happens. An edge that reading the code missed, for example because a helper function runs the agent for the tool, simply appears, solid, when it first happens.

Reading a canvas after a few runs:

- **Solid edge:** this delegation happens.
- **Dashed edge that stays dashed:** the code allows it, but none of the runs you have looked at took that path. That is often worth knowing.
- **No edge:** nothing in the tool's own code refers to another agent, and no run has delegated from it.

## Identity

Every blueprint has an opaque `id` (`a1`, `a2`, ... for agents, `g1`, ... for graphs) that is stable for the life of a session, and separate from the agent's `name`. That separation is what lets a blueprint be updated in place:

- an unnamed agent gets its name on first run and keeps its `id`,
- a static blueprint registered after an observed one of the same name takes over that `id`,
- every update is an upsert: the viewer replaces the blueprint with that `id` and keeps your selection.

Runs are matched to blueprints by agent name. At most one blueprint in a session that records runs has any given name (a `view` session has no runs, so it shows every agent under its own name). If two different agents in your program share a name, their runs are drawn on the same blueprint; give agents distinct names to keep them apart.

## Runs

A run is a trace: all the spans that share a trace id. Its root is the top-level agent run, or the graph run when you use [`run_graph`](../guides/graphs.md). Runs of delegated agents are part of their parent's run, nested under the tool call that started them, which is why one run can involve several blueprints and the canvas has a switcher between them.

Spans come in five kinds: `agent_run`, `model_request`, `tool_call`, `graph_run` and `graph_node`. Anything else that reaches the session (for example spans from other instrumentation when you [share a tracer provider with Logfire](../guides/logfire.md)) is kept as `other` and shown as a plain row.

What a run can show depends on when you look: a span that has started but not ended has its name, its place in the tree and, for a tool call, its arguments, but not yet a result, usage or messages. [How capture works](how-capture-works.md) explains why.

## Warnings

When part of a blueprint could not be read, the blueprint says so. Its `warnings` list holds a plain sentence per problem, the agent card shows a badge, and the inspector lists them.

- In a blueprint read **from source**, a warning names a value that only running the code could tell, such as "the model is set by `get_model()`, so it is only known when the code runs". See [How view works](how-view-works.md#what-shows-as-decided-at-run-time-and-why).
- In a blueprint read **from an object**, the usual cause is a Pydantic AI release that renamed an internal attribute; the affected field is left empty and everything else works. See [Compatibility](../reference/compatibility.md).
