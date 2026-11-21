/**
 * The pviz wire contract, version 1.
 *
 * Hand-written from the Python models in `src/pydantic_ai_viz/_models.py`, which are the source
 * of truth. `schema/pviz.schema.json` is generated from those models and the fixtures typed
 * against this file are validated against it in `fixtures.test.ts`.
 *
 * Rules worth remembering when reading these types:
 * - every listed field is always present; unknown values are `null`, never omitted;
 * - consumers ignore unknown fields and unknown event `type` values;
 * - all times are epoch milliseconds on one clock (`ts`, `start_ms`, `end_ms`);
 * - the frontend never interprets raw OTel `attributes`, it only displays them.
 */

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };

// ---------- envelope ----------

export type Envelope = { seq: number; ts: number } & Event;

export type Event =
  | SessionStarted
  | AgentBlueprintEvent
  | GraphBlueprintEvent
  | SpanStarted
  | SpanEnded
  | StreamDelta;

export type SessionStarted = {
  type: "session.started";
  pviz: 1;
  /** 32 hex characters, new per session. */
  session_id: string;
  title: string;
  /** "blueprint": the session holds blueprints only and no run will ever appear in it. */
  mode: SessionMode;
  include_content: boolean;
  /** Token streaming enabled. */
  stream: boolean;
  /** Messages about the session as a whole, such as files static analysis had to skip. */
  notices: string[];
  versions: { pydantic_ai_viz: string; pydantic_ai: string; python: string };
};

export type SessionMode = "capture" | "blueprint";

/** Upsert by `agent.id`. */
export type AgentBlueprintEvent = { type: "blueprint.agent"; agent: AgentBlueprint };

/** Upsert by `graph.id`. */
export type GraphBlueprintEvent = { type: "blueprint.graph"; graph: GraphBlueprint };

/** The span has status "running" and `end_ms: null`. */
export type SpanStarted = { type: "span.started"; span: Span };

/** The full span; replaces any earlier version with the same `span_id`. */
export type SpanEnded = { type: "span.ended"; span: Span };

export type StreamDelta = {
  type: "stream.delta";
  trace_id: string;
  /** The open model_request span this delta belongs to (resolved in Python). */
  span_id: string;
  /** Index of the response part within this model request. */
  part_index: number;
  part_kind: "text" | "thinking" | "tool_call";
  /** Set for tool_call parts. */
  tool_name: string | null;
  /** Text to append (text/thinking) or a raw args-JSON fragment (tool_call). */
  delta: string;
};

// ---------- blueprints ----------

/**
 * How a fact was obtained: "code" was read from source code without importing or running it,
 * "object" was read from a live Python object, "observed" was seen happening in a run.
 */
export type Provenance = "code" | "object" | "observed";

/** Where something is defined in analysed source code. */
export type SourceLocation = {
  /** Path relative to the analysed root, with forward slashes. */
  file: string;
  /** 1-based. */
  line: number;
};

export type AgentBlueprint = {
  /** Opaque, stable within a session ("a1", "a2", ...). */
  id: string;
  /** `agent.name`; null until known. */
  name: string | null;
  /** Never empty: name, else discovered variable name, else "agent-<n>". */
  label: string;
  description: string | null;
  source: Provenance;
  /** Where the agent is constructed; set by static analysis only. */
  location: SourceLocation | null;
  model: ModelInfo | null;
  /** Qualified type name; null for object/None. */
  deps_type: string | null;
  instructions: Instruction[];
  toolsets: Toolset[];
  /** Function/external tools only; output tools are in `output.tool_names`. */
  tools: Tool[];
  /** Provider-side tool kinds, e.g. "tool_search". */
  native_tools: string[];
  output: OutputInfo;
  capabilities: Capability[];
  settings: AgentSettings;
  delegates: Delegate[];
  /** Human-readable degradations. */
  warnings: string[];
};

export type ModelInfo = {
  id: string | null;
  name: string;
  provider: string | null;
  offline: boolean;
  /**
   * Source text of the model argument when it is not a string literal. `name` then repeats it,
   * unless the source builds a Pydantic AI model class with a literal model name
   * (`OpenAIChatModel("gpt-5.2", ...)`): that name is the `name`.
   */
  expression: string | null;
  /** The Pydantic AI model class, e.g. "OpenAIChatModel", when it is known. */
  model_class: string | null;
  /**
   * Set when the model name is read from an environment variable when the code runs. `name` and
   * `expression` then hold the source text, and `model_class` the class built with that name.
   */
  env: EnvironmentValue | null;
};

/** A value the code reads from an environment variable when it runs (`os.getenv("X", default)`). */
export type EnvironmentValue = {
  variable: string;
  /** The default the source gives, used only when the variable is not set; never the value. */
  default: string | null;
};

export type OutputInfo = {
  /** "text" | "auto" | "tool" | "native" | "prompted" | "unknown" (open set). */
  mode: string;
  allows_text: boolean | null;
  /** e.g. "Resolution", "str", "A | B". */
  type_name: string | null;
  json_schema: Json | null;
  /** e.g. ["final_result"]; may be filled from observed runs. */
  tool_names: string[];
  /** Where the output type is defined. */
  location: SourceLocation | null;
};

