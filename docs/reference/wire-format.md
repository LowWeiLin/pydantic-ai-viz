# Wire format

A pydantic-ai-viz session is an append-only log of events. The same events travel over the live connection, fill a `.pviz.jsonl` recording and sit inside an HTML export. This page specifies them.

- **Name and version:** `pviz`, version `1`. The version is the `pviz` field of the first event.
- **Machine-readable schema:** [`schema/pviz.schema.json`](https://github.com/LowWeiLin/pydantic-ai-viz/blob/main/schema/pviz.schema.json) (JSON Schema 2020-12), generated from the Python models. It describes one event.
- **Source of truth:** the Pydantic models in `src/pydantic_ai_viz/_models.py`.

The types below are written in TypeScript notation because it is compact. `Json` means any JSON value.

## Rules for every event { #rules }

1. **Every listed field is always present.** A value that is unknown or does not apply is `null`, never left out.
2. **Readers ignore what they do not know:** unknown fields, and events with an unknown `type`. This is what lets a newer writer work with an older reader.
3. **Times are milliseconds since the Unix epoch,** as JSON numbers with a fractional part (microsecond precision). The envelope's `ts` and a span's `start_ms` and `end_ms` are on the same clock.
4. **Ids are lowercase hex:** `trace_id` is 32 characters, `span_id` and `parent_id` are 16.
5. **Long strings are cut.** Any string longer than 32,768 characters is truncated and ends with `... [truncated, N chars total]`.
6. **Content can be absent.** When the session's `include_content` is `false`, content-bearing fields are `null`.

## Envelope and events { #events }

Each event is one JSON object: an envelope with a sequence number and a timestamp, plus the fields of one event type.

```ts
type Envelope = { seq: number; ts: number } & Event;

type Event =
  | SessionStarted
  | { type: "blueprint.agent"; agent: AgentBlueprint }   // upsert by agent.id
  | { type: "blueprint.graph"; graph: GraphBlueprint }   // upsert by graph.id
  | { type: "span.started"; span: Span }                 // status "running", end_ms null
  | { type: "span.ended"; span: Span }                   // the full span; replaces by span_id
  | StreamDelta;

type SessionStarted = {
  type: "session.started";
  pviz: 1;
  session_id: string;          // 32 hex characters, new for every session
  title: string;
  mode: "capture" | "blueprint"; // "blueprint": blueprints only; no run will ever appear
  include_content: boolean;
  stream: boolean;             // token streaming enabled; always false when include_content is false
  notices: string[];           // messages about the whole session, for the reader to show
  versions: { pydantic_ai_viz: string; pydantic_ai: string; python: string };
};

type StreamDelta = {
  type: "stream.delta";
  trace_id: string;
  span_id: string;             // the open model_request span this delta belongs to
  part_index: number;          // index of the response part within this model request
  part_kind: "text" | "thinking" | "tool_call";
  tool_name: string | null;    // set for tool_call parts
  delta: string;               // text to append, or a fragment of the arguments JSON for tool_call
};
```

### Ordering and identity { #ordering }

- `seq` starts at 0 and increases by 1 per event within a session. Gaps appear only after [eviction](#eviction).
- `session.started` is always `seq: 0` and is never evicted.
- For one span, `span.started` comes before `span.ended`. A parent's `span.started` comes before its children's.
- A `stream.delta` for a span lies between that span's `span.started` and `span.ended`.
- Blueprint events can arrive at any time and are upserts by `id`. In a `"capture"` session, at most one blueprint has any given non-null `name`, since runs are matched to blueprints by name; a `"blueprint"` session has no runs, and two of its blueprints may share a name.
- A reader must accept a `span.ended` with no earlier `span.started`. This happens after eviction, or when capture was attached in the middle of a run.
- A **run** is a trace: all spans that share a `trace_id`. Its root is the span with `parent_id: null`, an `agent_run` or a `graph_run`.

A session in `"blueprint"` mode is what `pydantic-ai-viz view` and a blueprint-only export produce. It holds `session.started` and blueprint events and nothing else, so a reader should not wait for runs or offer run controls. `notices` carries what concerns the session and no single blueprint, such as a file that static analysis had to skip.

## Blueprints { #blueprints }

```ts
// How a fact was obtained.
type Provenance =
  | "code"       // read from source code by static analysis; nothing was imported or run
  | "object"     // read from a live Python object (an Agent or a Graph)
  | "observed";  // seen happening in a run

// Where something is defined in analysed source code. Only static analysis sets it.
type SourceLocation = {
  file: string;                // path relative to the analysed root, with forward slashes
  line: number;                // 1-based
};

type AgentBlueprint = {
  id: string;                  // opaque, stable within a session: "a1", "a2", ...
  name: string | null;         // agent.name; null until known
  label: string;               // never empty: the name, else the variable name, else "agent-<n>"
  description: string | null;
  source: Provenance;
  location: SourceLocation | null;  // where the agent is constructed
  model: {
    id: string | null;
    name: string;
    provider: string | null;
    offline: boolean;
    expression: string | null; // source text of a model that is not a string literal; name repeats it, or is the literal model name given to a model class
    model_class: string | null; // the Pydantic AI model class, for example "OpenAIChatModel", when it is known
    env: EnvironmentValue | null; // set when the model name is read from an environment variable at run time
  } | null;
  deps_type: string | null;    // qualified type name; null for object or None
  instructions: Instruction[];
  toolsets: Toolset[];
  tools: Tool[];               // function and external tools; output tools are in output.tool_names
  native_tools: string[];      // provider-side tool kinds, for example "tool_search"
  output: {
    mode: string;              // "text" | "auto" | "tool" | "native" | "prompted" | "unknown"
    allows_text: boolean | null;
    type_name: string | null;  // for example "Resolution", "str", "A | B"
    json_schema: Json | null;
    tool_names: string[];      // for example ["final_result"]; may be filled from observed runs
    validators: string[];      // the @agent.output_validator functions, in registration order
    location: SourceLocation | null;  // where the output type is defined
  };
  capabilities: Capability[];
  settings: {
    model_settings: { [k: string]: Json } | null;
    end_strategy: string | null;
    tool_retries: number | null;
    output_retries: number | null;
    tool_timeout: number | null;
    defaults: string[];        // of end_strategy, tool_retries, output_retries and tool_timeout: those whose value is what an Agent() has by default (live only; [] from source)
    expressions: { [k: string]: string };  // settings computed at run time: name -> source text
  };
  delegates: { tool_name: string; agent_id: string; evidence: Provenance }[];
  warnings: string[];          // human-readable notes about anything that could not be read
};

type EnvironmentValue = {
  variable: string;            // the environment variable, for example "APP_MODEL"
  default: string | null;      // the default the source gives, used only when the variable is not set; never the value
};

type CapabilityCategory =
  | "harness" | "environment" | "tools" | "integration" | "web" | "reasoning"
  | "context" | "knowledge" | "safety" | "self_extension" | "runtime" | "loop"
  | "custom";

type Capability = {
  name: string;                // class name; for one static analysis cannot name, the source text that builds it
  builtin: boolean;            // added by Pydantic AI to every agent
  category: CapabilityCategory;
  package: "pydantic_ai" | "pydantic_ai_harness" | null;  // where the class is defined, when it is one of these
  deferred: boolean;           // defer_loading=True: loaded when the model asks for it
  details: { [k: string]: Json };       // what it was given, by parameter name; secrets and defaults left out
  expressions: { [k: string]: string }; // parameters the source computes at run time: name -> source text (static analysis only)
  toolsets: string[];          // Toolset.id of the toolsets it gives the agent
  native_tools: string[];      // provider-side tool kinds it gives the agent, for example "web_search"
  capability_id: string | null; // the id Pydantic AI knows it by (what load_capability takes); from source only a literal id=
  stack: string | null;        // the harness stack ("Coder", "Researcher") that put it on the agent; live only, null from source
  skills: SkillInfo[];         // the skills of a harness Skills capability, in catalog order; [] for every other capability
  hooks: HookInfo[];           // the functions a Hooks capability registers; [] for every other capability
};

type HookInfo = {
  point: string;               // the capability method it stands for, as Hooks keys it: "before_model_request", "wrap_run", "on_run_error", ...
  function: string | null;     // module.qualname of the function; null when the source does not name one
  tools: string[] | null;      // a tool hook's tools= filter, sorted; null when it runs for every tool
  timeout: number | null;
  location: SourceLocation | null;  // static analysis only
};

type SkillInfo = {
  name: string;                // the name of its frontmatter, or its directory's name: the id load_capability takes
  description: string;
  when_to_use: string | null;
  model_invocable: boolean;    // false with `disable-model-invocation: true`: left out of the model's catalog
  path: string;                // its SKILL.md, forward slashes, relative to the workspace it was read from (live) or the analysed root (source); never absolute
  body: string;                // the Markdown after the frontmatter, as written (rule 5 applies)
};

type Instruction = {
  kind: "instructions" | "system_prompt";
  dynamic: boolean;
  text: string | null;         // the literal text; for a dynamic template, the template in str.format syntax
  function: string | null;     // qualified function name for dynamic ones
  expression: string | null;   // source text of any other value computed at run time, as written (may span lines)
  location: SourceLocation | null;
};

type Toolset = {
  id: string;                  // unique within the blueprint: "ts0", "ts1", ...
  toolset_id: string | null;   // Pydantic AI's toolset id: "<agent>", a user-supplied id, or null
  label: string;               // "Agent tools", "FunctionToolset 'kb'", ...
  kind: "agent" | "function" | "mcp" | "dynamic" | "other";
  wrappers: string[];          // class names of wrapper toolsets around it, outermost first
  opaque: boolean;             // true when its tools cannot be listed without running it
  expression: string | null;   // source text of a toolset that static analysis cannot read
  location: SourceLocation | null;
};

type Tool = {
  name: string;
  toolset: string;             // a Toolset.id
  description: string | null;
  parameters_schema: Json;
  return_schema: Json | null;
  kind: "function" | "output" | "external" | "unapproved";
  takes_ctx: boolean | null;
  is_async: boolean | null;
  max_retries: number | null;  // null means "inherit the agent's default"
  function: string | null;     // qualified function name
  origin: Provenance;
  location: SourceLocation | null;
  library_version: string | null;  // a tool Pydantic AI defines, read from the installed Pydantic AI source: that version
};

type GraphBlueprint = {
  id: string;                  // "g1", "g2", ...
  name: string | null;
  label: string;
  source: "code" | "object";
  location: SourceLocation | null;
  state_type: string | null;
  input_type: string | null;
  output_type: string | null;
  nodes: {
    id: string;
    kind: string;
    label: string | null;
    note: string | null;
    location: SourceLocation | null;
  }[];
  edges: { source: string; target: string; label: string | null }[];
  mermaid: string | null;      // a Mermaid state diagram of the nodes and edges
  warnings: string[];
};
```

### Capabilities { #capabilities }

A capability's `category` is the group it is listed in by the [Pydantic AI capabilities docs](https://pydantic.dev/docs/ai/capabilities/overview/). The ids, in the docs' order:

| `category` | Group in the docs |
|---|---|
| `"harness"` | Harnesses |
| `"environment"` | Execution environments |
| `"tools"` | Tools & native abilities |
| `"integration"` | Integrations |
| `"web"` | Web & research |
| `"reasoning"` | Reasoning, planning & delegation |
| `"context"` | Context management |
| `"knowledge"` | Knowledge & memory |
| `"safety"` | Control & safety |
| `"self_extension"` | Self-extension |
| `"runtime"` | Execution runtime |
| `"loop"` | Loop customization |
| `"custom"` | Anything else: the project's own, another package's, or one this release does not know |

- **Matching** is by package (`pydantic_ai` or `pydantic_ai_harness`) together with the class name. The harness is never imported, so a project class that happens to be called `Memory` or `Shell` is not taken for the harness one. A subclass takes the category of its Pydantic AI or harness base. A class of either package that is not listed is `"custom"`.
- **`package`** names the package of the class itself, so an unlisted harness class is `"custom"` with package `"pydantic_ai_harness"`, and a project subclass of `MCP` has the category of `MCP` and no package.
- **`details`** holds values only: strings, numbers, booleans, lists of those, or objects of those. A live capability of Pydantic AI or the harness lists no value the class fills in itself, such as the `denied_commands` of a `Shell()`; a parameter the class requires is always listed. A plain `Capability(instructions=...)` shows them as a string, or a list when it has several, with a function by its qualified name. A workspace given to a capability is shown as its class name, never a path. strings, numbers, booleans, lists of those, or objects of those. A parameter left at its default, a `null` or empty value, `id` and `defer_loading` are left out. A parameter whose name contains `secret`, `passw`, `credential`, `cookie` or `bearer`, ends in `token`, `auth`, `authorization`, `header`, `headers` or `apikey`, ends in `key` as a word (`api_key`), or has `env`, `header`, `headers` or `authorization` as a word, is never written; `max_tokens`, `authority` and `environment` are. A positional argument of a class whose parameter names are not known is not written either, and is named in `warnings`. A URL is written without user info, query and fragment.
- **`expressions`** is filled by static analysis only: an argument that is not a literal, as source text on one line cut at 200 characters.
- **`toolsets`** holds ids of the blueprint's own `toolsets`. **`native_tools`** holds kinds.
- **`deferred`** is `true` for `defer_loading=True`. From source, only a literal `True` counts; any other value is in `expressions`.
- **The two ways of reading differ.**
    - A live agent shows a harness stack such as `Coder` or `Researcher` as one capability of category `"harness"` (with no details or native tools; its toolsets are those of its helpers, such as the empty `researcher_instructions` one) next to the capabilities it is made of, because Pydantic AI merges a combined capability into the agent's own and the stack is found by a marker it leaves behind. The stack's helper capabilities are left out: its private classes, and the plain `Capability` that holds a `Researcher`'s instructions. A `Researcher` with an overflow store of its own leaves no marker, so it is not listed.
    - Source code shows `CombinedCapability([...])` as the capabilities in it, to a depth of four, except one given a literal `defer_loading=True`, which stays one deferred capability, as on a live agent. One given a computed `defer_loading=` stays one capability too, not deferred, with the expression in `expressions`: the agent keeps it whole when the value is true. A capability kept whole lists what it holds by class name in `details.capabilities`, as a live agent does, never as source text, and has the toolsets and native tools of what it holds. A list the source computes stays one capability, with a warning.
    - From source, `WebSearch` and `WebFetch` given `local=` give the toolset of that function or, for `local=True`, of the tool of the installed Pydantic AI. A harness capability that gives a toolset (`FileSystem`, `Shell`, `Memory`, `Planning`, `ToolOutputLimits`, `PydanticAIDocs`, `PruneToolResults`, `CapabilityCreation`) gives one `opaque` toolset with no tools, since the tools depend on its arguments; a live agent lists them. So do `SubAgents` given `agents=` or `agent_folders=` (toolset id `sub_agents`, or its `id=`), `AskUser` (`ask_user`, or its `id=`), `Advisor` (`advisor`, with a `PreparedToolset` wrapper) and `Skills` given a literal `read_files=True` (`skills`). A `FileSystem` given a literal `read_only=True` has the `FilteredToolset` wrapper a live one has. `X.from_spec(...)` reads as `X(...)`.
    - A `Hooks` shows no details: its functions are in `hooks`.
    - The arguments of a harness capability given by position are named from the harness's own signatures when the class is known to come from `pydantic_ai_harness`: `Skills(directories)`, `SubAgents(agents, models, agent_folders)`, `Advisor(model)`, `CapabilityCreation(directory, guidance)`, `RepoContext(workspace_dir, home_dir, filenames)`, `Peers(network)` and `Memory(store, store_resolver, agent_name, namespace)`. A value that cannot be shown, such as a store object, is still left out.
    - From source, two capabilities of one class that Pydantic AI would merge into one are shown as one: those with the same literal `id=`, or, with no `id=`, a Pydantic AI class whose installed source declares a literal default `id` (`WebSearch()` twice). They are merged by Pydantic AI's rules: a value only one states survives, lists and dicts are joined with the later winning, any other value is the later one, and the capability keeps the position of its last occurrence. A harness class with no literal `id=` is not merged, because its default id is only in harness source, which is never opened.
    - `WebSearch` given `allowed_domains`, `blocked_domains` or `max_uses`, or `WebFetch` given `max_uses`, with a value other than a literal `None`, can only run as the provider's own tool, so it gives no local toolset, as on a live agent.
- **`capability_id`** is the id Pydantic AI knows the capability by: the `id` of a live capability when it is a string, a literal `id=` from source. A call of `load_capability` names a capability or a skill by it.
- **`stack`** is `"Coder"` or `"Researcher"` for a live capability that stack put on the agent, and `null` otherwise and always from source (a stack is not opened there). After the stack's marker, each public capability whose class is one the stack is known to add (`Coder`: `FileSystem`, `Shell`, `RepoContext`, `SubAgents`, `ClearToolResults`, `RepairToolArguments`; `Researcher`: `WebSearch`, `WebFetch`, `SubAgents`, `ToolOutputLimits`) is a member, each class at most once and in that order; the first one that is not ends the stack, so a `Thinking()` listed after `Researcher()` is the author's, and so is a second `Shell` after `Coder()` or a `SubAgents` that Pydantic AI merged with the stack's own and moved to the author's place.
- **`skills`** is the catalog of a harness `Skills` capability: its name, `when_to_use`, whether the model may load it, its `SKILL.md` and its body. The body is authored text, like a static instruction, and is not redacted when `include_content` is `false`. It does not include the skill directory the harness adds when it renders a skill. Every path is relative; a warning never holds an absolute path.
    - **Live:** the catalog is read with `Skills.load()`, which reads the files and runs a scanner configured on `Skills`, but starts no model and writes nothing. Only host workspaces are read (`LocalWorkspaceBackend`, `WindowsWorkspaceBackend`): the one given to `Skills` as `workspace=`, or else that of the agent's own `LocalWorkspace` or `WindowsWorkspace`. A sandbox or other backend is not read, and a `Skills` with no workspace known before the run is not read either; each is named in `warnings`.
    - **From source:** `SKILL.md` files are read under the `directories` of a literal `Skills(...)` (a string or a list of strings; the first argument or `directories=`), with a literal `include=` and `exclude=` applied. A relative directory is resolved against the root of a literal `workspace=LocalWorkspaceBackend('dir')` or `WindowsWorkspaceBackend('dir')`, or of the agent's `LocalWorkspace` or `WindowsWorkspace` given a literal `working_dir`, itself relative to the folder analysed. When the workspace is decided when the code runs, the folder of the source file is tried first and then the analysed folder, with one warning: "`Skills` reads `_skills` from a workspace decided when the code runs; the skills shown are those in `_skills`". Only files inside the analysed folder are read, at most 64 skills per capability, and the size limit of one file applies. Computed `directories` stay in `expressions`, with a warning.
    - **Skipped skills** are warnings: "skill `<path>` was left out: <reason>" (its `SKILL.md` could not be read, another skill has its name, the library path is not a library, or an artifact scan flagged it); a library that is not found gives "skill library `<dir>` was not found in the analysed files, so its skills are not shown", and a `SKILL.md` over the analysis's file size limit gives "skill `<path>` is not shown: its SKILL.md is larger than the <n> byte limit of the analysis; a run still offers it". A frontmatter that YAML would not read (an unquoted `: ` in a value, a value that opens a flow collection) is one that could not be read, as for the harness; a ` #` comment after a value is not part of it. A literal `scanner=` gives "skills are shown unscanned; a run scans them first".
- **`hooks`** lists the functions a `Hooks` capability registers, grouped by hook point in the order `Hooks` registered the first of each, and in registration order within a point, which is the order it runs them in. A live one is read from its registry. From source, the keyword arguments of `Hooks(...)` come first, in the order `Hooks` registers them, then each `@hooks.on.<point>` or `@hooks.on.<point>(tools=[...], timeout=...)` decorator in the module that builds it, in source order. The keyword or decorator name is mapped to the point (`run` is `wrap_run`, `run_error` is `on_run_error`, `deferred_tool_calls` is `handle_deferred_tool_calls`). A function the source does not name (a lambda) has `function: null`, and a computed `tools=` or `timeout=` is `null`, each with a warning.
- **Delegates of `SubAgents`.** A harness `SubAgents` capability gives a delegate for each `SubAgent` it lists whose agent can be found: its `tool_name` (`delegate_task` by default) as the edge's `tool_name`, with evidence `"object"` for a live agent and `"code"` from source. Since its toolset is opaque and lists no tool, the edge starts at the agent's card. `include_self` draws nothing. Its `details` show each entry as `{agent, name, description, models}`, live and from source; from source only when every entry is a `SubAgent(...)` whose agent is found and whose arguments are literals, otherwise `agents` stays in `expressions`. An `Advisor` consults its model through an inner agent, so a run shows a delegation from the `advisor` tool to an observed `advisor` agent. Before a run, `Advisor` and `Peers` have no edge: one holds a model, the other resolves its network when it runs.
- **What is not read.** The per-run content of other capabilities: the `AGENTS.md` and `CLAUDE.md` that `RepoContext` reads (it also reads the home directory), the provider-side tools of `MCP(native=True)`, which only the provider knows, and a store `Memory` takes from `store_resolver`, which needs a run.
- **Native tools.** An agent's `native_tools` holds kinds only, as a capability's does; a native tool of a capability loaded on demand is listed by its kind too.

### Provenance, locations and expressions { #provenance }

- **Provenance** says how a fact was obtained. A blueprint's `source`, a tool's `origin` and a delegation's `evidence` all use the same three values. A blueprint made by `pydantic-ai-viz view` is `"code"`; one made from an agent passed to `serve`, `record` or `blueprint` is `"object"`; one synthesised from runs is `"observed"`. A graph is never `"observed"`.
- **`location`** is set only for provenance `"code"`. It is `null` everywhere else, and also for a `"code"` fact that has no single place in the source.
- **An expression is source text, never a value.** Static analysis does not run code, so a value that code computes is reported as the text that computes it:
    - `model.expression` is set when the model is not a string literal, for example `get_model()`. `name` holds the same text and `id` is `null`. `provider` and `offline` are filled in only for Pydantic AI's own `TestModel` and `FunctionModel`.
    - `model.model_class` names the Pydantic AI model class when the source builds one (`OpenAIChatModel(...)`), directly or through a function of the project. From a live object it is the object's class; for a model given as a string it is `null`.
    - `model.env` is set when the model, or the model name given to its class, is read from an environment variable with `os.getenv` or `os.environ`. `variable` is its name and `default` the default the source gives (`null` when there is none). The default is **not** the model: `name` and `expression` still hold the source text, and the variable decides when the code runs.
    - An `Instruction` with `dynamic: true` has exactly one of `function` (a named function), `text` (a template) or `expression` (anything else). A template is an f-string or a `str.format` call, written in the syntax of `str.format`: a part filled in at run time is a placeholder in braces (`{today}`, `{user.name!r}`), a literal brace is doubled (`{{`), and a part whose value is a literal of the source is filled in.
    - `tool.library_version` is set on a tool that Pydantic AI itself defines, such as `duckduckgo_search_tool()`: its name, description and parameters were read from the source of the installed Pydantic AI of that version, which can differ from the one the project uses. It is `null` on every other tool, and always from a live object.
    - `settings.expressions` maps a setting to its source text: a field name such as `tool_retries` or `model_settings`, or `model_settings.<key>` for one model setting. The setting itself is `null` or absent.
    - A toolset with an `expression` is `opaque`; its `label` is the class name when that can be read, else the same text.
    - An instruction's `expression` is the source exactly as written, line breaks and indentation included, cut at 20,000 characters. Every other expression is put on one line and cut at 200 characters.
    - Whatever produced the value (an environment variable, a configuration file, a function of the project, of the standard library or of another package), the expression is reported the same way. No default that such a call is given is taken for the value; an environment variable's default is reported as `env.default` only.
- **What static analysis does not know is left out, not guessed.** A `"code"` blueprint has `null` for a setting the source does not set (the library default applies), an empty `output.tool_names`, and no built-in capabilities. A model, an instruction, a toolset, a tool, an output type or a capability that could not be resolved is also named in `warnings`; a computed setting is reported in `settings.expressions` only. A capability's native tools are filled in from source only for `WebSearch`, `WebFetch`, `XSearch`, `ImageGeneration`, `ToolSearch`, `MCP(native=True)` and `NativeTool(...)`.

- Known graph node kinds are `"start"`, `"end"`, `"step"`, `"decision"`, `"join"`, `"broadcast"` and `"map"`. A reader draws an unknown kind as a step.
- `model.offline` is `true` when the provider is `test` or `function`.
- `source`, `origin` and `evidence` are explained in [Blueprint vs run](../concepts/blueprint-vs-run.md).

## Spans { #spans }

```ts
type SpanBase = {
  trace_id: string;
  span_id: string;
  parent_id: string | null;
  name: string;                // display name, see "Normalisation"
  start_ms: number;
  end_ms: number | null;       // null while running
  status: "running" | "ok" | "error";
  agent_name: string | null;   // owning agent; null for graph spans
  run_id: string | null;       // Pydantic AI's run id of the owning agent run
  error: { type: string; message: string | null; stacktrace: string | null } | null;
  attributes: { [k: string]: Json };  // leftover raw OpenTelemetry attributes; {} on span.started
  events: RunEvent[];          // events recorded by CaptureEvents in this span; [] on span.started
};

// A CapabilityEvent or CustomEvent of the run, recorded by pydantic_ai_viz.CaptureEvents.
type RunEvent = {
  kind: string;                // "planning.plan_updated"; a custom event's name
  event_kind: "capability" | "custom";
  capability_id: string | null; // the id of the capability that emitted it
  tool_call_id: string | null;
  tool_name: string | null;
  ts_ms: number;
  data: Json | null;           // the event's own fields; null without content capture
};

type Span = SpanBase & (
  | { kind: "agent_run";     data: AgentRunData }
  | { kind: "model_request"; data: ModelRequestData }
  | { kind: "tool_call";     data: ToolCallData }
  | { kind: "graph_run";     data: GraphRunData }
  | { kind: "graph_node";    data: GraphNodeData }
  | { kind: "other";         data: null }
);

type Usage = { input_tokens: number; output_tokens: number };

type AgentRunData = {
  model_name: string | null;
  conversation_id: string | null;
  instructions: string | null;       // resolved instructions; end only
  output: Json | null;               // final result; end only
  usage: Usage | null;               // this agent's own requests only; end only
  messages: Message[] | null;        // the full conversation; end only
  deferred: DeferredCall[];          // tool calls the run ended waiting on; [] otherwise, and at start
};

// A tool call left for the caller: it needs approval, or it runs outside the agent.
type DeferredCall = { call_id: string | null; tool_name: string; kind: "approval" | "external" };

type ModelRequestData = {
  model_name: string;
  provider: string | null;           // end only
  response_model: string | null;     // end only
  settings: { [k: string]: Json };   // request settings such as temperature; {} at start
  instructions: string | null;       // end only
  input_messages: Message[] | null;  // end only
  output_messages: Message[] | null; // end only
  usage: Usage | null;               // end only
};

type ToolCallData = {
  tool_name: string;
  call_id: string | null;
  arguments: Json | null;            // available at start
  result: Json | null;               // end only
  retry: { cause: string; attempt: number; max_retries: number } | null;
  deferral: "approval" | "external" | null; // the tool raised ApprovalRequired / CallDeferred; end only
};

type GraphRunData  = { graph_id: string; graph_name: string; inputs: Json | null; output: Json | null };
type GraphNodeData = { graph_id: string; node_id: string; inputs: Json | null };

type Message = { role: "system" | "user" | "assistant" | "tool"; parts: Part[] };
type Part =
  | { type: "text";        content: string | null }
  | { type: "thinking";    content: string | null }
  | { type: "tool_call";   id: string | null; name: string; arguments: Json | null }
  | { type: "tool_result"; id: string | null; name: string; result: Json | null }
  | { type: "other";       raw: Json };
```

"End only" fields are `null` in `span.started` and filled in `span.ended`.

### Reading spans correctly { #reading-spans }

- **Retry or error.** A `tool_call` with `status: "error"` and a non-null `retry` is a retry: the tool asked the model to try again. Only an error with `retry: null` is a failure.
- **Token totals.** A run's total is the sum of `usage` over the `model_request` spans of the trace. Do not add `agent_run` usages together; see [why](../concepts/how-capture-works.md#why-token-totals-come-from-model-requests).
- **Waiting for the caller.** An `agent_run` with a non-empty `deferred` ended without running those tool calls: its output was `DeferredToolRequests`. A call to a tool that needs approval (`requires_approval=True`) has no span. A tool that raised `ApprovalRequired` or `CallDeferred` has an `ok` span with `deferral` set and no result. The run that resumes it (with `message_history` and `deferred_tool_results`) has the same `conversation_id`: an approved call runs there as a `tool_call` with the same `call_id`, and a denied call or a result the caller supplied is only a `tool_result` part in its first model request's input.
- **Output tools.** A call to an output tool such as `final_result` has no span. It appears in `messages` only. An `agent_run` that ends with `status: "ok"` produced its output.
- **Events.** `events` is only filled when the agent has the `CaptureEvents` capability: Pydantic AI does not record capability or custom events in its telemetry. An event is on the span that was current when it was dispatched: the agent run, or the tool span for an `immediate` event emitted in a tool. An event emitted in a tool at its stream position is dispatched after the tool returns, so it is on the agent run, with the tool's `tool_call_id`. Events reach a reader with `span.ended` of their span.
- **Delegation.** An `agent_run` whose parent is a `tool_call` is a delegated run, started by that tool.
- **Graph nesting.** An `agent_run` started inside a graph step has that `graph_node` as its parent, except in a parallel batch, where its parent is the `graph_run`.

## Normalisation { #normalisation }

Spans are produced by Pydantic AI as OpenTelemetry spans and converted to the shapes above in Python. Readers never need the raw attributes; they are documented here so the conversion can be checked.

**Kind and name**

| Condition on the raw span | `kind` | `name` |
|---|---|---|
| attribute `pydantic_ai_viz.kind` is `graph_run` or `graph_node` | the same | graph name or node id |
| `gen_ai.operation.name` is `invoke_agent` | `agent_run` | the agent name |
| `gen_ai.operation.name` is `chat` | `model_request` | the span name without its `chat ` prefix |
| `gen_ai.operation.name` is `execute_tool` | `tool_call` | `gen_ai.tool.name` |
| anything else | `other` | the raw span name |

**Fields**

| Wire field | Raw attribute |
|---|---|
| `agent_name` | `gen_ai.agent.name`, else `agent_name` |
| `run_id` | `gen_ai.agent.call.id` |
| `AgentRunData.model_name` | `model_name` |
| `AgentRunData.conversation_id` | `gen_ai.conversation.id` |
| `AgentRunData.output` | `final_result` |
| `AgentRunData.usage` | `gen_ai.aggregated_usage.input_tokens`, `.output_tokens` |
| `AgentRunData.messages` | `pydantic_ai.all_messages` |
| `AgentRunData.deferred` | the `calls` (`external`) and `approvals` (`approval`) of a `DeferredToolRequests` in `final_result`, names and ids only. Without content capture there is no `final_result`: the tool calls of the last model response that no tool result answers, `external` when their span raised `CallDeferred` or their tool is external, else `approval` |
| `instructions` (agent run and model request) | `gen_ai.system_instructions`, a JSON list whose `content` values are joined with a blank line |
| `ModelRequestData.model_name` | `gen_ai.request.model`, else the span name |
| `ModelRequestData.provider` | `gen_ai.provider.name`, else `gen_ai.system` |
| `ModelRequestData.response_model` | `gen_ai.response.model` |
| `ModelRequestData.settings` | every `gen_ai.request.*` attribute except `model`, keyed by its suffix |
| `ModelRequestData.input_messages`, `output_messages` | `gen_ai.input.messages`, `gen_ai.output.messages` |
| `ModelRequestData.usage` | `gen_ai.usage.input_tokens`, `.output_tokens` |
| `ToolCallData.call_id`, `arguments`, `result` | `gen_ai.tool.call.id`, `.arguments`, `.result` |
| `ToolCallData.retry` | `pydantic_ai.tool.retry.cause`, `.attempt`, `.max_retries` (or `pydantic_ai.output.retry.*`, on the span of a text output function) |
| `ToolCallData.deferral` | `pydantic_ai.tool.deferral.name`: `ApprovalRequired` gives `approval`, `CallDeferred` gives `external` |
| `events` | the span's `pydantic_ai_viz.event` events, added by `CaptureEvents`: their `kind`, `event_kind`, `capability_id`, `tool_call_id`, `tool_name` and `data` (a JSON string) attributes, and their timestamp |
| `error` | the span's `exception` event (`exception.type`, `.message`, `.stacktrace`) |
| `GraphRunData`, `GraphNodeData` | `pydantic_ai_viz.graph.id`, `.name`, `.node_id`, `.inputs`, `.output` |

**Other rules**

- **Status.** OpenTelemetry `UNSET` and `OK` become `"ok"`, `ERROR` becomes `"error"`, and a span that has not ended is `"running"`. An `ERROR` status without an exception event gives `error: {type: "Error", message: <status description>, stacktrace: null}`.
- **JSON inside strings.** The final result, tool arguments and tool results arrive as strings. They are parsed as JSON; if that fails, the string is kept as it is.
- **Messages.**
    - `tool_call_response` parts become `tool_result`.
    - `reasoning` and `thinking` parts both become `thinking`.
    - A part of an unknown type becomes `{type: "other", raw}`.
    - A message whose parts are all `tool_result` gets the role `"tool"`, whichever role the instrumentation version gave it.
    - Tool-call `arguments` that arrive as a JSON string (streamed requests) are parsed.
- **`attributes`** holds every raw attribute that was not turned into a field of that span.
    - An attribute is consumed only where its field exists. `gen_ai.conversation.id`, for example, becomes `conversation_id` on an agent run and is left in `attributes` on model requests and tool calls.
    - `gen_ai.operation.name` decides the kind and is kept. So is `gen_ai.agent.description`, which goes to the blueprint, not to a span field.
    - `model_request_parameters`, `gen_ai.tool.definitions` and `logfire.json_schema` are left out: they are large and already represented in the blueprint.

## Recording { #recording }

A recording is a UTF-8 text file with the extension `.pviz.jsonl`. Each line is one envelope as compact JSON. The first line is the `session.started` event. The file is exactly the session's event log, in order.

```text
{"seq":0,"ts":1795046400000.0,"type":"session.started","pviz":1,"session_id":"5f0c2a9e6f3b4d0c9a7e1b2c3d4e5f60","title":"Support triage","mode":"capture","include_content":true,"stream":true,"notices":[],"versions":{"pydantic_ai_viz":"0.1.0","pydantic_ai":"2.70.0","python":"3.12.9"}}
{"seq":1,"ts":1795046400001.2,"type":"blueprint.agent","agent":{"id":"a1","name":"triage","label":"triage", ...}}
{"seq":2,"ts":1795046401000.0,"type":"span.started","span":{"trace_id":"0af7651916cd43dd8448eb211c80319c","span_id":"b7ad6b7169203331","parent_id":null,"kind":"agent_run","name":"triage","start_ms":1795046401000.0,"end_ms":null,"status":"running", ...}}
```

(The second and third lines are shortened here with `...`; real lines are complete objects.)

## Example events { #example }

A tool call that asks for a retry. At start, the arguments are known:

```json
{
  "seq": 8, "ts": 1795046401805.0, "type": "span.started",
  "span": {
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "span_id": "53995c3f42cd8ad8",
    "parent_id": "b7ad6b7169203331",
    "kind": "tool_call", "name": "get_order",
    "start_ms": 1795046401805.0, "end_ms": null, "status": "running",
    "agent_name": "triage", "run_id": "01a1ec72-21e1-753d-baa9-89ecf9dfa6b4",
    "error": null, "attributes": {}, "events": [],
    "data": {
      "tool_name": "get_order", "call_id": "call_1",
      "arguments": {"order_id": "1042"}, "result": null, "retry": null, "deferral": null
    }
  }
}
```

At end, the same span carries its result, the error and the retry information:

```json
{
  "seq": 9, "ts": 1795046402100.0, "type": "span.ended",
  "span": {
    "trace_id": "0af7651916cd43dd8448eb211c80319c",
    "span_id": "53995c3f42cd8ad8",
    "parent_id": "b7ad6b7169203331",
    "kind": "tool_call", "name": "get_order",
    "start_ms": 1795046401805.0, "end_ms": 1795046402100.0, "status": "error",
    "agent_name": "triage", "run_id": "01a1ec72-21e1-753d-baa9-89ecf9dfa6b4",
    "error": {
      "type": "pydantic_ai.exceptions.ToolRetryError",
      "message": "Order ids look like 'A-1042'.",
      "stacktrace": "Traceback ..."
    },
    "attributes": {"gen_ai.operation.name": "execute_tool", "logfire.msg": "running tool: get_order"},
    "events": [
      {
        "kind": "orders.lookup", "event_kind": "capability", "capability_id": "orders",
        "tool_call_id": "call_1", "tool_name": "get_order", "ts_ms": 1795046401950.0, "data": {"found": true}
      }
    ],
    "data": {
      "tool_name": "get_order", "call_id": "call_1",
      "arguments": {"order_id": "1042"},
      "result": "Order ids look like 'A-1042'.",
      "retry": {"cause": "model_retry", "attempt": 1, "max_retries": 2},
      "deferral": null
    }
  }
}
```

A streamed fragment of a tool call's arguments:

```json
{
  "seq": 6, "ts": 1795046401390.0, "type": "stream.delta",
  "trace_id": "0af7651916cd43dd8448eb211c80319c", "span_id": "00f067aa0ba902b7",
  "part_index": 1, "part_kind": "tool_call", "tool_name": "get_order",
  "delta": "{\"order_id\": "
}
```

With `include_content: false`, the events have the same shape, with `content`, `arguments`, `result`, `output` and `instructions` set to `null`.

## Live transport { #transport }

`serve` runs an HTTP server on `127.0.0.1`. All paths are relative, so the viewer also works behind a path prefix.

| Method and path | Response |
|---|---|
| `GET /` | `200 text/html`: the viewer |
| `GET /api/v1/health` | `200 {"ok": true, "pviz": 1, "version": "0.1.0", "session_id": "..."}` |
| `GET /api/v1/events` | `200 text/event-stream`: the event log, see below |
| `GET /api/v1/export.jsonl` | `200 application/x-ndjson`: the recording, as an attachment named `<title>.pviz.jsonl` |
| `GET /api/v1/export.html` | `200 text/html`: a single-file export of the current log, as an attachment |
| `POST /api/v1/rerun` | Demo mode only. `202 {"accepted": true}`, or `409 {"accepted": false, "reason": "running"}`. `404` outside demo mode. |
| anything else | `404 {"error": "not found"}` |

Every response carries `Cache-Control: no-store`. No CORS headers are sent. A request whose `Host` header is not a loopback name gets `403`, unless the server was started on a non-loopback host. `POST` also requires the `Origin` header to be absent or the server's own. A request body over 64 KiB gets `413`, and a `Content-Length` that is not a plain number gets `400`.

### Event stream { #sse }

```text
retry: 1000

event: backlog
data: {"last_seq":41}

id: 5f0c2d9e8a7b4c1d9e3f2a1b0c4d5e6f:0
data: {"seq":0,"ts":1795046400000.0,"type":"session.started",...}

id: 5f0c2d9e8a7b4c1d9e3f2a1b0c4d5e6f:1
data: {"seq":1,...}

: ping
```

- One Server-Sent Event per envelope, with the default event name. `id` is `<session_id>:<seq>`.
- Before any envelope, one event named `backlog`, without an `id`: `last_seq` is the `seq` of the newest event in the log when the connection was opened (`-1` for none). Envelopes up to it are history the client was not there for; the ones after it happen while it watches. The viewer follows a new run only when it is news in this sense, or still going, so a run that starts and ends between two of its frames is followed too. A reader that wants only the envelopes skips events with a name.
- On a connection without `Last-Event-ID`, the server sends every retained event in order and then follows the log. There is no separate snapshot endpoint.
- With `Last-Event-ID: <session_id>:n`, the server sends retained events with `seq > n`. If the session id is not the server's own, the server has restarted since the client last heard from it, and it sends everything from the start. It does the same for an id it cannot parse and for an `n` beyond its highest `seq`.
- A `: ping` comment is sent after 15 seconds without an event.
- **Reader rule:** on a `session.started` whose `session_id` differs from the current one, discard all state and start over.

### Eviction { #eviction }

When the log exceeds `max_events` (50,000 by default), the oldest completed traces are dropped, each as a whole. `session.started` and blueprint events are never dropped. Neither is a trace with a span still open, nor the trace that completed last, so a log can hold more than `max_events` events. Evicted events leave gaps in `seq`.

## HTML export { #html-export }

An export is the viewer page with the event log embedded. The viewer contains one placeholder element:

```html
<script id="pviz-boot" type="application/json">"__PVIZ_BOOT__"</script>
```

To make an export, the quoted token is replaced by a JSON object:

```ts
type Boot =
  | { mode: "live"; events_url: "api/v1/events"; export_html_url: string; export_jsonl_url: string; rerun_url: string | null }
  | { mode: "embedded"; events: Envelope[] };
```

In the embedded JSON, `<`, `>` and `&` are written as the JSON escapes `\u003c`, `\u003e` and `\u0026`, and U+2028 and U+2029 are escaped, so captured content cannot end the script element.

When the placeholder has not been replaced, the viewer runs in **static mode** and looks for data in this order:

1. `?live` in the URL: connect to `api/v1/events` (used for frontend development).
2. `?src=<URL on the same origin>`: fetch that recording.
3. `recordings/index.json` next to the page: show a gallery of the recordings it lists.
4. Otherwise: show a drop zone that accepts a `.pviz.jsonl` file.

```ts
// recordings/index.json
type RecordingIndex = {
  recordings: {
    id: string;
    title: string;
    description: string;
    src: string;
    kind?: "run" | "blueprint"; // "blueprint": a recording without runs, opened rather than played
  }[];
};
```

## Compatibility of the format { #versioning }

The wire format is versioned separately from the package. Within `pviz: 1`:

- fields and event types may be **added**; readers ignore what they do not know,
- nothing is removed or renamed, and no field changes its meaning.

A change that breaks those rules gets a new `pviz` number and is called out in the [changelog](../changelog.md).

The capability fields `capability_id`, `stack` and `skills` (with the `SkillInfo` type) and `settings.defaults` were added within `pviz` 1. The viewer opens a recording without them and fills `null`, `null`, `[]` and `[]`. The Python models and the JSON Schema require them.

Recordings made by 0.1.0 have capabilities with only `name` and `builtin`. The viewer opens them, treating the missing fields as category `"custom"` and empty. The Python models and the JSON Schema describe what this release writes, so they require the new fields and do not accept those capabilities.