/** The group of the Pydantic AI capabilities docs, in the docs' order; "custom" for any other. Open set: read an unknown value as "custom". */
export type CapabilityCategory =
  | "harness"
  | "environment"
  | "tools"
  | "integration"
  | "web"
  | "reasoning"
  | "context"
  | "knowledge"
  | "safety"
  | "self_extension"
  | "runtime"
  | "loop"
  | "custom";

export type Capability = {
  /** Class name; for one static analysis cannot name, the source text that builds it. */
  name: string;
  /** Added by Pydantic AI to every agent; shown folded away. */
  builtin: boolean;
  category: CapabilityCategory;
  /** Where the class is defined, when it is Pydantic AI ("Core") or the harness ("Harness"). */
  package: "pydantic_ai" | "pydantic_ai_harness" | null;
  /** defer_loading=True: loaded when the model asks for it. */
  deferred: boolean;
  /** What it was given, by parameter name; secrets and defaults are left out. */
  details: { [k: string]: Json };
  /** Parameters the source computes at run time: name -> source text (static analysis only). */
  expressions: { [k: string]: string };
  /** `Toolset.id`s of the toolsets it gives the agent. */
  toolsets: string[];
  /** Provider-side tool kinds it gives the agent, e.g. "web_search". */
  native_tools: string[];
  /**
   * The id Pydantic AI knows it by, the one `load_capability` takes for a deferred capability;
   * from source only a literal `id=`.
   */
  capability_id: string | null;
  /** The harness stack that put it on the agent ("Coder", "Researcher"); null from source. */
  stack: string | null;
  /** The skills a harness `Skills` capability offers, in catalog order; [] for every other one. */
  skills: Skill[];
};

/** One skill a harness `Skills` capability offers. */
export type Skill = {
  /** The frontmatter name, else the directory name: the id `load_capability` takes. */
  name: string;
  description: string;
  when_to_use: string | null;
  /** False with `disable-model-invocation: true`: left out of the model's catalog. */
  model_invocable: boolean;
  /**
   * Its SKILL.md, with forward slashes, relative to the workspace root it was read from (object)
   * or to the analysed root (code); never absolute.
   */
  path: string;
  /** The Markdown after the frontmatter, as written: what the model gets when it loads the skill. */
  body: string;
};

export type AgentSettings = {
  model_settings: { [k: string]: Json } | null;
  end_strategy: string | null;
  tool_retries: number | null;
  output_retries: number | null;
  tool_timeout: number | null;
  /**
   * Settings that source code computes at run time: setting name -> source text. The key is a
   * field of this object, or `model_settings.<key>` for one model setting; the field itself is null.
   */
  expressions: { [k: string]: string };
  /**
   * The settings whose value is the one Pydantic AI gives an agent that sets none (field names
   * of this object); [] from source, where a setting the code does not set is null.
   */
  defaults: string[];
};

export type Delegate = {
  tool_name: string;
  agent_id: string;
  evidence: Provenance;
};

export type Instruction = {
  kind: "instructions" | "system_prompt";
  dynamic: boolean;
  /**
   * The literal text; for a template (a dynamic f-string or `str.format` call), the template, in
   * the syntax of `str.format`: a part filled in at run time is a placeholder in braces (`{date}`),
   * and a literal brace is doubled (`{{`).
   */
  text: string | null;
  /** Qualified function name for dynamic ones. */
  function: string | null;
  /** Source text of a value that is neither a literal, an f-string nor a named function. */
  expression: string | null;
  location: SourceLocation | null;
};

export type Toolset = {
  /** Unique within the blueprint: "ts0", "ts1", ... */
  id: string;
  /** pydantic-ai toolset id ("<agent>", "extra_ts", null). */
  toolset_id: string | null;
  /** "Agent tools", "FunctionToolset 'kb'", ... */
  label: string;
  kind: "agent" | "function" | "mcp" | "dynamic" | "other";
  /** Class names of WrapperToolsets around it, outermost first. */
  wrappers: string[];
  /** True when tools cannot be enumerated statically. */
  opaque: boolean;
  /** Source text of a toolset that source code builds in a way static analysis cannot read. */
  expression: string | null;
  location: SourceLocation | null;
};

export type Tool = {
  name: string;
  /** `Toolset.id`. */
  toolset: string;
  description: string | null;
  parameters_schema: Json;
  return_schema: Json | null;
  kind: "function" | "output" | "external" | "unapproved";
  takes_ctx: boolean | null;
  is_async: boolean | null;
  /** Null = inherit the agent default. */
  max_retries: number | null;
  /** Qualified function name. */
  function: string | null;
  origin: Provenance;
  location: SourceLocation | null;
  /**
   * Set for a tool Pydantic AI itself defines (`duckduckgo_search_tool()`), read from the installed
   * Pydantic AI source by static analysis: that version. The project may use another one.
   */
  library_version: string | null;
};

export type GraphBlueprint = {
  /** "g1", "g2", ... */
  id: string;
  name: string | null;
  label: string;
  source: "code" | "object";
  location: SourceLocation | null;
  state_type: string | null;
  input_type: string | null;
  output_type: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  /** `graph.render(direction="LR")`, or the same diagram drawn from the analysed source. */
  mermaid: string | null;
  warnings: string[];
};

/** Known kinds: start, end, step, decision, join, broadcast, map. Render unknown kinds as "step". */
export type GraphNode = {
  id: string;
  kind: string;
  label: string | null;
  note: string | null;
  location: SourceLocation | null;
};

export type GraphEdge = { source: string; target: string; label: string | null };

// ---------- spans ----------

export type SpanStatus = "running" | "ok" | "error";

export type SpanError = { type: string; message: string | null; stacktrace: string | null };

export type SpanBase = {
  /** 32 lowercase hex characters. */
  trace_id: string;
  /** 16 lowercase hex characters. */
  span_id: string;
  parent_id: string | null;
  /** Display name. */
  name: string;
  start_ms: number;
  /** Null while running. */
  end_ms: number | null;
  status: SpanStatus;
  /** Owning agent (`gen_ai.agent.name`); null for graph spans. */
  agent_name: string | null;
  /** `gen_ai.agent.call.id`. */
  run_id: string | null;
  error: SpanError | null;
  /** Leftover raw OTel attributes; `{}` on span.started. */
  attributes: { [k: string]: Json };
};

export type AgentRunSpan = SpanBase & { kind: "agent_run"; data: AgentRunData };
export type ModelRequestSpan = SpanBase & { kind: "model_request"; data: ModelRequestData };
export type ToolCallSpan = SpanBase & { kind: "tool_call"; data: ToolCallData };
export type GraphRunSpan = SpanBase & { kind: "graph_run"; data: GraphRunData };
export type GraphNodeSpan = SpanBase & { kind: "graph_node"; data: GraphNodeData };
export type OtherSpan = SpanBase & { kind: "other"; data: null };

export type Span =
  | AgentRunSpan
  | ModelRequestSpan
  | ToolCallSpan
  | GraphRunSpan
  | GraphNodeSpan
  | OtherSpan;

export type SpanKind = Span["kind"];

export type Usage = { input_tokens: number; output_tokens: number };

export type AgentRunData = {
  model_name: string | null;
  conversation_id: string | null;
  /** Resolved instructions, end only. */
  instructions: string | null;
  /** Final result, end only. */
  output: Json | null;
  /** This agent's own requests only, end only. */
  usage: Usage | null;
  /** Full conversation, end only. */
  messages: Message[] | null;
};

export type ModelRequestData = {
  /** From the span name at start, `gen_ai.request.model` at end. */
  model_name: string;
  /** End only. */
  provider: string | null;
  /** End only. */
  response_model: string | null;
  /** `gen_ai.request.*` minus "model", key = suffix; `{}` at start. */
  settings: { [k: string]: Json };
  /** End only. */
  instructions: string | null;
  /** End only. */
  input_messages: Message[] | null;
  /** End only. */
  output_messages: Message[] | null;
  /** End only. */
  usage: Usage | null;
};

export type ToolRetry = { cause: string; attempt: number; max_retries: number };

export type ToolCallData = {
  tool_name: string;
  call_id: string | null;
  /** Available at start. */
  arguments: Json | null;
  /** End only. */
  result: Json | null;
  retry: ToolRetry | null;
};

export type GraphRunData = {
  graph_id: string;
  graph_name: string;
  inputs: Json | null;
  output: Json | null;
};

export type GraphNodeData = { graph_id: string; node_id: string; inputs: Json | null };

export type Message = { role: "system" | "user" | "assistant" | "tool"; parts: Part[] };

export type Part =
  | { type: "text"; content: string | null }
  | { type: "thinking"; content: string | null }
  | { type: "tool_call"; id: string | null; name: string; arguments: Json | null }
  | { type: "tool_result"; id: string | null; name: string; result: Json | null }
  | { type: "other"; raw: Json };

// ---------- boot and static mode ----------

/** What Python writes into the `pviz-boot` slot. An untouched slot parses to a string. */
export type Boot =
  | {
      mode: "live";
      events_url: string;
      export_html_url: string;
      export_jsonl_url: string;
      rerun_url: string | null;
    }
  | { mode: "embedded"; events: Envelope[] };

/** `recordings/index.json`, served next to the viewer by the hosted demo. */
export type RecordingIndex = { recordings: RecordingEntry[] };

export type RecordingEntry = {
  id: string;
  title: string;
  description: string;
  src: string;
  /** `"blueprint"` for a recording with no runs, such as what `view` read; a run otherwise. */
  kind?: "run" | "blueprint";
};

/** The wire format version this viewer understands. */
export const PVIZ_VERSION = 1;
